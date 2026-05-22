import os
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# ── 로깅 설정 ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mars")


async def log_http_service_status(
    service_name: str,
    base_url: str,
    health_path: str = "/health",
    timeout: float = 3.0,
) -> None:
    """Log whether an external HTTP service is reachable without blocking startup."""
    base_url = base_url.rstrip("/")
    health_url = f"{base_url}{health_path}"
    logger.info(f"{service_name} 연결 확인 중... ({base_url})")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(health_url)
            response.raise_for_status()
        logger.info(f"{service_name} 연결 완료 ({health_url})")
    except Exception as exc:
        logger.warning(f"{service_name} 연결 확인 실패 ({health_url}): {exc}")


# ── Lifespan ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    mongo_uri = os.getenv("MONGO_URI") or os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name   = os.getenv("DB_NAME",   "projects_db")
    agent_url = os.getenv("AGENT_URL", "http://localhost:8001")
    inference_url = os.getenv("MEDCENTERAI_INFERENCE_URL", "http://localhost:8010")
    inference_health_path = os.getenv("MEDCENTERAI_INFERENCE_HEALTH_PATH", "/health")

    logger.info("서버 시작 중...")
    logger.info(f"MongoDB 연결 중... ({mongo_uri})")
    mongodb_client = AsyncIOMotorClient(mongo_uri)
    app.mongodb_client = mongodb_client
    app.mongodb = mongodb_client[db_name]
    logger.info(f"MongoDB 연결 완료 (DB: {db_name})")
    logger.info(f"AI Agent URL: {agent_url}")
    await log_http_service_status(
        "추론 서버",
        inference_url,
        health_path=inference_health_path,
    )
    logger.info("MARS AI Backend 서버가 준비되었습니다.")
    yield
    logger.info("서버 종료 중...")
    mongodb_client.close()
    logger.info("MongoDB 연결 종료")


# ── FastAPI 앱 ─────────────────────────────────────
app = FastAPI(
    title="MARS AI Backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 헬스체크 ───────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "mars-ai-backend"}


# ── 라우터 등록 ────────────────────────────────────
# routes/api.py 에서 /projects, /inference, /admin, /pipeline 을 한 번에 등록
from routes.api import router as api_router
app.include_router(api_router)
logger.info("라우터 등록 완료 (/projects, /inference, /admin, /pipeline)")

# uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# http://127.0.0.1:8000/docs
