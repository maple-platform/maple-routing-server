"""
Control Node — 파이프라인 모델 순차 실행 서비스.

Agent가 생성한 Execution Plan을 받아 Step별로 모델 컨테이너를 순차 호출하고
이전 Step의 출력을 다음 Step의 입력으로 전달한다.
"""
import logging
import base64
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from services.inference_client import (
    InferenceServerClient,
    InferenceServerError,
    InferenceServerTimeout,
)

logger = logging.getLogger("mars.pipeline")


class PipelineService:
    def __init__(self, projects_repo, results_repo):
        self.projects_repo = projects_repo
        self.results_repo  = results_repo
        self.inference_client = InferenceServerClient()

    async def _get_container_url(self, department: str, project: str) -> str | None:
        """DB에서 모델의 docker.service_url 조회"""
        model_info = await self.projects_repo.get_model_by_project(department, project)
        if not model_info:
            return None
        return model_info.get("docker", {}).get("service_url")

    async def _get_model_path(self, department: str, project: str) -> str | None:
        """DB에서 모델의 checkpoint 경로 조회.
        컨테이너 환경(MODEL_ROOT=/AI_Models)이면 컨테이너 내부 경로로 변환.
        로컬 환경이면 절대경로로 변환.
        """
        import os
        model_info = await self.projects_repo.get_model_by_project(department, project)
        if not model_info:
            return None
        model_path_dict = model_info.get("model_path", {})
        if not model_path_dict:
            return None
        rel_path = list(model_path_dict.values())[0]  # 예: "AI_Models/Rheumatology/.../best.pt"

        # 컨테이너에서는 /AI_Models 볼륨 마운트, 로컬에서는 절대경로
        model_root = os.getenv("MODEL_ROOT")
        if model_root:
            # Docker/K8s: MODEL_ROOT 환경변수로 컨테이너 내부 경로 구성
            clean = rel_path.replace("\\", "/").lstrip("/")
            if clean.startswith("AI_Models/"):
                clean = clean[len("AI_Models/"):]
            return f"{model_root.rstrip('/')}/{clean}"
        else:
            # 로컬: 서버 자체 기본 경로 사용
            return None

    def _to_container_result(self, infer_result: dict) -> dict:
        result = infer_result.get("result") or {}
        output_images = infer_result.get("output_images") or []
        restored = {
            "status": "ok",
            "result_type": result.get("result_type") or infer_result.get("metadata", {}).get("result_type"),
            "predictions": result.get("predictions"),
        }
        if result.get("data") is not None:
            restored["data"] = result.get("data")
        restored.update(infer_result.get("model_output") or {})
        if len(output_images) == 1:
            restored["image_b64"] = output_images[0]
        elif len(output_images) > 1:
            restored["images_b64"] = output_images
        return restored

    async def run_pipeline(
        self,
        steps: list[dict],       # Agent Execution Plan의 steps
        image_path: str,         # 업로드된 DICOM 절대경로
        save_output_dir: Path,
    ) -> dict:
        """
        steps 예시:
        [
            { "step": 1, "model": "YOLOv12",   "department": "Rheumatology", "project": "SI Joints Detection" },
            { "step": 2, "model": "GradCAM++", "department": "Rheumatology", "project": "BME Classification" },
        ]
        """
        save_output_dir.mkdir(parents=True, exist_ok=True)

        prev_output: Any = None   # 이전 Step 출력 (다음 Step 입력으로 전달)
        step_results = []

        for step in sorted(steps, key=lambda s: s["step"]):
            step_num = step["step"]
            dept     = step["department"]
            project  = step["project"]

            logger.info(f"[Step {step_num}] 실행 시작 - {dept}/{project}")

            model_info = await self.projects_repo.get_model_by_project(dept, project)
            if not model_info:
                return {"status": "error", "message": f"[Step {step_num}] {project} 모델 정보 없음"}

            # 컨테이너 URL / 모델 경로 조회 (project 기반)
            container_url = model_info.get("docker", {}).get("service_url")
            model_path    = await self._get_model_path(dept, project)
            if not container_url:
                return {"status": "error", "message": f"[Step {step_num}] {project} 컨테이너 URL 없음"}

            # input_data 구성 — Step 1은 image_path, Step 2+는 이전 출력(ROI 좌표) 포함
            # model_path=None이면 각 서버의 기본값 사용 (로컬 개발환경)
            if step_num == 1:
                input_data = image_path
            else:
                # 이전 Step이 ROI 좌표를 반환했으면 함께 전달
                roi = prev_output.get("roi") if isinstance(prev_output, dict) else None
                if roi:
                    input_data = {
                        "image_path": prev_output.get("image_path", image_path),
                        "roi":        roi,
                    }
                else:
                    input_data = image_path
            params = {
                "container_url": container_url,
                "container_endpoint": "/run",
            }
            if model_path:
                params["model_path"] = model_path

            try:
                infer_result = await self.inference_client.infer(
                    model_info=model_info,
                    model_id=model_info.get("model_id") or f"{dept}/{project}",
                    input_data=input_data,
                    params=params,
                )
                result = self._to_container_result(infer_result)
            except InferenceServerTimeout as e:
                logger.error(f"[Step {step_num}] 추론 서버 타임아웃: {e}")
                return {"status": "error", "message": f"[Step {step_num}] {str(e)}"}
            except InferenceServerError as e:
                logger.error(f"[Step {step_num}] 추론 서버 호출 실패: {e}")
                return {"status": "error", "message": f"[Step {step_num}] {str(e)}"}

            if result.get("status") != "ok":
                return {"status": "error", "message": f"[Step {step_num}] 추론 오류: {result.get('detail')}"}

            logger.info(f"[Step {step_num}] 완료 - result_type: {result.get('result_type')}")

            # output_image_role: DB 모델 메타데이터에서 읽음
            output_image_role = (model_info or {}).get("output_image_role")

            # 이미지 결과 처리: 디스크 저장 + images 필드 구성
            images_field: list[dict] = []
            raw_b64_list: list[str]  = []

            b64_list = result.get("images_b64") or []
            if not b64_list and result.get("image_b64"):
                b64_list = [result["image_b64"]]

            for b64 in b64_list:
                img_path = save_output_dir / f"step{step_num}_{project}_result_{len(raw_b64_list)+1}.png"
                img_path.write_bytes(base64.b64decode(b64))
                logger.info(f"[Step {step_num}] 이미지 저장 → {img_path}")
                raw_b64_list.append(b64)
                img_entry: dict = {"data": f"data:image/png;base64,{b64}"}
                if output_image_role:
                    if len(b64_list) > 1:
                        img_entry["role"] = f"{output_image_role}_{len(raw_b64_list)}"
                    else:
                        img_entry["role"] = output_image_role
                images_field.append(img_entry)

            # Step 결과 기록
            step_entry: dict = {
                "step":        step_num,
                "model":       project,
                "result_type": result.get("result_type"),
                "predictions": result.get("predictions"),
                "model_output": {
                    k: v for k, v in result.items()
                    if k not in ("status", "result_type", "predictions",
                                 "image_b64", "images_b64")
                },
                # 프론트엔드용 b64 목록 (1장이면 image_b64, 여러 장이면 images_b64)
                "image_b64":   raw_b64_list[0] if len(raw_b64_list) == 1 else None,
                "images_b64":  raw_b64_list if len(raw_b64_list) > 1 else [],
            }
            if images_field:
                step_entry["images"] = images_field
            step_results.append(step_entry)

            # 다음 Step에 전달할 출력 구성
            # ROI 좌표가 있으면 image_path와 함께 넘김
            if result.get("result_type") == "roi":
                prev_output = {
                    "image_path": image_path,
                    "roi": result.get("data", {}).get("roi"),
                }
            elif result.get("result_type") == "image":
                # 이미지 모델이지만 ROI도 함께 반환하는 경우 (YOLOv12 → BME 파이프라인)
                prev_output = {
                    "image_path": image_path,
                    "roi": result.get("data", {}).get("roi") if result.get("data") else None,
                }
            else:
                prev_output = result

        # DB에 파이프라인 결과 저장
        await self.results_repo.save_result({
            "pipeline":    True,
            "steps":       [{"step": s["step"], "project": s.get("project", s.get("department"))} for s in steps],
            "timestamp":   datetime.now(timezone.utc),
            "result_type": "pipeline",
            "step_results": [
                {"step": r["step"], "model": r["model"], "result_type": r["result_type"]}
                for r in step_results
            ],
        })

        return {
            "status":       "success",
            "result_type":  "pipeline",
            "step_results": step_results,
        }
