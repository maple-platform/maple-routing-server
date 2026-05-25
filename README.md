# maple-routing-server

maple-platform의 백엔드 서버입니다.
의료 AI 추론 요청을 받아 컨테이너화된 AI 모델로 라우팅하고, 결과를 저장·반환합니다.

---

## 시스템 구성

maple-platform은 세 개의 독립적인 서버로 구성됩니다.

```
[Frontend UI]  maple-client (React, Port 3000)
      │
      │  HTTP (Agent 직접 접근 불가 — 백엔드 프록시 경유)
      ▼
[Back-end]     maple-routing-server (FastAPI, Port 8000)    ◄── 이 저장소
      │
      ├── HTTP ──► [AI 모델 컨테이너]  maple-model-execution-server (Port 9020~9023)
      │
      └── SSH터널(localhost:8001) ──► [AI Agent]  maple-agent-server (NHN Cloud B200, Port 8001)
                                                  ├── Ollama (Port 11434, gemma4:31b)
                                                  ├── ChromaDB (Port 8002, RAG)
                                                  └── /wiki (LLM Wiki, 지식 누적)
```

> **중요**: 브라우저(프론트엔드)는 SSH 터널이 열린 `localhost:8001`에 직접 접근할 수 없습니다.
> 프론트엔드의 모든 Agent 요청은 백엔드(`/inference/agent/*`)를 통해 프록시됩니다.

| 서버 | 저장소 | 포트 | 역할 |
|---|---|---|---|
| Back-end | `maple-routing-server` | 8000 | 추론 라우팅, 프로젝트 관리, 결과 저장, 파일 변환 |
| AI Agent | `maple-agent-server` | NHN Cloud B200:8001 (SSH터널 → localhost:8001) | 모드별 쿼리 라우팅, RAG 임상 해석, 모델 검색, VLM 범용 분석 |
| AI 모델 | `maple-model-execution-server` | 9020~9023 | 도메인 특화 AI 모델 실행 |
| Frontend | `maple-client` | 3000 | 사용자 인터페이스 |

---

## 기술 스택

| 분류 | 기술 | 버전 | 용도 |
|---|---|---|---|
| **웹 프레임워크** | FastAPI | 0.121.3 | API 서버 |
| | Uvicorn | 0.34.1 | ASGI 서버 |
| | Pydantic | 2.11.3 | 요청/응답 스키마 검증 |
| **데이터베이스** | MongoDB | - | 프로젝트·모델 메타데이터, 결과 이력 저장 |
| | Motor | 3.7.0 | 비동기 MongoDB 드라이버 |
| **HTTP 클라이언트** | httpx | 0.27.0 | AI 컨테이너·Agent 비동기 호출 |
| **파일 처리** | pydicom | 3.0.1 | DICOM → PNG 변환 (general 모드) |
| | nibabel | 5.3.2 | NIfTI → PNG 변환 (general 모드) |
| | Pillow | 10.4.0 | 이미지 base64 인코딩 |
| | numpy | 2.2.4 | pixel 정규화 |
| | pandas | 2.3.2 | CSV 파싱 → dict 변환 |
| **컨테이너** | Docker | - | AI 모델 격리 실행 |
| | docker-compose | - | 멀티 컨테이너 오케스트레이션 |

---

## 아키텍처

### 레이어드 아키텍처

백엔드는 **Controller → Service → Repository** 3계층 구조로 설계되어 있습니다.

```
요청
 │
 ▼
Controller   — HTTP 요청 파싱, 응답 포맷, 라우팅
 │
 ▼
Service      — 비즈니스 로직, AI 컨테이너 호출, 파이프라인 오케스트레이션, Agent 연동
 │
 ▼
Repository   — MongoDB 쿼리, 데이터 영속성
```

각 계층은 역할이 분리되어 있어, AI 모델이 추가되거나 DB 구조가 바뀌어도 다른 계층에 영향을 최소화합니다.
의존성은 `dependencies.py`에서 FastAPI `Depends()`로 주입하여 각 계층이 직접 인스턴스를 생성하지 않습니다.

### 비동기 처리

`motor` (비동기 MongoDB 드라이버)와 `httpx.AsyncClient` (비동기 HTTP)를 사용해 전 구간이 `async/await`로 처리됩니다.
AI 컨테이너 추론처럼 I/O 대기가 긴 작업에서도 서버가 블로킹되지 않습니다.

---

## 폴더 구조

