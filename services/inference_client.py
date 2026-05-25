from typing import Any

import httpx

from config.settings import INFERENCE_URL, REMOTE_INFERENCE_URL


class InferenceServerError(Exception):
    pass


class InferenceServerTimeout(InferenceServerError):
    pass


class InferenceServerClient:
    def __init__(self, timeout: float = 130.0):
        self.timeout = timeout

    def resolve_base_url(self, model_info: dict[str, Any]) -> str:
        server = model_info.get("inference_server") or "local"
        if isinstance(server, str) and server.startswith(("http://", "https://")):
            return server.rstrip("/")

        env_map = {
            "local": "MAPLE_INFERENCE_URL",
            "internal": "MAPLE_INFERENCE_URL",
            "0": "MAPLE_INFERENCE_URL",
            "remote": "MAPLE_REMOTE_INFERENCE_URL",
            "external": "MAPLE_REMOTE_INFERENCE_URL",
            "1": "MAPLE_REMOTE_INFERENCE_URL",
        }
        env_name = env_map.get(str(server).lower(), "MAPLE_INFERENCE_URL")
        default_url = INFERENCE_URL if env_name == "MAPLE_INFERENCE_URL" else REMOTE_INFERENCE_URL
        base_url = default_url
        if not base_url:
            raise InferenceServerError(f"추론 서버 URL을 찾을 수 없습니다. 환경변수 {env_name}를 확인하세요.")
        return base_url.rstrip("/")

    def resolve_endpoint(self, model_info: dict[str, Any]) -> str:
        endpoint = model_info.get("endpoint") or "/infer"
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return endpoint

    async def infer(
        self,
        model_info: dict[str, Any],
        model_id: str,
        input_data: Any,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_url = self.resolve_base_url(model_info)
        endpoint = self.resolve_endpoint(model_info)
        payload = {
            "model_id": model_id,
            "input_data": input_data,
            "params": params or {},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{base_url}{endpoint}", json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException as exc:
            raise InferenceServerTimeout(f"추론 서버 요청 시간이 초과되었습니다: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise InferenceServerError(f"추론 서버 응답 실패 ({exc.response.status_code}): {detail}") from exc
        except httpx.HTTPError as exc:
            raise InferenceServerError(f"추론 서버 호출 실패: {exc}") from exc
