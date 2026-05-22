from pydantic import BaseModel
from typing import List, Dict, Any, Optional


# ── 기본 도메인 모델 ──────────────────────────────────

class AIModel(BaseModel):
    model_name: str
    model_description: str
    model_path: str
    required_data: List[str]

class Project(BaseModel):
    project_name: str
    ai_models: List[AIModel]

class Department(BaseModel):
    department_name: str
    projects: List[Project]


# ── /inference 요청 스키마 ────────────────────────────

class InferenceMode(str):
    AUTO       = "auto"
    CLINICAL   = "clinical"
    PREDICTION = "prediction"
    GENERAL    = "general"


# ── /inference 응답 스키마 ────────────────────────────

class PredictionItem(BaseModel):
    """단일 분류 결과 항목"""
    side:      Optional[str]  = None   # "left" | "right" (이미지 모델)
    pred:      Optional[int]  = None
    pred_name: Optional[str]  = None
    prob:      Optional[float]= None

class SourceItem(BaseModel):
    """RAG 검색 출처"""
    source: str
    text:   str
    score:  Optional[float] = None

class InferenceResponse(BaseModel):
    """prediction 모드 응답"""
    status:         str
    mode:           str
    result_type:    Optional[str]             = None
    images:         Optional[List[str]]       = None   # data:image/png;base64,...
    predictions:    Optional[List[Dict[str, Any]]] = None
    interpretation: Optional[str]             = None
    step_results:   Optional[List[Dict[str, Any]]] = None

class ClinicalResponse(BaseModel):
    """clinical / general 모드 응답"""
    status:  str
    mode:    str
    message: Optional[str]       = None
    sources: Optional[List[SourceItem]] = None


# ── Admin CRUD 스키마 ─────────────────────────────────

# 부서
class DepartmentCreate(BaseModel):
    department_name: str

class DepartmentUpdate(BaseModel):
    new_name: str

# 프로젝트
class ProjectCreate(BaseModel):
    project_name: str

class ProjectUpdate(BaseModel):
    new_name: str

# 모델
class ModelCreate(BaseModel):
    model_name: str
    model_description: str
    model_path: Dict[str, str]   # key: weight name, value: file path
    required_data: List[str]
    task_type: str               # classification, segmentation, detection, etc.
    result_type: str             # image | text
    inference_script: Optional[str] = None

class ModelUpdate(BaseModel):
    new_name: str
    department_name: str
    project_name: str

class ModelDelete(BaseModel):
    department_name: str
    project_name: str


# ── 기타 ─────────────────────────────────────────────

class InferenceInput(BaseModel):
    department_name: str
    project_name: str
    model_name: str
    input_image_path: str

class InferenceResult(BaseModel):
    department_name: str
    project_name: str
    model_name: str
    input_image_path: str
    result_value: Dict[str, Any]

class InferenceMetadata(BaseModel):
    department: str
    task: str
    model: str

