# maple-routing-server 개발 요구사항

> **작성 배경**: `maple-client`를 MAPLE Clinical Chat UI로 재구축하면서 필요해진 백엔드 작업 정리.
> 현재 프론트엔드는 환자·의료진 데이터를 **데모 상수**로 들고 있고, 이를 서버 데이터로 교체하기 위한 명세다.
> **기준**: `maple-routing-server` (포트 8100) / `maple-model-execution-server` **dev** 브랜치

---

## 목차

1. [현재 상태](#1-현재-상태)
2. [DB 구축](#2-db-구축)
3. [신규 API](#3-신규-api)
4. [기존 API 수정](#4-기존-api-수정)
5. [인증](#5-인증)
6. [클라이언트 연결 지점](#6-클라이언트-연결-지점)
7. [우선순위 체크리스트](#7-우선순위-체크리스트)

---

## 1. 현재 상태

### 있는 것

| 항목 | 내용 |
|---|---|
| 프레임워크 | FastAPI + uvicorn (`main.py`, 포트 8100) |
| DB | MongoDB (`motor` 비동기 드라이버), DB명 `maple_db` |
| 컬렉션 | `departments` (모델 레지스트리), `inference_results` |
| 라우터 | `/projects`, `/inference`, `/admin`, `/pipeline` |
| 외부 연동 | Agent (`localhost:8101`), 추론 게이트웨이 (`localhost:8110`) |
| 헬스체크 | `GET /health` → `{"status":"ok","service":"maple-ai-backend"}` |

### 없는 것

| 항목 | 영향 |
|---|---|
| 인증 / 권한 | 누구나 모든 API 호출 가능 |
| `doctors` 컬렉션 | 프론트가 의료진 정보를 하드코딩 중 |
| `patients` / `visits` / `notes` 컬렉션 | 환자 데이터가 전부 데모 상수 |
| 환자–추론결과 연결 | `inference_results`가 진료과·프로젝트·모델로만 키잉되어 환자별 이력을 못 모음 |
| 감사 로그 | 의료 데이터 열람 기록 없음 |

### 핵심 문제

`POST /inference/`는 파일을 `./data/input/{진료과}/{프로젝트}/{타임스탬프}`에 저장한다.
**경로에 환자 식별자가 없어서 "이 환자의 지난 검사 결과"를 조회할 방법이 없다.**

---

## 2. DB 구축

### 2.1 `counters` — 원자적 ID 발급

프론트가 지금 `PT-{1000+maxIdx+1}`로 환자 ID를 만드는데 **동시 등록 시 충돌한다.**
서버가 원자적으로 발급해야 한다.

```python
async def next_patient_id(db) -> str:
    doc = await db["counters"].find_one_and_update(
        {"_id": "patient_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"PT-{1000 + doc['seq']}"
```

> 클라이언트의 환자 ID 입력란은 이미 `readonly`로 막아뒀다 (`RegisterModal.tsx`).

---

### 2.2 `doctors`

```jsonc
{
  "_id": ObjectId,
  "employee_id": "chest01",        // 로그인 ID (unique index)
  "password_hash": "$2b$12$...",   // bcrypt / argon2 — 평문 저장 금지
  "name": "김체스트 교수",
  "hospital": "챔피언 병원",
  "department": "정형외과",
  "title": "전문의",
  "initial": "김",                  // 아바타 표시용 (없으면 name[0])
  "is_active": true,
  "created_at": ISODate,
  "last_login_at": ISODate
}
```

**인덱스**: `employee_id` (unique)

---

### 2.3 `patients`

```jsonc
{
  "_id": ObjectId,
  "patient_id": "PT-1040",         // counters로 발급 (unique index)
  "name": "홍길동",
  "gender": "M",                    // "F" | "M" — 프론트가 화면에서만 여/남으로 표기
  "birth_date": "1972-04-10",      // 나이는 저장하지 않고 계산한다
  "appt_date": "2026-07-21",        // 좌측 캘린더가 이 값으로 목록을 고른다
  "appt_time": "15:40",
  "exam_type": "DICOM",             // 표시용. 빈 문자열 허용
  "care_status": "관찰중",           // 관찰중 | 치료중 | 추적관찰 | 퇴원
  "assigned_doctor_id": ObjectId,
  "created_at": ISODate,
  "updated_at": ISODate
}
```

**인덱스**
- `patient_id` (unique)
- `(appt_date, appt_time)` — 날짜별 예약 목록 조회
- `name`, `patient_id` — 검색

> **나이를 저장하지 말 것.** 프론트는 `birth_date`에서 만 나이를 계산한다 (`ageFromBirthDate`).
> 나이를 저장하면 시간이 지나며 틀어진다.

---

### 2.4 `visits` — 한 번의 촬영·분석

```jsonc
{
  "_id": ObjectId,
  "visit_id": "V-20260721-0001",
  "patient_id": "PT-1040",
  "doctor_id": ObjectId,
  "visit_date": "2026-07-21",
  "exam_type": "흉부 CT",
  "modalities": ["T1", "FLAIR"],   // 시퀀스가 나뉘는 검사만
  "status": "done",                 // waiting | analyzing | done | failed

  // ── AI 분석 결과 ──
  "model_name": "RSNA_Pneumonia_YOLO26x",   // 분석 배지에 표시
  "risk_tier": "High",                       // Low | High | Critical  ← 서버가 판정 (§4-③)
  "confidence": 0.87,
  "finding": "우측 폐야에서 폐렴 의심 소견...",        // ← 3개 필드로 분리 (§4-②)
  "interpretation": "경화 및 간유리음영 소견은...",
  "recommendation": "① 영상의학과 판독 검토 ...",

  // ── 이미지 (GridFS file_id) ──
  "images": {
    "base": ["65f1a...", "65f1b...", "65f1c..."],   // 슬라이스별 원본
    "heat": ["65f1d...", "65f1e...", "65f1f..."],   // Grad-CAM 오버레이
    "box":  ["65f20...", "65f21...", "65f22..."]    // Bounding box 오버레이
  },
  "predictions": { /* 모델 원본 출력 */ },
  "created_at": ISODate
}
```

**인덱스**: `(patient_id, visit_date desc)`, `visit_id` (unique)

> `images`의 세 배열은 **같은 인덱스가 같은 슬라이스**에 대응해야 한다.
> 프론트 뷰어가 슬라이스 인덱스로 세 배열을 동시에 참조한다 (`SliceSet`).
> 오버레이가 원본보다 적으면 클라이언트가 마지막 프레임을 재사용해 길이를 맞춘다.

---

### 2.5 `notes`

```jsonc
{
  "_id": ObjectId,
  "patient_id": "PT-1040",
  "author_id": ObjectId,
  "author_name": "김체스트 교수",   // 접수 메모는 "김체스트 교수 · 접수"
  "text": "환자 발열 38.2도, 항생제 투여 시작함.",
  "created_at": ISODate
}
```

**인덱스**: `(patient_id, created_at)`

---

### 2.6 `chat_messages`

```jsonc
{
  "_id": ObjectId,
  "patient_id": "PT-1040",
  "visit_id": "V-20260721-0001",   // 방문별로 대화를 묶는다
  "doctor_id": ObjectId,
  "role": "user",                   // user | assistant
  "content": "폐결절 의심 소견이 있는지 확인해줘.",
  "created_at": ISODate
}
```

**인덱스**: `(patient_id, visit_id, created_at)`

---

### 2.7 `access_logs` — 감사 로그

의료 시스템에서 사실상 필수다. **미들웨어로 붙이는 게 나중에 소급하기보다 훨씬 쉽다.**

```jsonc
{
  "doctor_id": ObjectId,
  "employee_id": "chest01",
  "action": "read",                 // read | create | update | delete
  "resource": "patient",
  "resource_id": "PT-1040",
  "method": "GET",
  "path": "/patients/PT-1040",
  "ip": "10.70.16.2",
  "at": ISODate
}
```

**인덱스**: `(doctor_id, at desc)`, `(resource_id, at desc)`

---

### 2.8 이미지 저장 — GridFS

현재 추론 결과 이미지는 **base64로 응답에 인라인**된다.
원본·Grad-CAM·Bbox × N슬라이스면 응답이 수십 MB가 된다.

- GridFS 버킷(`fs.files` / `fs.chunks`)에 저장하고 **`file_id`만 반환**
- `GET /files/{file_id}`로 스트리밍 (`Content-Type: image/png`, `Cache-Control` 설정)

> 프론트는 이미 URL과 `data:` URI를 모두 받게 되어 있어 **클라이언트 수정 없이 전환된다** (`toSrc()`).

---

### 2.9 `departments` 리팩터링 (기존)

모든 진료과·프로젝트·모델이 **단일 문서**에 들어 있고 `MAIN_DOCUMENT_ID` 환경변수로 찾는다.

- 동시 쓰기에 취약 (`_save_doc`이 문서 전체를 `replace_one`)
- dev 브랜치에서 모델이 50개로 늘었다 → 문서 크기·경합 증가

`departments` / `projects` / `models` 컬렉션 분리를 권한다.

---

## 3. 신규 API

### 3.1 인증

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/auth/login` | 로그인 |
| `POST` | `/auth/refresh` | 액세스 토큰 재발급 |
| `POST` | `/auth/logout` | 리프레시 토큰 폐기 |
| `GET` | `/auth/me` | 현재 세션의 의료진 프로필 |

**`POST /auth/login`**

```jsonc
// Request
{ "employee_id": "chest01", "password": "..." }

// 200
{
  "access_token": "eyJhbGci...",
  "refresh_token": "eyJhbGci...",
  "doctor": {
    "employee_id": "chest01",
    "name": "김체스트 교수",
    "hospital": "챔피언 병원",
    "department": "정형외과",
    "title": "전문의",
    "initial": "김"
  }
}

// 401 — 자격 증명 불일치
{ "detail": "invalid credentials" }
```

> **클라이언트 동작**: `401`/`403`이면 즉시 실패시킨다.
> `404`/`405`(엔드포인트 미구현) 또는 연결 실패일 때만 데모 계정으로 폴백한다.
> 즉 **이 엔드포인트가 생기는 순간 데모 우회는 자동으로 닫힌다.**

---

### 3.2 환자

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/patients` | 환자 등록 (`patient_id` 서버 발급) |
| `GET` | `/patients?date=YYYY-MM-DD` | 해당 날짜 예약 목록 — **좌측 패널** |
| `GET` | `/patients/{patient_id}` | 상세 |
| `PATCH` | `/patients/{patient_id}` | `care_status` 등 변경 |

**`POST /patients`**

```jsonc
// Request — patient_id는 보내지 않는다 (서버 발급)
{
  "name": "홍길동",
  "gender": "M",
  "birth_date": "1972-04-10",
  "appt_date": "2026-07-21",
  "appt_time": "15:40",
  "note": "폐결절 의심 소견 확인해줘."   // 선택. 있으면 notes에 저장
}

// 201
{ "patient_id": "PT-1040", ... }
```

**`GET /patients?date=2026-07-21`**

```jsonc
{
  "patients": [
    {
      "patient_id": "PT-1002",
      "name": "박예빈",
      "gender": "F",
      "birth_date": "1972-03-03",
      "appt_time": "09:20",
      "exam_type": "흉부 CT",
      "care_status": "치료중",
      "risk_tier": "High",        // 최신 방문 기준 — 좌측 위험도 배지
      "status": "done",           // waiting → "대기", analyzing → "분석중" 표시
      "visit_count": 2
    }
  ]
}
```

> **날짜 필터가 필수다.** 좌측 패널이 캘린더로 날짜를 골라 목록을 조회하는 구조다.
> 위험도 정렬은 프론트에서 하므로 서버는 `appt_time` 순으로 주면 된다.

---

### 3.3 방문 · 메모 · 대화

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/patients/{id}/visits` | 방문 이력 — 이력 패널 / 방문 칩 |
| `GET` | `/patients/{id}/visits/{visit_id}` | 방문 상세 (분석 카드 + 이미지 file_id) |
| `GET` | `/patients/{id}/notes` | 메모 목록 |
| `POST` | `/patients/{id}/notes` | 메모 작성 |
| `GET` | `/patients/{id}/chat?visit_id=` | 대화 이력 |
| `POST` | `/patients/{id}/chat` | 대화 저장 |
| `GET` | `/files/{file_id}` | 이미지 스트리밍 |

---

## 4. 기존 API 수정

> 아래 7건은 **클라이언트를 구현하면서 실제로 막힌 지점**이다.

### ① `history`가 버려진다

프론트는 `POST /inference/agent/plan`에 최근 20개 대화를 `history`로 보내지만,
라우팅 서버가 이를 어디에서도 읽지 않아 **후속 질문의 문맥이 Agent에 전혀 전달되지 않는다.**

수정 범위는 3곳이다.

**(1) `services/agent_service.py` — `plan()` 시그니처에 파라미터 추가**

```python
async def plan(
    self,
    mode: str,
    query: str,
    ...
    attachments: list[dict] | None = None,
    history: list[dict] | None = None,      # ← 추가
) -> dict:
```

**(2) 같은 함수 — Agent 페이로드에 포함**

```python
if history:
    payload["history"] = history            # ← 추가
```

**(3) `controllers/inference_controller.py` — `proxy_agent_plan()`에서 전달**

```python
result = await agent_service.plan(
    mode           = body.get("mode", "auto"),
    query          = body.get("query", ""),
    ...
    csv_data       = body.get("csv_data"),
    history        = body.get("history"),   # ← 추가
)
```

**(4) `maple-agent-server`** — `POST /agent/plan`이 `history`를 받아 프롬프트에 반영해야 한다.
라우팅 서버만 고쳐도 Agent가 무시하면 효과가 없으므로 Agent 담당자와 함께 확인할 것.

> 프론트가 보내는 형식: `[{"role": "user"|"assistant", "content": "..."}, ...]`

---

### ② 해석 텍스트를 3개 필드로 분리

UI는 **소견 / 임상적 해석 / 권장 조치**를 3단으로 표시한다.
지금은 `interpretation` 한 덩어리로 와서 **프론트가 정규식으로 쪼갠다** (`service.ts` `parseSections()`).

Agent가 구조화해서 반환하도록 바꾸고, 프론트 파서는 폴백으로만 남긴다.

```jsonc
{
  "finding": "...",
  "interpretation": "...",
  "recommendation": "..."
}
```

---

### ③ `risk_tier` · `confidence`를 응답에 포함

지금은 프론트가 `predictions.confidence`에서 임의 임계값(0.85 / 0.6)으로 등급을 매긴다.

**임상 위험도 판정을 클라이언트에서 하는 건 위험하다.** 서버가 명시해야 한다.

```jsonc
{ "risk_tier": "High", "confidence": 0.87 }
```

---

### ④ 이미지를 `file_id`로 반환

[§2.8](#28-이미지-저장--gridfs) 참고. base64 인라인 → GridFS + `file_id`.

---

### ⑤ `POST /inference/`에 `patient_id` · `visit_id` 추가

지금 구조로는 추론 결과를 환자에 붙일 수 없다.

```
mode, query, department, project,
patient_id,   ← 추가
visit_id,     ← 추가 (없으면 서버가 생성)
<파일들>
```

결과는 `visits` 컬렉션에 저장하고, 저장 경로도 `./data/input/{patient_id}/{visit_id}/`로 바꾸는 게 낫다.

---

### ⑥ 모달리티를 구분할 수 없다

form-data가 파일을 **데이터 타입으로만 키잉**한다 (`nifti`, `dicom`, `csv`, `image`).
BraTS의 `T1` / `T1ce` / `T2` / `FLAIR` 4개 시퀀스를 서버가 분간하지 못한다.

- dev 브랜치에서 `modality`를 선언하는 모델: `BraTS2020_{T1,T1ce,T2,FLAIR}_UNet3D`, `SynthStroke_T1`
- 시퀀스별 라우팅이 필요하면 폼 키를 `nifti_T1` 형태로 바꾸거나 별도 메타 필드를 받아야 한다

> 이 제약 때문에 등록 모달에서 모달리티 선택을 제외했다. 스키마가 준비되면 다시 넣을 수 있다.

---

### ⑦ CORS 축소

```python
# 현재 — 전면 개방
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
```

인증 도입과 함께 클라이언트 오리진으로 좁히고 `allow_credentials=True`를 설정한다.

---

## 5. 인증

### 구현

- JWT: access(짧게, 15~30분) + refresh(길게)
- 비밀번호: bcrypt 또는 argon2
- 보호 라우트에 `Depends(get_current_doctor)`

### 추가 의존성

```
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
```

### 권장 사항

- **감사 로그** ([§2.7](#27-access_logs--감사-로그)) — 누가 어느 환자 기록을 열람했는지
- **유휴 자동 로그아웃** — 진료 워크스테이션은 공용이다. 클라이언트는 현재 `sessionStorage`를 써서
  창을 닫으면 세션이 사라지지만, 자리를 비운 동안의 노출은 서버 토큰 만료로 막는 게 확실하다

---

## 6. 클라이언트 연결 지점

서버 작업이 끝나면 프론트에서 바꿀 곳.

| 클라이언트 위치 | 현재 | 서버 연동 후 |
|---|---|---|
| `clinical/auth.ts` → `login()` | `POST /auth/login` 시도 후 데모 폴백 | `DEMO_ACCOUNTS` + 폴백 분기 삭제 |
| `clinical/data.ts` → `getPatients()` | 데모 환자 40명 생성 | `GET /patients?date=`로 교체 |
| `clinical/data.ts` → `INITIAL_NOTES`, `INITIAL_MGMT` | 데모 상수 | `GET /patients/{id}/notes` |
| `clinical/data.ts` → `imagingInfo()`, `clinicalInfo()` | idx 기반 파생값 | 방문/환자 실데이터 |
| `clinical/service.ts` → `caseFromInference()` | 응답 → `CaseData` 매핑 | ②③④ 반영해 파싱 로직 축소 |
| `clinical/service.ts` → `parseSections()` | 정규식 파서 | 폴백으로만 유지 |
| `Workspace.tsx` → `liveVisits` 상태 | 메모리 보관 | `POST /inference/` 후 `visits` 재조회 |
| `api.ts` | 토큰 없이 호출 | `Authorization: Bearer` 인터셉터 추가 |

**이미 서버 데이터를 받을 준비가 된 부분** (수정 불필요):
- `SliceSet` — URL / base64 둘 다 허용
- 환자 ID 입력란 — `readonly` (서버 발급 전제)
- `gender` — `F`/`M`으로 보관, 화면에서만 여/남
- 나이 — `birth_date`에서 계산

---

## 7. 우선순위 체크리스트

### 1차 — 환자 흐름 (이것만 되면 데모 데이터를 걷어낼 수 있다)

- [ ] `counters` + `next_patient_id()` 원자 발급
- [ ] `patients` 컬렉션 + 인덱스
- [ ] `POST /patients`, `GET /patients?date=`
- [ ] `visits` 컬렉션
- [ ] `POST /inference/`에 `patient_id` 추가 → `visits` 저장
- [ ] `GET /patients/{id}/visits`

### 2차 — 결과 품질

- [ ] ④ 이미지 GridFS + `GET /files/{id}`
- [ ] ② 해석 3필드 분리 (Agent 응답 스키마 변경 동반)
- [ ] ③ `risk_tier` · `confidence` 응답 포함
- [ ] ① `history` 전달 (라우팅 서버 3곳 + Agent 프롬프트 반영)
- [ ] `notes` · `chat_messages` 컬렉션 + API

### 3차 — 운영 · 보안

- [ ] `doctors` 컬렉션 + JWT 로그인
- [ ] 보호 라우트에 `Depends(get_current_doctor)`
- [ ] ⑦ CORS 축소 + `allow_credentials`
- [ ] `access_logs` 감사 미들웨어
- [ ] ⑥ 모달리티 폼 스키마
- [ ] `departments` 컬렉션 분리 ([§2.9](#29-departments-리팩터링-기존))

---

**요약**
- **1차 6개 항목이 실제 환자 데이터로 넘어가는 최소 조건**이다. 이것만 되면 프론트에서 데모 상수를 걷어낼 수 있다.
- ①은 다른 항목과 독립적이라 지금 바로 착수 가능하다 (단, Agent 서버 협의 필요).
- 인증(3차)은 데모 시연을 막지는 않지만, **실제 환자 데이터를 붙이기 전에는 반드시 선행돼야 한다.**
  현재 클라이언트의 데모 로그인은 자격 증명이 번들에 포함되어 있어 보안 경계가 아니다.
