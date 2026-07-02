import os
from dotenv import load_dotenv

load_dotenv()

# ── MongoDB ───────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.getenv("DB_NAME", "maple_db")

# ── AI Agent ──────────────────────────────────────────
AGENT_URL     = os.getenv("AGENT_URL", "http://localhost:8101")
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "300"))
# general 모드에서 Agent(VLM)로 보내는 요청당 최대 이미지 수 (토큰 예산).
# 초과 시 3D 첨부를 axial 단면으로 강등 → 그래도 초과면 하드 캡. (§4)
AGENT_MAX_IMAGES = int(os.getenv("AGENT_MAX_IMAGES", "8"))

# ── 추론 서버 ─────────────────────────────────────────
INFERENCE_URL         = os.getenv("MAPLE_INFERENCE_URL", "http://localhost:8110")
INFERENCE_HEALTH_PATH = os.getenv("MAPLE_INFERENCE_HEALTH_PATH", "/health")
REMOTE_INFERENCE_URL  = os.getenv("MAPLE_REMOTE_INFERENCE_URL", "")

# ── 모델 경로 ─────────────────────────────────────────
AI_MODELS_DIR = os.getenv("AI_MODELS_DIR", "../maple-model-execution-server/AI_Models")
MODEL_ROOT    = os.getenv("MODEL_ROOT", "")

# ── MongoDB 문서 ID ───────────────────────────────────
MAIN_DOCUMENT_ID = os.getenv("MAIN_DOCUMENT_ID", "")
