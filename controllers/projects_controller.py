from fastapi import APIRouter, Depends, HTTPException
from services.projects_service import ProjectsService
from dependencies import get_projects_service

router = APIRouter()

@router.get("/")
async def get_departments(service: ProjectsService = Depends(get_projects_service)):
    """
    부서 목록 반환
    Example API: http://127.0.0.1:8000/projects/
    """
    try:
        departments = await service.get_departments()
        print(f"    Get departments: {departments}")
        return {"departments": departments} # {"departments":["Rheumatology","Orthopedics"]}
    except Exception as e:
        print(f"Error in get_departments: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{department_name}")
async def get_projects_by_department(department_name: str, service: ProjectsService = Depends(get_projects_service)):
    """
    Retrieve project names and their unique IDs by department.
    Example API: http://127.0.0.1:8000/projects/Orthopedics/1
    """
    print(f"Server - Selected '{department_name}' department")
    projects_names, projects = await service.get_projects_by_department(department_name)
    if not projects:
        raise HTTPException(status_code=404, detail="Department or Projects not found")
    
    print(f"    Get projects by document: {projects_names}") # {"projects":["청소년기 특발성 척추측만증 분류","Bone Age 예측 모델"]}
    return {"projects": projects}


@router.get("/{department_name}/{project_number}")
async def get_models_by_projects_by_department(department_name: str, project_number: str, service: ProjectsService = Depends(get_projects_service)):
    """
    특정 프로젝트에 속한 모델 목록 조회
    Example API: http://127.0.0.1:8000/projects/Orthopedics/1/2
    """
    models_names, models = await service.get_models_by_projects_by_department(department_name, project_number)
    if not models:
        raise HTTPException(status_code=404, detail="Department not found")

    print(f"    Get models by projects by document: {models_names}") # {"models":["scoliosis basic classification model","scoliosis multimodal classification best model"]}
    if not models:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"models": models}


@router.get("/{department_name}/{project_number}/{model_number}/data")
async def get_data_by_models_by_projects_by_department(department_name: str, project_number: str, model_number: str, service: ProjectsService = Depends(get_projects_service)):
    """
    특정 모델에서 사용하는 데이터 목록 조회
    Example API: http://127.0.0.1:8000/projects/Orthopedics/1/2/data 
    """
    required_data = await service.get_data_by_models_by_projects_by_department(department_name, project_number, model_number)
    if not required_data:
        raise HTTPException(status_code=404, detail="Department not found")

    print(f"    Get data by models by projects by document: {required_data}") # {"required_data":["Spine X-ray","Bone Age"]}  
    if not required_data:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"required_data": required_data}


