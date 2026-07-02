import logging
from typing import Dict, Any

from config.settings import MODEL_ROOT as _DEFAULT_MODEL_ROOT

import os, json
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import base64
from io import BytesIO
from PIL import Image
from services.inference_client import (
    InferenceServerClient,
    InferenceServerError,
    InferenceServerTimeout,
)

logger = logging.getLogger("maple.inference")


class InferenceService:
    def __init__(self, results_repo, projects_repo):
        self.results_repo = results_repo
        self.projects_repo = projects_repo
        self.inference_client = InferenceServerClient()

    # ────────────────────────────────────────────────
    # 내부 유틸
    # ────────────────────────────────────────────────

    def _to_container_path(self, host_path: Path) -> str:
        """
        호스트 절대경로 → 컨테이너 마운트 경로로 변환.
        docker-compose: ./data:/data 볼륨 마운트 기준.
        DATA_ROOT 환경변수가 없으면 호스트 경로 그대로 반환 (로컬 직접 실행 시).
        """
        abs_path = str(host_path.resolve()).replace("\\", "/")

        # ./data 기준으로 /data 경로 구성
        try:
            rel = host_path.resolve().relative_to(Path("./data").resolve())
            return "/data/" + str(rel).replace("\\", "/")
        except ValueError:
            return abs_path

    def _numpy_to_base64(self, img_array: np.ndarray) -> str:
        if img_array.ndim == 2:
            img = Image.fromarray(img_array.astype("uint8"), mode="L")
        else:
            img = Image.fromarray(img_array.astype("uint8"), mode="RGB")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode()

    # ────────────────────────────────────────────────
    # 컨테이너 HTTP 추론
    # ────────────────────────────────────────────────

    def _model_path_from_model_info(self, model_info: dict) -> str | None:
        model_path_dict = model_info.get("model_path", {})
        model_root = os.getenv("MODEL_ROOT") or _DEFAULT_MODEL_ROOT
        if model_path_dict and model_root:
            rel_path = list(model_path_dict.values())[0]
            clean = rel_path.replace("\\", "/").lstrip("/")
            if clean.startswith("AI_Models/"):
                clean = clean[len("AI_Models/"):]
            return f"{model_root.rstrip('/')}/{clean}"
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

    def _container_url_from_model_info(self, model_info: dict) -> str | None:
        """
        DB의 docker 필드에서 컨테이너 URL을 조회.
        docker.service_url 이 없으면 환경변수에서 fallback.
        """
        docker_info = model_info.get("docker", {})
        if docker_info.get("service_url"):
            return docker_info["service_url"]

        # 환경변수 fallback (docker-compose / K8s 모두 동일하게 동작)
        image = docker_info.get("image", "")
        env_map = {
            "maple/si-joint-detector": "SI_JOINT_URL",
            "maple/bme-classifier":    "BME_URL",
            "maple/parkinson-gait":    "PARKINSON_URL",
            "maple/nnunet-smwi":       "NNUNET_SMWI_URL",
        }
        for key, env_var in env_map.items():
            if image.startswith(key):
                return os.getenv(env_var)
        return None

    # ────────────────────────────────────────────────
    # 메인 추론
    # ────────────────────────────────────────────────

    async def run_inference(
        self,
        department: str,
        project: str,
        save_input_dir: Path,
        save_output_dir: Path,
        extra: Dict[str, Any] = None,
    ) -> Dict:

        logger.info(f"추론 요청 - 진료과: {department} | 프로젝트: {project}")

        # 1. DB에서 모델 메타 정보 조회 (project = model)
        model_info = await self.projects_repo.get_model_by_project(department, project)
        if not model_info:
            logger.warning(f"모델 정보 없음 - {department}/{project}")
            return {"status": "error", "message": "모델 정보를 찾을 수 없습니다."}

        result_type  = model_info.get("result_type", "text")
        model_path = self._model_path_from_model_info(model_info)

        # 2. 업로드 파일 타입 vs required_data 검증
        required_data = model_info.get("required_data", [])
        if required_data:
            # required_data 값을 파일 카테고리로 정규화
            # "dicom", "dcm", "STIR T2", "Sacrum MRI" 등 → "dicom"
            # "csv" → "csv"
            # "png", "jpg", "image" → "image"
            # "nii", "nifti" → "nifti"
            def _to_category(val: str) -> str:
                v = val.lower().strip().strip('"[]\'')
                if v in ("dicom", "dcm", "t2", "t1", "stir t2", "fs t1",
                         "sacrum mri", "spine mri", "cervical mri", "thoracic mri",
                         "lumbar mri", "spine x-ray", "bone age x-ray", "mri", "x-ray"):
                    return "dicom"
                if v in ("csv",):
                    return "csv"
                if v in ("png", "jpg", "jpeg", "image"):
                    return "image"
                if v in ("nii", "nifti", "gz"):
                    return "nifti"
                return "dicom"  # 기본값: 영상 데이터로 간주

            required_categories = {_to_category(r) for r in required_data}

            # 업로드된 파일 확장자 → 카테고리
            saved_files_check = [p for p in save_input_dir.rglob("*") if p.is_file()]
            uploaded_categories: set[str] = set()
            for p in saved_files_check:
                ext = p.suffix.lower()
                if ext in (".dcm", ".dicom"):
                    uploaded_categories.add("dicom")
                elif ext == ".csv":
                    uploaded_categories.add("csv")
                elif ext in (".png", ".jpg", ".jpeg"):
                    uploaded_categories.add("image")
                elif ext == ".nii" or str(p).endswith(".nii.gz"):
                    uploaded_categories.add("nifti")

            if uploaded_categories and not uploaded_categories.intersection(required_categories):
                expected = ", ".join(sorted(required_categories))
                got = ", ".join(sorted(uploaded_categories))
                logger.warning(f"파일 타입 불일치 - 필요: {expected}, 업로드됨: {got}")
                return {
                    "status": "error",
                    "message": f"잘못된 파일 형식입니다. 이 모델은 [{expected}] 형식을 필요로 합니다. (업로드된 파일: {got})",
                }

        # 3. 컨테이너 URL 확인
        container_url = self._container_url_from_model_info(model_info)
        model_name = model_info.get("model_name", project)

        if not container_url:
            logger.error(f"컨테이너 URL 없음 - {model_name}")
            return {"status": "error", "message": "모델 컨테이너 URL을 찾을 수 없습니다. DB의 docker.service_url 또는 환경변수를 확인하세요."}
        logger.info(f"컨테이너 URL 확인 - {container_url}")

        # 4. 입력 데이터 준비 — 저장된 파일 경로 기준으로 처리
        input_data: Any = None

        # 저장된 파일 중 첫 번째 파일을 확장자로 판별
        saved_files_all = list(save_input_dir.rglob("*"))
        saved_files_all = [p for p in saved_files_all if p.is_file()]

        if saved_files_all:
            # 확장자별로 분류
            csv_files  = [p for p in saved_files_all if p.suffix.lower() == ".csv"]
            json_files = [p for p in saved_files_all if p.suffix.lower() == ".json"]
            img_files  = [p for p in saved_files_all if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
            dcm_files  = [p for p in saved_files_all if p.suffix.lower() in (".dcm", ".dicom")]
            nii_files  = [p for p in saved_files_all if p.suffix.lower() == ".nii" or str(p).endswith(".nii.gz")]

            if csv_files:
                df = pd.read_csv(csv_files[0])
                input_data = df.to_dict(orient="records")
            elif json_files:
                input_data = json.loads(json_files[0].read_text(encoding="utf-8"))
            elif dcm_files:
                input_data = self._to_container_path(dcm_files[0])
            elif nii_files:
                input_data = self._to_container_path(nii_files[0])
            elif img_files:
                input_data = self._to_container_path(img_files[0])
            else:
                return {"status": "error", "message": f"지원하지 않는 파일 형식: {saved_files_all[0].name}"}

        elif extra is not None:
            input_data = extra

        if input_data is None:
            return {"status": "error", "message": "입력 데이터가 없습니다."}

        params = {
            "container_url": container_url,
            "container_endpoint": "/run/v2",
            "model_name": project,
        }
        if model_path:
            params["model_path"] = model_path

        logger.info("Maple 추론 서버 요청 중... (%s/%s)", department, project)
        try:
            infer_result = await self.inference_client.infer(
                model_info=model_info,
                model_id=model_info.get("model_id") or f"{department}/{project}",
                input_data=input_data,
                params=params,
            )
            container_result = self._to_container_result(infer_result)
            logger.info(f"Maple 추론 완료 - result_type: {container_result.get('result_type')}")
        except InferenceServerTimeout as e:
            logger.error(f"추론 서버 타임아웃: {e}")
            return {"status": "error", "message": str(e)}
        except InferenceServerError as e:
            logger.error(f"추론 서버 호출 실패: {e}")
            return {"status": "error", "message": str(e)}

        if container_result.get("status") != "ok":
            logger.error(f"컨테이너 추론 오류: {container_result.get('detail')}")
            return {"status": "error", "message": container_result.get("detail", "컨테이너 추론 오류")}

        # 5. 결과 처리
        save_output_dir.mkdir(parents=True, exist_ok=True)

        # output_image_role: DB 모델 메타데이터에서 읽음
        output_image_role = model_info.get("output_image_role")

        # ── IMAGE ──
        if result_type == "image":
            # 단수(image_b64) 또는 복수(images_b64) 모두 처리
            images_b64_list = container_result.get("images_b64") or []
            if not images_b64_list:
                single = container_result.get("image_b64")
                if single:
                    images_b64_list = [single]
            if not images_b64_list:
                logger.error("이미지 결과 없음")
                return {"status": "error", "message": "이미지 결과를 받지 못했습니다."}

            # 저장
            saved_files = []
            for idx, b64 in enumerate(images_b64_list):
                img_bytes = base64.b64decode(b64)
                save_path = save_output_dir / f"result_{idx+1}.png"
                save_path.write_bytes(img_bytes)
                saved_files.append(str(save_path))
            logger.info(f"결과 이미지 {len(saved_files)}장 저장 완료 → {save_output_dir}")

            # images 필드: Agent VLM 해석용 (role + data)
            # - output_files가 있으면 파일명을 role로 사용 (Agent의 [IMG:filename] 태그와 키 일치)
            # - 없으면 output_image_role 기반으로 생성
            images_for_agent = []
            multi = len(images_b64_list) > 1
            output_files = container_result.get("output_files", [])
            for idx, b64 in enumerate(images_b64_list):
                entry: dict = {"data": f"data:image/png;base64,{b64}"}
                if output_files and idx < len(output_files):
                    entry["role"] = Path(output_files[idx]).name
                elif output_image_role:
                    entry["role"] = f"{output_image_role}_{idx + 1}" if multi else output_image_role
                images_for_agent.append(entry)

            # model_output: 컨테이너가 반환한 구조화 결과 (predictions 외 부가 정보)
            model_output = {
                k: v for k, v in container_result.items()
                if k not in ("status", "result_type", "image_b64", "images_b64", "predictions")
            }

            await self.results_repo.save_result({
                "department_name": department,
                "project_name":    project,
                "model_name":      model_name,
                "timestamp":       datetime.now(timezone.utc),
                "result_type":     "image",
                "saved_files":     saved_files,
            })

            return {
                "status":       "success",
                "result_type":  "image",
                # 프론트엔드용: data URI 리스트
                "images":       [f"data:image/png;base64,{b}" for b in images_b64_list],
                "saved_files":  saved_files,
                "predictions":  container_result.get("predictions"),
                "model_output": model_output,
                # Agent interpret용: role이 붙은 이미지 목록
                "images_for_agent": images_for_agent,
            }

        # ── TEXT / CLASSIFICATION ──
        predictions = container_result.get("predictions", [])
        summaries = [
            f"[Sample {i}] Pred = {p['pred']} ({p['pred_name']})"
            for i, p in enumerate(predictions)
        ] if predictions else []

        txt_path = save_output_dir / "result.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(summaries) if summaries else str(container_result))
        logger.info(f"결과 텍스트 저장 완료 → {txt_path}")

        model_output = {
            k: v for k, v in container_result.items()
            if k not in ("status", "result_type", "predictions")
        }

        await self.results_repo.save_result({
            "department_name": department,
            "project_name":    project,
            "model_name":      model_name,
            "timestamp":       datetime.now(timezone.utc),
            "result_type":     "text",
            "saved_txt":       str(txt_path),
        })

        return {
            "status":       "success",
            "result_type":  "text",
            "summary":      summaries if summaries else None,
            "txt_file":     str(txt_path),
            "predictions":  predictions,
            "model_output": model_output,
        }

    # ────────────────────────────────────────────────
    # general 모드 파일 변환
    # ────────────────────────────────────────────────

    async def build_attachments(self, files: list) -> list[dict]:
        """
        general 모드 전용 — 업로드 파일들을 attachments[] 계약으로 정규화.

        각 파일을 핸들러 레지스트리로 {type, images, text, metadata, tabular}
        형태로 변환하고, 요청 단위 이미지 토큰 예산을 적용한다.
        파일은 디스크에 저장하지 않고 메모리에서 처리한다.

        Returns:
            attachments: NormalizedAttachment.to_dict() 리스트
        """
        from services.attachments import apply_image_budget, normalize_attachment
        from config.settings import AGENT_MAX_IMAGES

        normalized = []
        for file in files:
            content = await file.read()
            na = await normalize_attachment(
                file.filename or "",
                content,
                getattr(file, "content_type", "") or "",
            )
            if na is not None:
                normalized.append(na)

        dropped = apply_image_budget(normalized, AGENT_MAX_IMAGES)
        logger.info(
            "attachments 구성 완료: %d건 | images=%d | dropped=%d",
            len(normalized),
            sum(len(a.images) for a in normalized),
            dropped,
        )
        return [na.to_dict() for na in normalized]

    async def extract_attachments_meta(self, save_input_dir: Path) -> list[dict]:
        """
        prediction interpret용 — 저장된 원본에서 메타데이터만 추출 (렌더 없이).

        Returns:
            [{"type": ..., "filename": ..., "metadata": {...}}, ...]
        """
        from services.attachments import extract_metadata, find_handler

        metas: list[dict] = []
        for path in sorted(p for p in save_input_dir.rglob("*") if p.is_file()):
            handler = find_handler(path.name)
            if handler is None:
                continue
            try:
                content = path.read_bytes()
            except Exception as e:
                logger.warning("원본 읽기 실패 (%s): %s", path, e)
                continue
            md = await extract_metadata(path.name, content)
            if md:
                metas.append({
                    "type":     getattr(handler, "type", "unknown"),
                    "filename": path.name,
                    "metadata": md,
                })
        logger.info("attachments_meta 추출: %d건", len(metas))
        return metas
