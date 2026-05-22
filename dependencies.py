from config.database import get_database, MAIN_DOCUMENT_ID
from repositories.projects_repository import ProjectsRepository
from repositories.results_repository import ResultsRepository
from services.projects_service import ProjectsService
from services.inference_service import InferenceService
from services.agent_service import AgentService
from services.admin_service import AdminService
from services.pipeline_service import PipelineService

# 프로젝트 레포지토리
def get_projects_repository():
    db = get_database()
    return ProjectsRepository(db, MAIN_DOCUMENT_ID)

# 프로젝트 서비스
def get_projects_service():
    projects_repo = get_projects_repository()
    return ProjectsService(projects_repo)

# 결과 레포지토리
def get_inference_repository():
    db = get_database()
    return ResultsRepository(db)

# 인퍼런스 서비스 (projects_repo와 results_repo 둘 다 주입)
def get_inference_service():
    inference_repo = get_inference_repository()
    projects_repo  = get_projects_repository()
    return InferenceService(inference_repo, projects_repo)

# AI Agent 서비스
def get_agent_service():
    return AgentService()

# 관리자 서비스 (AgentService 주입)
def get_admin_service():
    projects_repo = get_projects_repository()
    agent_service = get_agent_service()
    return AdminService(projects_repo, agent_service)

# 파이프라인 서비스
def get_pipeline_service():
    projects_repo  = get_projects_repository()
    inference_repo = get_inference_repository()
    return PipelineService(projects_repo, inference_repo)
