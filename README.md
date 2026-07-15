# maple-routing-server

maple-platform의 백엔드 서버입니다.
의료 AI 추론 요청을 받아 컨테이너화된 AI 모델로 라우팅하고, 결과를 저장·반환합니다.

---

## 시스템 구성

maple-platform은 네 개의 독립적인 서버로 구성됩니다.

```
[Frontend UI]  maple-client (React/Electron)  — 사용자 로컬 PC
      │
      │  HTTP (Agent 직접 접근 불가 — 백엔드 프록시 경유)
      │  ※ 라우팅 서버가 원격이므로 로컬 PC → 서버는 SSH 터널(-L 8100)로 연결
      ▼
[Back-end]     maple-routing-server (FastAPI, Port 8100)    ◄── 이 저장소
      │        (A100 서버, conda env `maple`)
      │
      ├── HTTP(localhost:8110) ──► [추론 게이트웨이]  maple-inference (Port 8110)
      │             ├── runtime-basic   (외부 9020, 내부 8000)   ┐ 같은 A100 서버 위
      │             ├── runtime-medical (외부 9021, 내부 8000)   │ docker compose 스택
      │             ├── runtime-yolo    (외부 9022, 내부 8000)   │ (maple-model-
      │             └── runtime-nnunet  (외부 9023, 내부 8000)   ┘  execution-server)
      │
      └── HTTP(사설망 :8101) ──► [AI Agent]  maple-agent-server (H100 서버, Port 8101)
                                                  ├── vLLM (gemma-4-31B-it)
                                                  ├── ChromaDB (Port 8010, RAG/모델 검색)
                                                  └── /wiki (LLM Wiki, 지식 누적)
```

