from pydantic import BaseModel
from typing import List, Dict, Optional


# ── Admin CRUD 스키마 ─────────────────────────────────

class DepartmentCreate(BaseModel):
    department_name: str

class DepartmentUpdate(BaseModel):
    new_name: str

class ProjectCreate(BaseModel):
    project_name: str

class ProjectUpdate(BaseModel):
    new_name: str

class ModelCreate(BaseModel):
    model_name: str
    model_description: str
    model_path: Dict[str, str]
    required_data: List[str]
    task_type: str
    result_type: str
    inference_script: Optional[str] = None

class ModelUpdate(BaseModel):
    new_name: str
    department_name: str
    project_name: str

class ModelDelete(BaseModel):
    department_name: str
    project_name: str
