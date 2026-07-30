import logging
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from config.logging_filters import SignedUrlRedactionFilter, redact_signed_url
from controllers.inference_controller import _normalize_fixture_question
from services.clinical_inference_service import (
    _artifact_role,
    _confidence,
    _execution_steps,
    _result_type_text,
    _uploaded_types,
    ClinicalInferenceService,
)
from services.clinical_chat_service import ClinicalChatService, IMAGE_TOKEN_PATTERN
from services.agent_service import canonicalize_interpretation_images
from services.risk_policy_service import evaluate_policy
from services.file_access_service import FileAccessError, FileAccessService
from services.pipeline_service import PipelineService


class AnalysisRulesTest(unittest.TestCase):
    def test_artifact_role_and_input_types(self):
        self.assertEqual(_artifact_role("gradcam_overlay"), "heat")
        self.assertEqual(_artifact_role("bbox_overlay"), "box")
        self.assertEqual(_artifact_role("segmentation_overlay"), "base")
        self.assertEqual(
            _uploaded_types([
                {"extension": "png"},
                {"extension": "dcm"},
                {"extension": "png"},
            ]),
            ["dicom", "image"],
        )

    def test_confidence_extraction_does_not_assign_risk(self):
        self.assertEqual(
            _confidence({"predictions": [{"label": "x", "score": 0.87}]}),
            0.87,
        )
        self.assertIsNone(_confidence({"label": "x"}))

    def test_general_agent_plan_uses_model_name_and_all_dag_steps(self):
        payload = {
            "status": "ready",
            "query_type": "general",
            "mode": "general",
            "execution_plan": {
                "steps": [
                    {
                        "step_id": "s1",
                        "model_name": "YOLO26x_RSNA_Pneumonia",
                        "project": "RSNA_Pneumonia_YOLO26x",
                        "department": "Pulmonology",
                        "depends_on": [],
                    },
                    {
                        "step_id": "s2",
                        "model_name": "ChestXray14_Multilabel_Classification",
                        "project": "ChestXray14_Multilabel_Classification",
                        "department": "Pulmonology",
                        "depends_on": [],
                    },
                ]
            },
        }
        mode, steps = _execution_steps(payload)
        self.assertEqual(mode, "general")
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["model_name"], "YOLO26x_RSNA_Pneumonia")
        self.assertEqual(steps[0]["project"], "RSNA_Pneumonia_YOLO26x")
        self.assertEqual(
            steps[1]["model_name"],
            "ChestXray14_Multilabel_Classification",
        )

    def test_prediction_top_level_contract_remains_supported(self):
        mode, steps = _execution_steps({
            "status": "ready",
            "mode": "prediction",
            "department": "Pulmonology",
            "project": "RSNA_Pneumonia_YOLO26x",
        })
        self.assertEqual(mode, "prediction")
        self.assertEqual(steps[0]["model_name"], "RSNA_Pneumonia_YOLO26x")

    def test_agent_interpret_result_type_is_always_a_string(self):
        self.assertEqual(_result_type_text(None), "unknown")
        self.assertEqual(
            _result_type_text(["classification", "bbox"]),
            "classification + bbox",
        )

    def test_chat_image_tokens_only_accept_registered_token_shape(self):
        content = (
            "소견입니다. [IMG:AN-20260729-0045:heat:0] "
            "[IMG:잘못된 토큰/경로]"
        )
        self.assertEqual(
            IMAGE_TOKEN_PATTERN.findall(content),
            ["AN-20260729-0045:heat:0"],
        )
        registry = {
            "AN-1:base:0": {
                "analysis_id": "AN-1",
                "role": "base",
            },
            "AN-1:heat:0": {
                "analysis_id": "AN-1",
                "role": "heat",
            },
            "AN-1:box:0": {
                "analysis_id": "AN-1",
                "role": "box",
            },
        }
        self.assertEqual(
            ClinicalChatService._fallback_image_tokens(
                "병변 위치 이미지를 보여줘",
                registry,
            ),
            ["AN-1:base:0", "AN-1:heat:0", "AN-1:box:0"],
        )

    def test_chat_fixture_question_normalization(self):
        self.assertEqual(
            ClinicalChatService._normalize_fixture_question(
                "  현재  가장 악화된 소견은 뭐야?  "
            ),
            "현재 가장 악화된 소견은 뭐야",
        )
        self.assertEqual(
            _normalize_fixture_question("이 환자의 경과가  어때?"),
            "이 환자의 경과가 어때",
        )

    def test_legacy_interpretation_image_roles_are_canonicalized(self):
        content, images = canonicalize_interpretation_images(
            "[IMG:bbox_overlay] [IMG:gradcam_overlay]",
            {"bbox_overlay": "box-data", "gradcam_overlay": "heat-data"},
        )
        self.assertEqual(content, "[IMG:box] [IMG:heat]")
        self.assertEqual(images, {"box": "box-data", "heat": "heat-data"})
        content, images = canonicalize_interpretation_images(
            "[IMG:original] [IMG:original_2]",
            {"original": "base-0", "original_2": "base-1"},
        )
        self.assertEqual(content, "[IMG:base] [IMG:base:1]")
        self.assertEqual(images, {"base": "base-0", "base:1": "base-1"})
        content, images = canonicalize_interpretation_images(
            "[IMG:gradcam_overlay]",
            {"heat": "same-data", "gradcam_overlay": "same-data"},
        )
        self.assertEqual(content, "[IMG:heat]")
        self.assertEqual(images, {"heat": "same-data"})

    def test_risk_policy_uses_configured_labels_not_confidence_cutoffs(self):
        policy = {
            "rules": [{
                "tier": "High",
                "labels": ["Pneumonia"],
                "prediction_value": 1,
            }],
            "default_tier": "Low",
        }
        self.assertEqual(
            evaluate_policy(
                policy,
                {
                    "predictions": [{
                        "label": "Pneumonia",
                        "prob": 0.12,
                        "pred": 1,
                    }]
                },
            ),
            "High",
        )
        self.assertEqual(
            evaluate_policy(
                policy,
                {
                    "predictions": [{
                        "label": "Pneumonia",
                        "prob": 0.95,
                        "pred": 0,
                    }]
                },
            ),
            "Low",
        )

    def test_signed_url_rejects_expired_and_tampered_signature(self):
        service = object.__new__(FileAccessService)
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        service.clock = lambda: now
        expires = int((now + timedelta(minutes=5)).timestamp())
        signature = service._signature("F-test", expires)
        service.verify("F-test", expires, signature)
        with self.assertRaises(FileAccessError):
            service.verify("F-test", expires, "0" * 64)
        service.clock = lambda: now + timedelta(minutes=6)
        with self.assertRaises(FileAccessError):
            service.verify("F-test", expires, signature)

    def test_signature_is_redacted_from_logs(self):
        value = "/files/F-1?exp=123&sig=abcdef&other=1"
        self.assertEqual(
            redact_signed_url(value),
            "/files/F-1?exp=123&sig=[REDACTED]&other=1",
        )
        record = logging.LogRecord(
            "httpx", logging.INFO, __file__, 1, "GET %s", (value,), None
        )
        SignedUrlRedactionFilter().filter(record)
        self.assertNotIn("abcdef", record.getMessage())


class ClinicalInferenceExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_execute_sends_all_general_steps_to_dag(self):
        payload = {
            "status": "ready",
            "mode": "general",
            "execution_plan": {
                "steps": [
                    {
                        "step_id": "s1",
                        "model_name": "YOLO26x_RSNA_Pneumonia",
                        "project": "RSNA_Pneumonia_YOLO26x",
                        "department": "Pulmonology",
                    },
                    {
                        "step_id": "s2",
                        "model_name": "ChestXray14_Multilabel_Classification",
                        "project": "ChestXray14_Multilabel_Classification",
                        "department": "Pulmonology",
                    },
                ]
            },
        }

        class FakeAgent:
            async def plan(self, **_kwargs):
                return payload

            async def interpret(self, **kwargs):
                self.interpret_steps = kwargs["step_results"]
                self.interpret_task = kwargs["task"]
                return {
                    "finding": "우하엽 음영",
                    "interpretation": "두 모델 종합 판독",
                    "recommendation": "임상 소견과 비교",
                }

        class FakeInference:
            async def build_attachments_from_dir(self, _directory):
                return []

        class FakePipeline:
            async def run_dag(self, *, steps, save_input_dir, save_output_dir):
                self.steps = steps
                self.input_dir = save_input_dir
                self.output_dir = save_output_dir
                return {
                    "status": "success",
                    "step_results": [
                        {
                            "step_id": "s1",
                            "model": "RSNA_Pneumonia_YOLO26x",
                            "result_type": "bbox_overlay",
                            "predictions": {"score": 0.91},
                            "model_output": {},
                            "images": [],
                        },
                        {
                            "step_id": "s2",
                            "model": "ChestXray14_Multilabel_Classification",
                            "result_type": "classification_probabilities",
                            "predictions": {"score": 0.82},
                            "model_output": {},
                            "images": [],
                        },
                    ],
                    "errors": [],
                }

        class FakeMedicalFiles:
            async def store_derived(self, **_kwargs):
                return {"base": [], "heat": [], "box": []}

        service = object.__new__(ClinicalInferenceService)
        service.agent = FakeAgent()
        service.inference = FakeInference()
        service.pipeline = FakePipeline()
        service.medical_files = FakeMedicalFiles()
        class FakeRiskPolicy:
            async def assess(self, _steps, _results):
                return "High", "assessed"
        service.risk_policy = FakeRiskPolicy()

        async def download_inputs(_analysis, input_dir: Path):
            (input_dir / "scan.dcm").write_bytes(b"dicom")
            return [{"extension": "dcm"}]

        service._download_inputs = download_inputs
        result = await service.execute({
            "analysis_id": "AN-test",
            "query": "폐렴 진단",
        })

        self.assertEqual(len(service.pipeline.steps), 2)
        self.assertEqual(
            service.pipeline.steps[0]["model_name"],
            "YOLO26x_RSNA_Pneumonia",
        )
        self.assertEqual(
            result["model_name"],
            (
                "YOLO26x_RSNA_Pneumonia · "
                "ChestXray14_Multilabel_Classification"
            ),
        )
        self.assertEqual(result["confidence"], 0.91)
        self.assertEqual(result["risk_tier"], "High")
        self.assertEqual(result["risk_status"], "assessed")
        self.assertEqual(result["finding"], "우하엽 음영")
        self.assertEqual(result["interpretation"], "두 모델 종합 판독")
        self.assertEqual(result["recommendation"], "임상 소견과 비교")
        self.assertEqual(
            service.agent.interpret_task,
            {
                "department": "Pulmonology",
                "project": (
                    "RSNA_Pneumonia_YOLO26x · "
                    "ChestXray14_Multilabel_Classification"
                ),
            },
        )
        self.assertTrue(all(
            isinstance(item["result_type"], str)
            for item in service.agent.interpret_steps
        ))
        self.assertEqual(result["normalized_result"]["mode"], "general")
        self.assertIn(
            "/data/clinical-work/",
            PipelineService(None, None)._to_container_path(
                service.pipeline.input_dir / "scan.dcm"
            ),
        )
        self.assertNotIn("/tmp/", str(service.pipeline.input_dir))
        self.assertFalse(service.pipeline.input_dir.exists())

    def test_pipeline_rejects_path_outside_shared_data_volume(self):
        with self.assertRaises(ValueError):
            PipelineService(None, None)._to_container_path(
                Path("/tmp/not-shared/scan.dcm")
            )

    async def test_clinical_dag_uses_infer_v2_without_database_container_url(self):
        class FakeProjects:
            async def get_model_by_project(self, department, project):
                return {
                    "model_id": f"{department}/{project}",
                    "project_name": project,
                    "inference_server": "local",
                    "docker": {"service_url": "관리자 작성 예정"},
                    "result_type": "classification",
                }

        class FakeResults:
            async def save_result(self, _result):
                return None

        class FakeInferenceClient:
            async def infer(self, **_kwargs):
                raise AssertionError("임상 DAG가 레거시 /infer를 호출했습니다.")

            async def infer_v2(self, **kwargs):
                self.request = kwargs
                return {
                    "status": "ok",
                    "result": {
                        "result_type": "classification",
                        "predictions": [{"label": "fracture", "score": 0.8}],
                        "data": {"roi": [1, 2, 3, 4]},
                    },
                    "output_images": [],
                    "model_output": {},
                    "metadata": {
                        "model_name": "FracAtlas_Fracture_Fusion",
                        "runtime": "runtime-medical",
                    },
                }

        pipeline = PipelineService(FakeProjects(), FakeResults())
        pipeline.inference_client = FakeInferenceClient()

        with tempfile.TemporaryDirectory(dir="data") as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "scan.png").write_bytes(b"image")
            result = await pipeline.run_dag(
                steps=[{
                    "step_id": "s1",
                    "model_name": "FracAtlas_Fracture_Fusion",
                    "project": "FracAtlas_Fracture_Fusion",
                    "department": "Orthopedics",
                    "required_data": ["image"],
                    "depends_on": [],
                }],
                save_input_dir=input_dir,
                save_output_dir=output_dir,
            )

        self.assertEqual(result["status"], "success")
        request = pipeline.inference_client.request
        self.assertEqual(request["model_name"], "FracAtlas_Fracture_Fusion")
        self.assertTrue(request["input_path"].startswith("/data/"))
        self.assertTrue(request["output_dir"].startswith("/data/"))
        self.assertEqual(request["params"], {})
        self.assertNotIn("container_url", request)

    async def test_clinical_dag_moves_dependency_roi_to_v2_params(self):
        class FakeProjects:
            async def get_model_by_project(self, department, project):
                return {
                    "model_id": f"{department}/{project}",
                    "inference_server": "local",
                    "result_type": "classification",
                }

        class FakeResults:
            async def save_result(self, _result):
                return None

        class FakeInferenceClient:
            def __init__(self):
                self.requests = []

            async def infer_v2(self, **kwargs):
                self.requests.append(kwargs)
                data = {"roi": [10, 20, 30, 40]} if len(self.requests) == 1 else {}
                return {
                    "status": "ok",
                    "result": {
                        "result_type": "classification",
                        "predictions": [],
                        "data": data,
                    },
                    "output_images": [],
                    "model_output": data,
                    "metadata": {},
                }

        pipeline = PipelineService(FakeProjects(), FakeResults())
        pipeline.inference_client = FakeInferenceClient()

        with tempfile.TemporaryDirectory(dir="data") as directory:
            root = Path(directory)
            input_dir = root / "input"
            input_dir.mkdir()
            (input_dir / "scan.png").write_bytes(b"image")
            result = await pipeline.run_dag(
                steps=[
                    {
                        "step_id": "s1",
                        "project": "first",
                        "department": "Orthopedics",
                        "required_data": ["image"],
                        "depends_on": [],
                    },
                    {
                        "step_id": "s2",
                        "project": "second",
                        "department": "Orthopedics",
                        "required_data": ["image"],
                        "depends_on": ["s1"],
                    },
                ],
                save_input_dir=input_dir,
                save_output_dir=root / "output",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            pipeline.inference_client.requests[1]["params"]["roi"],
            [10, 20, 30, 40],
        )


if __name__ == "__main__":
    unittest.main()