```
maple-routing-server/
├── main.py                     # FastAPI 앱 진입점
├── dependencies.py             # 의존성 주입 설정
├── requirements.txt
├── Dockerfile                  # 백엔드 컨테이너 이미지
├── scan_and_register.py        # AI_Models/ 스캔 → MongoDB + ChromaDB 일괄 등록 유틸
│
├── config/
│   ├── settings.py             # 환경변수 상수 정의 (단일 진입점)
│   └── database.py             # MongoDB 클라이언트 설정
│
├── routes/
│   └── api.py                  # 라우터 등록 (prefix 매핑)
│
├── controllers/                # 요청 핸들러 (FastAPI Router)
│   ├── projects_controller.py  # GET /projects/*
│   ├── inference_controller.py # POST /inference
│   ├── pipeline_controller.py  # POST /pipeline/run
│   └── admin_controller.py     # CRUD /admin/*
│
├── services/                   # 비즈니스 로직
│   ├── projects_service.py     # 프로젝트 계층 조회
│   ├── inference_service.py    # 단일 모델 추론 + 컨테이너 호출 + 파일 변환
│   ├── pipeline_service.py     # 다단계 파이프라인 오케스트레이션
│   ├── agent_service.py        # AI Agent 연동 (plan / interpret 호출)
│   ├── admin_service.py        # 관리자 CRUD
│   └── inference_client.py     # AI 컨테이너 HTTP 클라이언트
│
├── repositories/               # 데이터 접근 레이어 (MongoDB)
│   ├── projects_repository.py  # 진료과/프로젝트/모델 CRUD
│   └── results_repository.py   # 추론 결과 저장
│
├── models/
│   └── schemas.py              # Pydantic 요청/응답 스키마
│
└── data/                       # Docker 볼륨 마운트 디렉토리 (gitignore)
    ├── input/                  # 업로드된 의료 영상
    └── output/                 # 추론 결과 이미지/텍스트
```

---

## API 엔드포인트

### Projects — 프로젝트 조회

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/projects/` | 전체 진료과 목록 |
| `GET` | `/projects/{department}` | 진료과 내 프로젝트 목록 |
| `GET` | `/projects/{department}/{project_name}` | 프로젝트 내 모델 목록 |
| `GET` | `/projects/{department}/{project_name}/{model_number}/data` | 모델의 required_data 조회 |

### Health — 헬스체크

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/health` | 백엔드 서버 상태 확인 |

```json
{"status": "ok", "service": "maple-routing-server"}
```