> **배포 구성 (GPU 서버 2대)**
> - **A100 서버**: 라우팅 서버(8100) + 모델실행 스택(8110, 9020~9023) + MongoDB(도커) 를 모두 실행. 라우팅은 도커가 아닌 **conda(`maple`, Python 3.10) + uvicorn** 으로 호스트에서 직접 실행.
> - **H100 서버**: AI Agent(8101, vLLM/ChromaDB). 별도 서버.
> - **A100↔H100 통신 = 사설망 직결**: 두 서버는 사설 IP로 서로 직접 통신한다. 라우팅은 `AGENT_URL=http://<H100-사설IP>:8101` 로 Agent를 **인터넷·터널 없이 사설망으로 직접 호출**한다. (각 서버의 사설 IP 확인: `ip addr show ens224`)
> - **클라이언트↔A100 서버 통신**: 클라이언트는 사용자 **로컬 PC**에서 실행되므로, 로컬 PC에서 라우팅 서버로 SSH 터널(`-L 8100`)을 열어 접속한다 (아래 [실행 방법](#실행-방법) 참고).

> **중요**: 클라이언트(프론트엔드)는 Agent(H100 서버, 사설망)에 직접 접근할 수 없습니다 — 사설망은 A100 서버만 접근 가능.
> 프론트엔드의 모든 Agent 요청은 백엔드(`/inference/agent/*`)를 통해 프록시됩니다.
> 컨테이너끼리 통신할 때는 내부 포트 8000 사용 (예: `http://runtime-medical:8000`)

| 서버 | 저장소 | 위치 / 포트 | 역할 |
|---|---|---|---|
| Back-end | `maple-routing-server` | A100 서버 `:8100` (conda `maple`) | 추론 라우팅, 프로젝트 관리, 결과 저장, Agent 프록시 |
| 추론 게이트웨이 | `maple-inference` | A100 서버 `:8110` (docker) | 런타임 컨테이너 라우팅 (백엔드 내부용) |
| AI 모델 | `maple-model-execution-server` | A100 서버 `9020~9023` (docker, 내부 8000) | 도메인 특화 AI 모델 실행 |
| AI Agent | `maple-agent-server` | H100 서버 `:8101` (A100↔H100 사설망 직결) | 모드별 쿼리 라우팅, RAG 임상 해석, 모델 검색, VLM 범용 분석 |
| Frontend | `maple-client` | 사용자 로컬 PC (SSH `-L 8100` → A100 서버) | 사용자 인터페이스 |

### 서버 인프라 (A100 서버 실행 형태)

A100 서버에서 각 구성요소가 실행되는 방식은 다음과 같다. **라우팅 서버 자체는 도커가 아니라 conda 로 실행**하고, 도커는 MongoDB·모델실행 스택에만 쓴다.

| 구성요소 | 실행 형태 | 비고 |
|---|---|---|
| 라우팅 서버 | **conda** env `maple` (Python 3.10) + `uvicorn` (호스트 직접) | 도커화하지 않음. `main.py` 참고 |
| MongoDB | **도커** 컨테이너 `maple-mongo` (`mongo:7`) | `127.0.0.1:27017`, named volume `maple-mongo-data` 에 데이터 영속, `restart=unless-stopped` |
| 추론 게이트웨이 + 런타임 | **도커 compose** (`maple-inference` 8110, `runtime-*` 9020~9023) | **`maple-model-execution-server` 레포에서 관리·빌드** (이 레포 아님) |

> - **왜 라우팅은 conda(호스트), MongoDB는 도커인가?** — 라우팅은 코드를 자주 고치며 `--reload`로 돌려야 해서 호스트에서 conda로 직접 실행하는 편이 편하다. 반면 MongoDB는 한 번 띄우면 건드릴 일이 거의 없는 인프라라, 컨테이너로 격리해 버전 고정·데이터 볼륨 분리·재부팅 자동기동을 맡기는 게 관리하기 좋다. (개발/단일 서버 배포에서 흔한 조합)
> - MongoDB 는 `docker run -d --name maple-mongo -p 127.0.0.1:27017:27017 -v maple-mongo-data:/data/db mongo:7` 형태로 기동되어 있으며(`127.0.0.1` 바인딩 → 외부 비노출, `restart=unless-stopped`), 컨테이너를 지워도 볼륨 `maple-mongo-data` 만 있으면 데이터는 보존된다.
> - 도커 명령을 `sudo` 없이 쓰려면 실행 사용자가 `docker` 그룹에 속해야 한다 (`sudo usermod -aG docker $USER` 후 재로그인).
> - 모델실행 스택의 Dockerfile/compose 및 빌드 방법은 `maple-model-execution-server` 레포 README 를 참고. 이 레포는 그 컨테이너들을 **호출만** 한다.

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
├── Dockerfile                  # 백엔드 컨테이너 이미지 (현재 미사용 — A100 서버에선 conda로 직접 실행)
├── .dockerignore               # Dockerfile 빌드 시 제외 목록 (.env, .venv, .git, logs 등)
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

> `/inference/agent/*` 엔드포인트는 브라우저가 `localhost:8101`에 직접 접근할 수 없어 백엔드가 프록시 역할을 합니다.

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

> 아래는 `scan_and_register.py` 로 현재 A100 서버에 등록된 실제 구조 예시다.
> `project_name` 은 `AI_Models/{dept}/{project}/` 의 디렉토리명과 동일(언더스코어 표기),
> `docker.service_url` 은 compose 내부 이름(`runtime-*:8000`)을 그대로 저장한다 (게이트웨이가 이 값으로 포워딩).

```json
{
  "departments": [
    {
      "department_name": "Neurology",
      "projects": {
        "1": {
          "project_name": "BraTS2020_T1ce_UNet3D",
          "model_name": "BraTS2020_T1ce_UNet3D",
          "model_path": { "best.pt": "AI_Models/Neurology/BraTS2020_T1ce_UNet3D/checkpoint/best.pt" },
          "required_data": ["nii.gz", "nii"],
          "task_type": "segmentation",
          "result_type": ["segmentation_overlay", "3d_overlay"],
          "output_image_role": "segmentation_overlay",
          "docker": { "service_url": "http://runtime-medical:8000" }
        }
        // BraTS2020_T1_UNet3D / _T2_ / _FLAIR_ 도 동일하게 runtime-medical
      }
    },
    {
      "department_name": "Pulmonology",
      "projects": {
        "1": {
          "project_name": "ChestXray14_Multilabel_Classification",
          "model_name": "ChestXray14_Multilabel_Classification",
          "required_data": ["png", "jpg", "jpeg"],
          "task_type": "classification",
          "result_type": ["gradcam_overlay", "classification_probabilities"],
          "output_image_role": "gradcam_overlay",
          "docker": { "service_url": "http://runtime-medical:8000" }
        },
        "2": {
          "project_name": "RSNA_Pneumonia_YOLO26x",
          "model_name": "YOLO26x_RSNA_Pneumonia",
          "model_path": { "best.pt": "AI_Models/Pulmonology/RSNA_Pneumonia_YOLO26x/checkpoint/best.pt" },
          "required_data": ["dcm"],
          "task_type": "bbox detection",
          "result_type": ["bbox_overlay", "detection_predictions"],
          "output_image_role": "bbox_overlay",
          "docker": { "service_url": "http://runtime-yolo:8000" }
        }
      }
    }
  ]
}
```

> 현재 등록된 모델(6): Neurology `BraTS2020_{T1,T1ce,T2,FLAIR}_UNet3D` (→ runtime-medical), Pulmonology `ChestXray14_Multilabel_Classification` (→ runtime-medical) · `RSNA_Pneumonia_YOLO26x` (→ runtime-yolo).

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

| 변수 | 값 (A100 서버 기준) | 설명 |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB 연결 URI (`MONGO_URL` 도 하위 호환 인식) |
| `DB_NAME` | `maple_db` | 데이터베이스명 |
| `MAIN_DOCUMENT_ID` | *(필수, `.env` 설정)* | MongoDB `departments` 컬렉션 문서 ObjectId |
| `AGENT_URL` | `http://<H100-사설IP>:8101` | AI Agent 서비스 URL — **H100 서버 사설 IP 직결** (터널 불필요) |
| `AGENT_TIMEOUT` | `300` | Agent 요청 타임아웃 (초) |
| `MAPLE_INFERENCE_URL` | `http://localhost:8110` | 추론 게이트웨이 URL (같은 A100 서버 로컬 도커) |
| `MAPLE_REMOTE_INFERENCE_URL` | *(비움)* | 원격 추론 서버 URL (미사용) |
| `AI_MODELS_DIR` | `../maple-model-execution-server/AI_Models` | AI 모델 루트 디렉토리 (`scan_and_register.py` 스캔 대상) |

> **AGENT_URL**: A100↔H100이 사설망으로 직결되므로 Agent(H100 서버)의 사설 IP를 직접 지정한다 (`ip addr show ens224` 로 확인). 과거처럼 `localhost:8101` + SSH 터널을 쓸 필요 없음.
> **모델 컨테이너 URL**: 런타임 컨테이너 URL은 MongoDB `departments` 의 `docker.service_url`(예 `http://runtime-yolo:8000`)에서 읽으며, 게이트웨이(8110)가 이 값을 받아 compose 네트워크 내부에서 해당 런타임으로 포워딩한다.

---

## 실행 방법

### 라우팅 서버 실행 (A100 서버에서)

라우팅 서버는 **A100 서버**에서 **conda 환경 + uvicorn** 으로 실행한다 (도커 아님).

**사전 요구사항**
- Miniconda/Anaconda (A100 서버 `~/miniconda3`), env **`maple`** (Python 3.10)
- MongoDB — A100 서버에서 도커 컨테이너 `maple-mongo` (mongo:7) 로 실행 중, `localhost:27017`
- 추론 게이트웨이/런타임 — A100 서버에서 도커 compose 로 실행 중 (`maple-inference` 8110, 런타임 9020~9023)
- AI Agent — H100 서버 `:8101`, 사설망 직결

```bash
# 0. (최초 1회) conda 환경 준비
conda create -n maple python=3.10 -y
conda activate maple
pip install -r requirements.txt

# 1. MongoDB 컨테이너 상태 확인 (없으면 기동)
docker ps | grep maple-mongo
# 없으면: docker run -d --name maple-mongo -p 127.0.0.1:27017:27017 -v maple-mongo-data:/data/db mongo:7

# 2. Agent(H100 서버) 사설망 직결 확인  (<H100-사설IP> = H100 서버의 ens224 IP)
curl http://<H100-사설IP>:8101/health
# {"status":"ok","service":"maple-ai-agent","vllm":"ok","chromadb":"ok","model":"google/gemma-4-31B-it"}

# 3. 모델 레지스트리 확인/등록 (MongoDB가 비어 있으면 "모델 정보 없음" 발생)
python scan_and_register.py --dry-run   # 등록될 모델 미리보기
python scan_and_register.py             # AI_Models/ 스캔 → MongoDB + Agent ChromaDB 등록

# 4. 백엔드 서버 실행
conda activate maple
uvicorn main:app --host 0.0.0.0 --port 8100 --reload
```

API 문서 (A100 서버 로컬): http://localhost:8100/docs

### 클라이언트 연결 (로컬 PC)

클라이언트(`maple-client`)는 사용자 **로컬 PC**에서 돌고, 라우팅 서버는 원격(A100 서버)이다. 로컬 PC에서 라우팅 서버로 SSH 터널을 열면 클라이언트 코드 수정 없이 `http://localhost:8100` 그대로 사용할 수 있다.

```bash
# 로컬 PC 에서 (A100 서버로 SSH -L 터널; <A100-공인IP> = A100 서버 공인 IP)
ssh -N -L 8100:localhost:8100 tta@<A100-공인IP>
#   → 로컬 PC의 localhost:8100 → A100 서버 라우팅 8100

# 클라이언트 실행 (maple-client, src/api.ts 의 BACKEND_URL = http://localhost:8100)
npm start          # 웹 (localhost:3000)  또는
npm run electron   # 데스크톱 앱
```

> A100↔H100(Agent)는 사설망 직결이라 **로컬 PC를 경유하는 릴레이 터널은 더 이상 필요 없다**. 로컬 PC가 여는 터널은 오직 클라이언트→라우팅(`-L 8100`) 하나뿐.
> `ssh -N` 은 연결이 끊기면 자동 복구되지 않으므로, 상시 사용 시 자동 재접속 래퍼(autossh, 또는 PowerShell `while` 루프)로 감싸는 것을 권장.

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

> 백엔드(라우팅) 서버 자체는 Docker로 실행하지 않고, A100 서버에서 conda(`maple`) + uvicorn 으로 직접 실행합니다. (도커는 MongoDB·모델실행 스택에만 사용)

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
A100 서버에서는 MongoDB를 도커 컨테이너(`maple-mongo`)로 실행한다. 컨테이너 상태를 확인한다.
```bash
docker ps | grep maple-mongo
# 없으면 기동 (데이터는 named volume maple-mongo-data 에 보존)
docker run -d --name maple-mongo -p 127.0.0.1:27017:27017 -v maple-mongo-data:/data/db mongo:7
# 재시작만 필요하면
docker start maple-mongo
```

---

### 추론 요청 시 500 오류

**AI 컨테이너(게이트웨이/런타임)가 실행되지 않은 경우** — 모델실행 스택은 `maple-model-execution-server` 레포에서 관리하므로 그 디렉토리에서 조작한다.

```bash
# 컨테이너 상태 확인 (게이트웨이 maple-inference, 런타임 maple-runtime-*)
docker ps

cd ../maple-model-execution-server
# 재시작 (빌드 없이)  {서비스명} = maple-inference | runtime-yolo | runtime-medical ...
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d {서비스명}
# 로그 확인
docker compose -f docker-compose.yml -f docker-compose.runtime.yml logs -f {서비스명}
```

**컨테이너가 `unhealthy` 상태인 경우**

healthcheck가 실패한 것입니다. 컨테이너 내부 로그로 원인을 파악합니다.
```bash
docker logs {컨테이너명}                     # 예: maple-runtime-yolo
docker inspect {컨테이너명} | grep -A 10 Health
```

---

### `PermissionError: [Errno 13] ... 'data/input'`

라우팅은 업로드 파일을 `./data/input/...` 에 저장한다. 그런데 `data/` 는 런타임 컨테이너가 볼륨으로 마운트(`../maple-routing-server/data:/data`)하며 컨테이너가 root로 실행되므로, 컨테이너가 먼저 만든 `data/` 가 **root 소유**가 되면 conda(사용자 `tta`)로 도는 라우팅이 하위 디렉토리를 못 만들어 이 에러가 난다.

```bash
# 소유권을 A100 서버 실행 사용자(tta)로 교정
sudo chown -R tta:tta /home/tta/maple/maple-routing-server/data
```

---

### A100 서버 ↔ Agent(H100 서버) 연결 확인

라우팅(A100) → Agent(H100)는 **사설망 직결**이다. 연결이 안 되면:

```bash
# A100 서버에서 Agent 사설 IP 직결 확인 (<H100-사설IP> = H100 서버 ens224 IP)
curl http://<H100-사설IP>:8101/health
# 사설 IP 확인
ip addr show ens224                              # 각 서버의 사설 IP
ssh tta@<H100-사설IP> "ss -ltnp | grep 8101"     # H100 서버에서 Agent 리스닝(0.0.0.0:8101) 확인
```

`.env` 의 `AGENT_URL` 이 `http://<H100-사설IP>:8101` 인지 확인한다. (사설망이 열려 있으므로 과거의 노트북 경유 릴레이 터널은 불필요)

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

### `모델 정보 없음` (파이프라인 실패)

```
[DAG s1] 실패: [s1] RSNA_Pneumonia_YOLO26x 모델 정보 없음
```

라우팅은 실행 시 MongoDB `departments`에서 모델을 조회한다(`get_model_by_project`). **가장 흔한 원인은 MongoDB가 비어 있는 것**이다 — Agent(ChromaDB)는 모델을 알아 실행계획을 세우지만, 라우팅 MongoDB에 등록이 안 돼 있으면 실행 단계에서 이 에러가 난다. (Agent ChromaDB = 계획용 / 라우팅 MongoDB = 실행용, 두 레지스트리가 분리되어 있음)

```bash
# 1) 등록 상태 확인 (모델 수가 0이면 등록 필요)
conda activate maple
python - <<'PY'
from pymongo import MongoClient
d = MongoClient("mongodb://localhost:27017")["maple_db"]["departments"].find_one({})
print(sum(len(x.get("projects",{})) for x in d.get("departments",[])), "models")
PY

# 2) 등록 실행
python scan_and_register.py
```

프로젝트명 자체가 다른 경우도 있다. `project_name` 은 `AI_Models/{dept}/{project}/` 디렉토리명과 동일해야 하며, `_find_project` 가 언더스코어↔공백/대소문자/`ml`·`model` 접미사 정도는 자동 보정한다.

---

### Agent 서버 연결 오류

**ChromaDB tenant 오류**
```
ValueError: Tenant default_tenant not found
```
Agent 서버 최초 실행 시 ChromaDB를 먼저 띄운 후 `ingest_knowledge.py`를 실행하여 초기화합니다.
(Agent(H100 서버)의 ChromaDB는 `0.0.0.0:8010`. 아래 명령은 H100 서버에서 실행)
```bash
# ChromaDB 서버 실행 (H100 서버)
chroma run --host 0.0.0.0 --port 8010 --path ./chroma_data

# 사전 지식 ingest (최초 1회)
cd maple-agent-server
python scripts/ingest_knowledge.py
```

**vLLM 모델 미로드**
```
404 model not found
```
vLLM 서버가 `google/gemma-4-31B-it` 모델과 함께 실행 중인지 확인합니다.
```bash
python -m vllm.entrypoints.openai.api_server \
  --model google/gemma-4-31B-it \
  --tensor-parallel-size 2 \
  --port 8003
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
