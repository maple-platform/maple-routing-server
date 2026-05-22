from fastapi import APIRouter
from controllers.projects_controller import router as projects_router
from controllers.inference_controller import router as inference_router
from controllers.admin_controller import router as admin_router
from controllers.pipeline_controller import router as pipeline_router

router = APIRouter()
router.include_router(projects_router, prefix="/projects",  tags=["projects"])
router.include_router(inference_router, prefix="/inference", tags=["inference"])
router.include_router(admin_router,    prefix="/admin",     tags=["admin"])
router.include_router(pipeline_router, prefix="/pipeline",  tags=["pipeline"])