### Inference — 추론 실행

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/inference/` | 모드별 추론 실행 |
| `POST` | `/inference/agent/plan` | Agent plan 프록시 (프론트엔드 전용) |
| `GET` | `/inference/agent/models/lookup` | Agent 모델 조회 프록시 (프론트엔드 전용) |

> `/inference/agent/*` 엔드포인트는 브라우저가 `localhost:8001`에 직접 접근할 수 없어 백엔드가 프록시 역할을 합니다.

**Request (multipart/form-data)**

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `department` | string | 진료과명 (e.g. `Neurology`, `Radiology`). `prediction` 모드 전용 |
| `project` | string | 프로젝트명 (e.g. `BraTS2020 T1ce UNet3D`, `RSNA Pneumonia YOLO26x`). `prediction` 모드 전용 |
| `query` | string | 사용자 자연어 요청 |
| `mode` | string | `auto` \| `clinical` \| `prediction` \| `general` (기본값: `auto`) |
| `file` | File | DICOM / NIfTI / CSV 파일 (선택) |

**모드별 동작**

| mode | 사용자 명칭 | 백엔드 처리 | Agent 호출 |
|---|---|---|---|
| `auto` | - | 파일 저장 | `POST /agent/plan {mode: "auto", query, uploaded_types}` → Agent가 판단 |
| `clinical` | 임상지식모드 | - | `POST /agent/plan {mode: "clinical", query}` → RAG + LLM 즉시 답변 |
| `prediction` | 특화모델모드 | 파일 저장 → 컨테이너 실행 → 결과 저장 | `POST /agent/plan {mode: "prediction", ...}` → 실행 계획 수립 후 컨테이너 실행, `POST /agent/interpret` → 임상 해석 |
| `general` | 범용모드 | 파일 변환 (DICOM→PNG base64, CSV→dict) | `POST /agent/plan {mode: "general", images, csv_data}` → VLM 종합 분석 |

**Response**

```json
// 특화모델모드(prediction) 응답 — BraTS2020 T1ce UNet3D (뇌종양 분할)
{
  "status": "success",
  "mode": "prediction",
  "result_type": "image",
  "images": ["data:image/png;base64,..."],
  "predictions": null,
  "step_results": [],
  "interpretation": "임상 해석 텍스트 (Agent /agent/interpret 반환)\n\n[IMG:000_sample_t1ce_WT.png] ...",
  "interpretation_images": {
    "000_sample_t1ce_WT.png": "data:image/png;base64,..."
  }
}
```

> `images`: 프론트엔드에 표시할 결과 이미지 목록 (base64 data URI)
> `step_results`: 파이프라인인 경우 각 Step의 요약 결과 (이미지 제외, 예측값만 포함)
> `interpretation`: Agent가 생성한 임상 해석 텍스트. `[IMG:role]` 토큰으로 인라인 이미지 위치를 표시합니다.
> `interpretation_images`: `[IMG:role]` 토큰에 대응하는 이미지 dict. key는 role 값, value는 base64 data URI.

```json
// 임상지식모드(clinical) / 범용모드(general) 응답
{
  "status": "success",
  "mode": "clinical",
  "message": "axSpA는 축성 척추관절염으로...",
  "sources": [{"source": "pubmedqa", "text": "...", "score": 0.91}]
}
```

> `prediction` 모드에서 파이프라인이 필요한 프로젝트(e.g. BME Classification)는 `/inference`로 요청해도 내부적으로 자동 파이프라인 처리됩니다.

### Pipeline — 파이프라인 추론

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/pipeline/run` | 다단계 파이프라인 수동 실행 |

**Request (multipart/form-data)**

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `steps` | JSON string | 실행할 단계 목록 |
| `file` | File | 입력 파일 |

```json
// steps 예시 — 다단계 파이프라인 (현재 등록된 모델은 모두 standalone)
[
  {"step": 1, "department": "Neurology", "project": "BraTS2020 T1 UNet3D"},
  {"step": 2, "department": "Neurology", "project": "BraTS2020 T1ce UNet3D"}
]
```

### Admin — 관리자

> 모델 등록, 수정, 삭제는 관리자 전용 엔드포인트를 사용합니다.

**진료과 (Departments)**

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/departments` | 진료과 목록 |
| `POST` | `/admin/departments` | 진료과 생성 |
| `PUT` | `/admin/departments/{name}` | 진료과명 수정 |
| `DELETE` | `/admin/departments/{name}` | 진료과 삭제 |

**프로젝트 (Projects)**

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/projects/{department}` | 프로젝트 목록 |
| `POST` | `/admin/projects/{department}` | 프로젝트 생성 |
| `PUT` | `/admin/projects/{dept}/{old_name}` | 프로젝트명 수정 |
| `DELETE` | `/admin/projects/{dept}/{name}` | 프로젝트 삭제 |

**모델 (Models)**

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/models/{department}/{project}` | 모델 목록 |
| `POST` | `/admin/models/{department}/{project}` | 모델 등록 |
| `PUT` | `/admin/models/{model_name}` | 모델명 수정 |
| `DELETE` | `/admin/models/{model_name}` | 모델 삭제 |

**모델 등록 (`POST /admin/models/{department}/{project}`) 요청 형식: `multipart/form-data`**

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `model_name` | string (Form) | 모델명 |
| `model_description` | string (Form) | 모델 설명 |
| `required_data` | string[] (Form) | 필요 파일 타입 (예: `["dicom"]`) |
| `task_type` | string (Form) | `classification` \| `detection` \| `segmentation` 등 |
| `result_type` | string (Form) | `image` \| `text` |
| `output_image_role` | string (Form, 선택) | 출력 이미지 역할 라벨 (`bbox_overlay` \| `gradcam_overlay` \| `segmentation_overlay`) |
| checkpoint 파일들 | File (선택) | 모델 가중치 파일 (key = 파일명, e.g. `best.pt`) |
| `inference_script` | File (선택) | 추론 스크립트 업로드 |
| `requirements_file` | File (선택) | 패키지 목록 파일 업로드 |

모델 등록(`POST /admin/models`) 시 백엔드가 MongoDB 저장과 동시에 Agent의 `POST /agent/models/register`를 호출하여 Wiki 페이지 자동 생성 및 ChromaDB `maple_models` 등록을 수행합니다.

모델 삭제(`DELETE /admin/models`) 시에도 Agent의 `DELETE /agent/models/{model_id}`를 호출하여 Wiki 및 ChromaDB에서 동기 삭제합니다.

---

## 데이터베이스

플랫폼은 **MongoDB**(백엔드 관리)와 **ChromaDB**(AI Agent 관리) 두 DB를 목적에 따라 분리해 사용합니다.

| | MongoDB | ChromaDB |
|---|---|---|
| **관리 주체** | Back-end | AI Agent |
| **저장 방식** | 구조화 문서(BSON) | 벡터 임베딩 |
| **주요 용도** | 컨테이너 URL·모델 경로 조회, 추론 이력 저장 | 쿼리 기반 모델 탐색, 임상 지식 RAG 검색 |
| **접근 방법** | 백엔드가 Motor로 직접 쿼리 | 백엔드 → Agent HTTP 호출을 통해서만 접근 |

> 같은 모델이 **MongoDB에도, ChromaDB에도 등록**됩니다.
> MongoDB는 백엔드가 컨테이너를 실행할 때 쓰는 구조화 메타데이터,
> ChromaDB는 Agent가 "이 쿼리에 어떤 모델이 맞나?" 벡터 검색할 때 사용하는 임베딩 레지스트리입니다.

---

### MongoDB

**DB명:** `maple_db`

| 컬렉션 | 설명 |
|---|---|
| `departments` | 진료과 → 프로젝트 → 모델 전체 계층 구조 (단일 문서) |
| `inference_results` | 추론 실행 이력 및 결과 저장 |

#### departments 구조

```json
{
  "departments": [
    {
      "department_name": "Neurology",
      "projects": {
        "1": {
          "project_name": "BraTS2020 T1ce UNet3D",
          "model_name": "BraTS2020_T1ce_UNet3D",
          "model_path": { "best.pt": "AI_Models/Neurology/BraTS2020_T1ce_UNet3D/checkpoint/best.pt" },
          "required_data": ["nifti"],
          "task_type": "segmentation",
          "result_type": "image",
          "output_image_role": "segmentation_overlay",
          "docker": {
            "service_url": "http://localhost:9021"
          }
        }
      }
    },
    {
      "department_name": "Radiology",
      "projects": {
        "1": {
          "project_name": "RSNA Pneumonia YOLO26x",
          "model_name": "RSNA_Pneumonia_YOLO26x",
          "model_path": { "best.pt": "AI_Models/Radiology/RSNA_Pneumonia_YOLO26x/checkpoint/best.pt" },
          "required_data": ["dicom"],
          "task_type": "detection",
          "result_type": "image",
          "output_image_role": "bbox_overlay",
          "docker": {
            "service_url": "http://localhost:9022"
          }
        },
        "2": {
          "project_name": "ChestXray14 Multilabel Classification",
          "model_name": "ChestXray14_Multilabel_Classification",
          "model_path": {},
          "required_data": ["image"],
          "task_type": "classification",
          "result_type": "image",
          "output_image_role": "gradcam_overlay",
          "docker": {
            "service_url": "http://localhost:9021"
          }
        }
      }
    }
  ]
}
```

> `output_image_role`: 컨테이너가 반환하는 이미지에 붙이는 역할 라벨.
> Agent가 이미지를 해석할 때 어떤 이미지인지 구분하는 데 사용합니다.
> 가능한 값: `"bbox_overlay"` | `"gradcam_overlay"` | `"segmentation_overlay"` | `null`

---

### ChromaDB

AI Agent 서버가 관리하며, 백엔드는 직접 접근하지 않고 Agent HTTP API를 통해서만 데이터를 읽고 씁니다.

| 컬렉션 | 데이터 | 용도 |
|---|---|---|
| `maple_models` | 등록된 AI 모델의 설명·`required_data`·`output_image_role` 벡터 임베딩 | 특화모델모드에서 쿼리에 맞는 모델 탐색 |
| `pubmedqa` / `medmcqa` | 임상 논문·QA 데이터 벡터 임베딩 | 임상지식모드 RAG 검색 |
| Wiki (`/wiki`) | 누적된 임상 해석 패턴 마크다운 | 임상 해석 품질 개선 |

**모델 등록 시 ChromaDB 동기화:**
`POST /admin/models` → 백엔드가 MongoDB에 저장 후 Agent의 `POST /agent/models/register` 자동 호출 → ChromaDB `maple_models`에 벡터 등록

**모델 삭제 시 ChromaDB 동기화:**
`DELETE /admin/models/{model_name}` → 백엔드가 Agent의 `DELETE /agent/models/{model_id}` 자동 호출 → ChromaDB에서 제거

---

## AI 모델 컨테이너

AI 모델은 `maple-model-execution-server`에서 실행 환경별로 묶인 런타임 컨테이너로 관리됩니다.
내부 포트는 `8000`으로 고정이며, 외부 포트만 다릅니다.

| 컨테이너 | 외부 포트 | 담당 모델 | GPU |
|---|---|---|---|
| `maple-runtime-basic` | 9020 | 범용 경량 모델 | ✅ |
| `maple-runtime-medical` | 9021 | BraTS2020 UNet3D (T1/T1ce/T2/FLAIR), ChestXray14 (TorchXRayVision) | ✅ |
| `maple-runtime-yolo` | 9022 | RSNA_Pneumonia_YOLO26x | - |
| `maple-runtime-nnunet` | 9023 | nnUNet 계열 | ✅ |

컨테이너 실행은 `maple-model-execution-server` 디렉토리에서:

```bash
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d
```

**컨테이너 공통 계약 (POST /run/v2)**

```json
// Request — RSNA Pneumonia YOLO26x (DICOM 흉부 X-ray, 폐렴 탐지)
{
  "model_name": "RSNA_Pneumonia_YOLO26x",
  "input_data": "/data/input/Radiology/RSNA Pneumonia YOLO26x/{ts}/image.dcm",
  "model_path": "/app/AI_Models/Radiology/RSNA_Pneumonia_YOLO26x/checkpoint/best.pt"
}

// Response — RSNA Pneumonia YOLO26x
{
  "status": "ok",
  "model_name": "RSNA_Pneumonia_YOLO26x",
  "result": {
    "result_type": "image",
    "images_b64": ["..."],
    "predictions": [{"x1": 120, "y1": 80, "x2": 450, "y2": 390, "conf": 0.87, "pred": 1, "pred_name": "pneumonia_opacity"}],
    "output_files": []
  }
}

// Request — BraTS2020 T1ce UNet3D (NIfTI T1ce MRI, 뇌종양 분할)
{
  "model_name": "BraTS2020_T1ce_UNet3D",
  "input_data": "/data/input/Neurology/BraTS2020 T1ce UNet3D/{ts}/sample_t1ce.nii.gz",
  "model_path": "/app/AI_Models/Neurology/BraTS2020_T1ce_UNet3D/checkpoint/best.pt"
}

// Response — BraTS2020 T1ce UNet3D (5개 분할 이미지 반환)
{
  "status": "ok",
  "model_name": "BraTS2020_T1ce_UNet3D",
  "result": {
    "result_type": "image",
    "images_b64": ["...", "...", "...", "...", "..."],
    "predictions": null,
    "output_files": [
      "/data/output/.../000_sample_t1ce_3D.png",
      "/data/output/.../000_sample_t1ce_axial.png",
      "/data/output/.../000_sample_t1ce_WT.png",
      "/data/output/.../000_sample_t1ce_TC.png",
      "/data/output/.../000_sample_t1ce_ET.png"
    ]
  }
}
```

> 백엔드와 컨테이너 간 파일은 `./data` 디렉토리를 볼륨으로 공유합니다.
> 호스트 경로 `./data/...` → 컨테이너 경로 `/data/...`로 자동 변환됩니다.

**이미지 role 라벨 (Agent 해석용)**

컨테이너가 이미지를 반환하면, 백엔드는 이미지에 역할 라벨을 붙여 Agent에 전달합니다.

- 컨테이너가 `output_files`를 반환하는 경우 → **파일명을 role로 사용** (e.g. `000_sample_t1_WT.png`)
- `output_files`가 없는 경우 → MongoDB `output_image_role` 기반으로 생성 (e.g. `segmentation_overlay_1`)

```json
// Agent interpret에 전달되는 images 포맷 예시 — BraTS2020 T1ce UNet3D
// output_files 파일명이 role로 사용됨
[
  {"role": "000_sample_t1ce_3D.png",    "data": "data:image/png;base64,..."},
  {"role": "000_sample_t1ce_axial.png", "data": "data:image/png;base64,..."},
  {"role": "000_sample_t1ce_WT.png",    "data": "data:image/png;base64,..."},
  {"role": "000_sample_t1ce_TC.png",    "data": "data:image/png;base64,..."},
  {"role": "000_sample_t1ce_ET.png",    "data": "data:image/png;base64,..."}
]

// RSNA Pneumonia YOLO26x — output_files 없음 → output_image_role 기반
[
  {"role": "bbox_overlay", "data": "data:image/png;base64,..."}
]
```

Agent는 이 role을 임상 해석 텍스트에 `[IMG:role]` 토큰으로 삽입하고, 프론트엔드는 해당 위치에 이미지를 인라인으로 렌더링합니다.

---

## 환경 변수

`.env` 파일 또는 환경변수로 설정합니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MONGO_URL` | `mongodb://localhost:27017` | MongoDB 연결 URI |
| `DB_NAME` | `maple_db` | 데이터베이스명 |
| `MAIN_DOCUMENT_ID` | *(필수, `.env` 설정)* | MongoDB `departments` 컬렉션 문서 ObjectId |
| `AGENT_URL` | `http://localhost:8101` | AI Agent 서비스 URL (SSH 터널 경유) |
| `AGENT_TIMEOUT` | `300` | Agent 요청 타임아웃 (초) |
| `MAPLE_INFERENCE_URL` | `http://localhost:8110` | AI 추론 서버(로컬) URL |
| `MAPLE_REMOTE_INFERENCE_URL` | *(선택)* | AI 추론 서버(원격) URL |
| `AI_MODELS_DIR` | `../maple-model-execution-server/AI_Models` | AI 모델 루트 디렉토리 |

> AI 모델 컨테이너의 URL은 MongoDB `departments` 컬렉션의 `docker.service_url` 필드에서 읽습니다.

---

## 실행 방법

### 로컬 실행 (개발)

**사전 요구사항**
- Python 3.10+
- MongoDB (`localhost:27017`)
- AI Agent 서버 접속 정보 (NHN Cloud B200)

```bash
# 1. SSH 터널 연결 (Agent 서버, 별도 터미널)
ssh -L 8001:localhost:8001 maple-platform -N
# SSH config (~/.ssh/config) 에 maple-platform 등록 필요:
#   Host maple-platform
#       HostName 59.150.35.1
#       Port 34702
#       User skku_mjch
#       IdentityFile "C:\Users\...\PLFM-YS_key"

# 2. Agent 연결 확인
curl http://localhost:8001/health
# {"status":"ok","service":"maple-agent-server","ollama":"ok","chromadb":"ok"}

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 백엔드 서버 실행
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

API 문서: http://localhost:8000/docs

### AI 모델 컨테이너 실행 (Docker)

AI 모델 컨테이너는 `maple-model-execution-server`에서 관리합니다.

```bash
cd maple-model-execution-server

# 런타임 컨테이너 전체 빌드 및 실행
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d --build

# 특정 런타임만 실행
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d runtime-yolo
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d runtime-medical

# 로그 확인
docker compose logs -f runtime-yolo

# 중지
docker compose -f docker-compose.yml -f docker-compose.runtime.yml down
```

> 백엔드 서버 자체는 현재 Docker로 별도 실행하지 않고 로컬에서 uvicorn으로 실행합니다.

---

## 추론 흐름

### prediction 모드 (특화 모델 실행)

```
1. 클라이언트  →  POST /inference {mode: "prediction", department, project, file}
2. Back-end    →  POST /agent/plan {mode: "prediction", query, uploaded_types}
3. AI Agent    →  ChromaDB maple_models 검색 → required_data 매칭 → 실행 계획 반환
4. Back-end    →  실행 계획의 steps 순서대로:
                    MongoDB에서 docker.service_url 조회
                    파일을 ./data/input/{dept}/{project}/{ts}/ 에 저장
                    POST http://{service_url}/run {image_path, model_path}
5. AI 컨테이너 →  추론 실행 → {status, images_b64, predictions} 반환
6. Back-end    →  이미지 디코딩 → ./data/output/...에 저장
7. Back-end    →  POST /agent/interpret {query, step_results} (임상 해석 요청)
8. AI Agent    →  Wiki + LLM으로 임상 해석 → interpretation 반환
9. Back-end    →  결과를 inference_results 컬렉션에 저장
10. Back-end   →  클라이언트에 최종 응답 반환
```

### clinical 모드 (임상 지식 질문)

```
1. 클라이언트  →  POST /inference {mode: "clinical", query}
2. Back-end    →  POST /agent/plan {mode: "clinical", query}
3. AI Agent    →  RAG(PubMedQA·MedMCQA) + Wiki 검색 → LLM 답변 생성
4. Back-end    →  클라이언트에 응답 반환 (컨테이너 호출 없음)
```

### general 모드 (범용 AI 종합 분석)

```
1. 클라이언트  →  POST /inference {mode: "general", file, query}
2. Back-end    →  파일 타입별 변환:
                    DICOM / NIfTI  →  pydicom으로 PNG 변환 → base64 인코딩
                    CSV            →  pd.read_csv().to_dict() 파싱
3. Back-end    →  POST /agent/plan {mode: "general", query, images: [...], csv_data: [...]}
4. AI Agent    →  Gemma 4 VLM으로 이미지 + 수치 데이터 종합 분석
5. Back-end    →  클라이언트에 응답 반환 (컨테이너 호출 없음)
```

### auto 모드 (자동 판단)

```
1. 클라이언트  →  POST /inference {mode: "auto", query, file(선택)}
2. Back-end    →  POST /agent/plan {mode: "auto", query, uploaded_types}
3. AI Agent    →  LLM이 쿼리 분석 → prediction / clinical 중 판단 후 처리
4. Back-end    →  판단 결과에 따라 prediction 또는 clinical 흐름으로 이어서 처리
```

**파이프라인 자동 전환 (prediction 모드)**

`inference_controller.py`의 `PIPELINE_REQUIRED` 딕셔너리에 등록된 `(department, project)` 조합이 들어오면 `/inference`로 요청해도 내부적으로 자동으로 파이프라인으로 전환됩니다.

```python
# 현재 등록된 파이프라인 자동 전환 목록
# 현재 모든 모델이 standalone이므로 비어 있음
PIPELINE_REQUIRED = {
    # 다단계 파이프라인이 필요한 프로젝트는 여기에 등록
    # 예시:
    # ("Neurology", "Multi-Modal Segmentation"): [
    #     {"step": 1, "department": "Neurology", "project": "BraTS2020 T1 UNet3D"},
    #     {"step": 2, "department": "Neurology", "project": "BraTS2020 T1ce UNet3D"},
    # ],
}
```

새 프로젝트가 다단계 파이프라인을 필요로 하면 이 딕셔너리에 추가해야 합니다.

```
예시: Multi-Modal Brain Tumor Segmentation
Step 1  →  BraTS2020 T1 UNet3D   →  T1 분할 결과
Step 2  →  BraTS2020 T1ce UNet3D →  T1ce 분할 결과 (앙상블용)
```

---

## 모델 등록 흐름

```
1. 연구원이 inference.py, checkpoint/, meta.json, requirements.txt, sample_data/ 제출
2. 관리자가 runner.py, config.yaml 작성 (maple-model-execution-server/models/{model_name}/)
3. meta.json의 docker.service_url에 해당 런타임 컨테이너 URL 기입
   (예: "http://runtime-medical:8000" 또는 "http://runtime-yolo:8000")
4. scan_and_register.py 실행 → AI_Models/ 스캔 후 MongoDB + ChromaDB 일괄 등록
   cd maple-routing-server
   python scan_and_register.py
   └── 백엔드가 MongoDB 저장 + POST /agent/models/register 자동 호출
       └── AI Agent: Wiki 페이지 생성 + ChromaDB maple_models 등록
5. 해당 런타임 컨테이너 재시작 (config.yaml이 동적 로드되므로 재빌드 불필요)
   docker compose -f docker-compose.yml -f docker-compose.runtime.yml restart runtime-medical
```

모델 삭제 시:
```
DELETE /admin/models/{model_name}
└── 백엔드가 자동으로 DELETE /agent/models/{model_id} 호출
    └── AI Agent: Wiki 페이지 삭제 + ChromaDB에서 제거
```

---

## data/ 디렉토리

추론 실행 시 입력 파일과 결과 파일이 `data/` 디렉토리에 저장됩니다.
이 디렉토리는 Docker 볼륨으로 마운트되어 **백엔드와 AI 컨테이너가 공유**합니다.

```
data/
├── input/
│   └── {진료과}/{프로젝트}/{timestamp}/
│       └── {원본 파일명}          # 업로드된 DICOM / NIfTI / CSV
└── output/
    └── {진료과}/{프로젝트}/{timestamp}/
        ├── result_1.png           # 추론 결과 이미지
        ├── result_2.png
        └── result.txt             # 텍스트 결과 (분류 모델)
```

- 백엔드는 파일을 `./data/input/...`에 저장한 뒤, 컨테이너에는 `/data/input/...` 경로로 전달합니다.
- `general` 모드에서는 파일을 저장하지 않고 메모리에서 변환 후 Agent에 직접 전달합니다.
- 결과 이미지는 `./data/output/...`에 저장되고, 프론트엔드에는 Base64로 인코딩하여 전달합니다.
- `data/` 폴더는 `.gitignore`에 포함해야 합니다. 의료 데이터가 포함될 수 있습니다.
- 결과 파일이 누적되면 주기적으로 정리가 필요합니다.

---

## 트러블슈팅

### 백엔드 서버가 시작되지 않는다

**MongoDB 연결 실패**
```
ServerSelectionTimeoutError: localhost:27017
```
MongoDB 서비스가 실행 중인지 확인합니다.
```bash
# Windows
net start MongoDB

# 실행 상태 확인
mongosh --eval "db.runCommand({ connectionStatus: 1 })"
```

---

### 추론 요청 시 500 오류

**AI 컨테이너가 실행되지 않은 경우**

```bash
# 컨테이너 상태 확인
docker ps

# 컨테이너 재시작 (빌드 없이)
docker-compose up -d {서비스명}

# 로그 확인
docker-compose logs -f {서비스명}
```

**컨테이너가 `unhealthy` 상태인 경우**

healthcheck가 실패한 것입니다. 컨테이너 내부 로그로 원인을 파악합니다.
```bash
docker-compose logs {서비스명}
docker inspect {컨테이너명} | grep -A 10 Health
```

---

### docker-compose 명령어가 동작하지 않는다

**`no such service` 오류**

`docker-compose.yml`이 있는 디렉토리에서 실행해야 합니다.
AI 모델 컨테이너는 `maple-model-execution-server/`에 `docker-compose.runtime.yml`이 있습니다.

```bash
# 올바른 경로
cd maple-model-execution-server
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d runtime-nnunet

# ❌ maple-routing-server/ 등 다른 디렉토리에서 실행하면 오류
```

---

### AI 컨테이너 빌드 실패

**pypi 메타데이터 버그 패키지**

`nnunetv2`, `vedo`, `batchgeneratorsv2` 등 일부 패키지는 pypi에서 직접 설치 시 메타데이터 오류가 발생합니다.
해당 패키지는 Dockerfile에서 GitHub 소스로 직접 설치합니다.

```dockerfile
# ❌ pypi 직접 설치 — 메타데이터 오류
RUN pip install nnunetv2

# ✅ GitHub 소스 설치
RUN pip install "git+https://github.com/MIC-DKFZ/nnUNet.git@v2.6.3"
```

---

### 프로젝트명 매칭 실패 (`모델 정보 없음`)

DB에 등록된 project_name과 Agent/프론트엔드가 반환하는 이름이 다를 때 발생합니다.

```
모델 정보 없음 - Neurology/BraTS2020T1ceUNet3D
```

`_find_project`는 아래 변환을 자동으로 처리합니다:
- 언더스코어 ↔ 공백 (`BraTS2020_T1ce_UNet3D` ↔ `BraTS2020 T1ce UNet3D`)
- 대소문자 무시
- `ml`, `model` 접미사 무시

그래도 매칭이 안 된다면 DB에 저장된 `project_name` 값을 직접 확인합니다:
```bash
mongosh maple_db --eval "db.departments.find({}, {'departments.department_name':1, 'departments.projects':1})"
```

---

### Agent 서버 연결 오류

**ChromaDB tenant 오류**
```
ValueError: Tenant default_tenant not found
```
Agent 서버 최초 실행 시 ChromaDB를 먼저 띄운 후 `ingest_knowledge.py`를 실행하여 초기화합니다.
```bash
# ChromaDB 서버 실행
chroma run --host 0.0.0.0 --port 8002 --path ./chroma_data

# 사전 지식 ingest (최초 1회)
cd maple-agent-server
python scripts/ingest_knowledge.py
```

**Ollama 모델 미설치**
```
404 model not found
```
Ollama에 `gemma4:31b` 모델이 pull되어 있지 않은 경우입니다.
```bash
ollama pull gemma4:31b
```

**Agent interpret 422 오류**

```
HTTP/1.1 422 Unprocessable Entity
{"detail":[{"type":"list_type","loc":["body","step_results",0,"predictions"],...}]}
```

모델 컨테이너가 `predictions`를 list 대신 dict(`{"left": {...}, "right": {...}}`)로 반환할 때 발생합니다.
백엔드의 `_normalize_predictions()`가 자동으로 list로 변환합니다. 새 모델 추가 시 동일하게 적용됩니다.

---

**prediction 모드에서 `type_mismatch` 반환**
```json
{"status": "type_mismatch", "mismatched_models": [...]}
```
업로드한 파일 타입이 모델의 `required_data`와 맞지 않습니다. 올바른 파일 형식을 확인하거나 `/projects/{dept}/{proj}/data` 엔드포인트로 `required_data`를 조회합니다.
