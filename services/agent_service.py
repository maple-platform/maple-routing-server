"""
AgentService — MARS AI Agent 서버 연동 레이어.

Back-end ↔ AI Agent (NHN Cloud B200, SSH 터널 → localhost:8001) 간
모든 HTTP 통신을 이 서비스에서 담당한다.

엔드포인트:
  POST /agent/plan          — 모드별 쿼리 라우팅 및 실행 계획 수립
  POST /agent/interpret     — 추론 결과 임상 해석
  POST /agent/models/register — 모델 Wiki + ChromaDB 등록
  DELETE /agent/models/{model_id} — 모델 Wiki + ChromaDB 삭제
"""
import os
import logging
import json
import re
from typing import Any

import httpx

logger = logging.getLogger("mars.agent")

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8001")
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "300"))


class AgentService:
    def __init__(self, agent_url: str = AGENT_URL):
        self.agent_url = agent_url.rstrip("/")

    def _summarize_interpret_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        summarized = {
            "query": payload.get("query"),
            "task": payload.get("task"),
            "execution_context": payload.get("execution_context"),
            "step_results": [],
        }

        for step in payload.get("step_results", []):
            images = step.get("images") or []
            summarized["step_results"].append({
                "step": step.get("step"),
                "model": step.get("model"),
                "result_type": step.get("result_type"),
                "predictions": step.get("predictions"),
                "model_output": step.get("model_output"),
                "images": [
                    {
                        "role": image.get("role"),
                        "data_preview": (image.get("data", "")[:48] + "...")
                        if image.get("data") else "",
                    }
                    for image in images
                ],
            })

        return summarized

    # ──────────────────────────────────────────────────
    # /agent/plan  — 모드별 쿼리 라우팅
    # ──────────────────────────────────────────────────

    async def plan(
        self,
        mode: str,
        query: str,
        department: str | None = None,
        project: str | None = None,
        uploaded_types: list[str] | None = None,
        images: list[str] | None = None,
        csv_data: list[dict] | None = None,
    ) -> dict:
        """
        Agent에 실행 계획 요청.

        Args:
            mode: "auto" | "clinical" | "prediction" | "general"
            query: 사용자 자연어 요청
            department: prediction 모드 전용
            project: prediction 모드 전용
            uploaded_types: auto 모드 — 업로드된 파일 타입 목록 (예: ["dicom"])
            images: general 모드 — base64 인코딩된 이미지 목록
            csv_data: general 모드 — CSV를 dict로 변환한 리스트

        Returns:
            Agent 응답 dict. 실패 시 {"status": "error", "message": ...}
        """
        payload: dict[str, Any] = {"mode": mode, "query": query}
        if department:
            payload["department"] = department
        if project:
            payload["project"] = project
        if uploaded_types is not None:
            payload["uploaded_types"] = uploaded_types
        if images is not None:
            payload["images"] = images
        if csv_data is not None:
            payload["csv_data"] = csv_data

        logger.info(f"[Agent] POST /agent/plan — mode={mode}")
        try:
            async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
                resp = await client.post(f"{self.agent_url}/agent/plan", json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[Agent] plan 오류 (HTTP {e.response.status_code}): {e.response.text}")
            return {"status": "error", "message": f"Agent plan 오류: {e.response.status_code}"}
        except Exception as e:
            logger.warning(f"[Agent] plan 연결 실패: {e}")
            return {"status": "error", "message": f"Agent 연결 실패: {e}"}

    # ──────────────────────────────────────────────────
    # /agent/interpret  — 추론 결과 임상 해석
    # ──────────────────────────────────────────────────

    async def interpret(
        self,
        query: str,
        step_results: list[dict],
        task: dict | None = None,
        execution_context: dict | None = None,
    ) -> dict:
        """
        추론 결과를 Agent에 전달해 임상 해석 결과를 반환받는다.

        Returns:
            {
              "interpretation": "...[IMG:role]... 텍스트",
              "images": {"role": "data:image/png;base64,..."}   # 인라인 이미지 dict
            }
            실패 시 {"interpretation": "", "images": {}}
        """
        payload: dict[str, Any] = {
            "query":        query,
            "step_results": step_results,
        }
        if task:
            payload["task"] = task
        if execution_context:
            payload["execution_context"] = execution_context

        role_summary: list[dict[str, Any]] = []
        for step in step_results:
            images = step.get("images") or []
            roles = [img.get("role") for img in images if isinstance(img, dict)]
            role_summary.append({
                "step": step.get("step"),
                "model": step.get("model"),
                "images_len": len(images),
                "roles": roles,
            })

        logger.info(
            f"[Agent] POST /agent/interpret — query='{query}' | "
            f"task={task} | steps={len(step_results)}"
        )
        logger.info(f"[Agent] interpret images summary: {role_summary}")
        logger.info(
            "[Agent] interpret payload(step_results): %s",
            json.dumps(
                self._summarize_interpret_payload(payload),
                ensure_ascii=False,
                default=str,
            ),
        )
        try:
            async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.agent_url}/agent/interpret",
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.error(f"[Agent] interpret {resp.status_code}: {resp.text}")
                    return {"interpretation": "", "images": {}}
                data = resp.json()
                interpretation = data.get("interpretation", "")
                img_tokens = re.findall(r"\[IMG:[^\]]+\]", interpretation)
                ordered_img_roles = re.findall(r"\[IMG:([^\]]+)\]", interpretation)
                image_roles_input: list[str] = []
                for step in step_results:
                    for image in step.get("images") or []:
                        role = image.get("role") if isinstance(image, dict) else None
                        if role:
                            image_roles_input.append(role)
                ordered_roles = sorted(
                    image_roles_input,
                    key=lambda role: (
                        re.sub(r"\d+$", "", role),
                        int(re.search(r"(\d+)$", role).group(1)) if re.search(r"(\d+)$", role) else 0,
                        role,
                    ),
                )
                slice_lines = [
                    line for line in interpretation.splitlines()
                    if "Original MRI" in line or "Segmentation Overlay" in line
                ]
                logger.info("[Agent] interpret final markdown:\n%s", interpretation)
                logger.info("[Agent] interpret final response_text:\n%s", interpretation)
                logger.info("[Agent] interpret img tokens ordered: %s", ordered_img_roles)
                logger.info("[Agent] interpret image_roles input: %s", image_roles_input)
                logger.info("[Agent] interpret image_roles sorted: %s", ordered_roles)
                logger.info("[Agent] interpret slice lines: %s", slice_lines)
                logger.info(
                    "[Agent] interpret response summary: img_token_count=%d | img_tokens=%s | inline_image_keys=%s",
                    len(img_tokens),
                    img_tokens,
                    list((data.get("images") or {}).keys()),
                )
                return {
                    "interpretation": interpretation,
                    "images":         data.get("images", {}),
                }
        except Exception as e:
            logger.exception(
                "[Agent] interpret 실패: %s: %r | query=%r | task=%r | steps=%d",
                type(e).__name__,
                e,
                query,
                task,
                len(step_results),
            )
            return {"interpretation": "", "images": {}}

    # ──────────────────────────────────────────────────
    # /agent/models/register  — 모델 등록
    # ──────────────────────────────────────────────────

    async def register_model(
        self,
        model_id: str,
        model_name: str,
        department: str,
        project: str,
        description: str,
        task_type: str,
        required_data: list[str],
        result_type: str,
        output_image_role: str | None = None,
    ) -> bool:
        """
        Agent의 Wiki + ChromaDB에 모델을 등록한다.

        Returns:
            성공 시 True, 실패 시 False
        """
        payload: dict[str, Any] = {
            "id": model_id,
            "model_name": model_name,
            "department": department,
            "project": project,
            "description": description,
            "task_type": task_type,
            "required_data": required_data,
            "result_type": result_type,
        }
        if output_image_role:
            payload["output_image_role"] = output_image_role
        logger.info(f"[Agent] POST /agent/models/register — id={model_id}")
        try:
            async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.agent_url}/agent/models/register", json=payload
                )
                resp.raise_for_status()
                logger.info(f"[Agent] 모델 등록 완료: {model_id}")
                return True
        except Exception as e:
            logger.warning(f"[Agent] 모델 등록 실패 (추후 수동 등록 필요): {e}")
            return False

    # ──────────────────────────────────────────────────
    # /agent/models/{model_id}  — 모델 삭제
    # ──────────────────────────────────────────────────

    async def lookup_model(self, model_name: str) -> dict:
        """
        Agent ChromaDB에서 모델명으로 department/project 조회.

        Returns:
            {"found": True, "model_name": ..., "department": ..., "project": ...}
            또는 {"found": False}
        """
        logger.info(f"[Agent] GET /agent/models/lookup — model_name={model_name}")
        try:
            async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.agent_url}/agent/models/lookup",
                    params={"model_name": model_name},
                )
                resp.raise_for_status()
                data = resp.json()

                # Agent가 found 필드 없이 model_name/department/project를 직접 반환하는 경우 정규화
                if "found" not in data:
                    has_model = bool(data.get("model_name") or data.get("department"))
                    data["found"] = has_model

                # "** " 등 접두사 제거
                for key in ("department", "project", "model_name"):
                    if isinstance(data.get(key), str):
                        data[key] = data[key].lstrip("* ").strip()

                logger.info(f"[Agent] lookup 결과: {data}")
                return data
        except Exception as e:
            logger.warning(f"[Agent] lookup_model 실패: {e}")
            return {"found": False}

    async def delete_model(self, model_id: str) -> bool:
        """
        Agent의 Wiki + ChromaDB에서 모델을 삭제한다.

        Returns:
            성공 시 True, 실패 시 False
        """
        logger.info(f"[Agent] DELETE /agent/models/{model_id}")
        try:
            async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
                resp = await client.delete(
                    f"{self.agent_url}/agent/models/{model_id}"
                )
                resp.raise_for_status()
                logger.info(f"[Agent] 모델 삭제 완료: {model_id}")
                return True
        except Exception as e:
            logger.warning(f"[Agent] 모델 삭제 실패: {e}")
            return False
