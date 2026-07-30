# Maple Client API 연동 인수인계

> 대상: `maple-client` 프론트엔드 담당자  
> 백엔드: `maple-routing-server`  
> 구현 기준일: 2026-07-29  
> API 문서: 백엔드 실행 후 `http://localhost:8100/docs`

이 문서는 현재 백엔드에 **실제로 구현된 계약**을 기준으로 작성했다. 클라이언트
데모 데이터와 로컬 분석 상태를 아래 API로 교체할 때 사용한다.

---

## 1. 연결 정보

개발 기본 주소:

```ts
export const API_BASE_URL =
  process.env.REACT_APP_API_BASE_URL ?? 'http://localhost:8100';
```

원격 서버를 SSH port forwarding으로 연결하는 경우에도 클라이언트에서는
`http://localhost:8100`을 사용한다.

허용된 클라이언트 origin:

```text
개발 웹        http://localhost:3000
Electron      app://maple
```

모든 JSON 요청은 특별한 언급이 없으면 다음 헤더를 사용한다.

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

환자 등록과 파일 업로드는 `multipart/form-data`이며, 브라우저가 boundary를
지정하도록 `Content-Type`을 직접 설정하지 않는다.

---

## 2. 데모 계정

| 사번 | 비밀번호 | 권한 |
|---|---|---|
| `chest01` | `1234` | doctor |
| `rad02` | `1234` | doctor |
| `mapleadmin03` | `1234` | doctor, admin |

`1234`는 시연용 초기 비밀번호다. 일반 회원가입 비밀번호는 8자 이상이어야 한다.

---

## 3. 인증

### 3.1 로그인

```http
POST /auth/login
```

```json
{
  "employee_id": "chest01",
  "password": "1234"
}
```

응답:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 1800,
  "doctor": {
    "employee_id": "chest01",
    "name": "김체스트 교수",
    "hospital": "챔피언 병원",
    "department": "정형외과",
    "title": "전문의",
    "initial": "김",
    "roles": ["doctor"]
  }
}
```

두 토큰과 `doctor`는 공용 워크스테이션 정책에 따라 `sessionStorage`에 저장한다.

### 3.2 회원가입

```http
POST /auth/signup
```

```json
{
  "employee_id": "doctor04",
  "password": "password1234",
  "name": "홍길동",
  "hospital": "챔피언 병원",
  "department": "영상의학과",
  "title": "전문의"
}
```

- 가입 즉시 로그인 토큰을 반환한다.
- `hospital`은 정확히 `챔피언 병원`이어야 한다.
- 직접 가입한 사용자는 항상 `roles=["doctor"]`다.
- 중복 사번은 `409`다.

### 3.3 내 정보

```http
GET /auth/me
```

응답은 로그인 응답의 `doctor`와 같은 형태다.

### 3.4 Access token 갱신

```http
POST /auth/refresh
```

```json
{
  "refresh_token": "..."
}
```

응답은 로그인과 동일한 토큰 응답이다. Refresh token은 호출할 때마다 회전하므로
응답으로 받은 **새 refresh token을 반드시 저장**한다. 이미 사용한 refresh token을
다시 보내면 해당 세션이 폐기된다.

권장 동작:

1. 보호 API가 `401`을 반환하면 refresh를 한 번만 호출한다.
2. 동시에 여러 요청이 `401`이어도 refresh 요청은 하나만 실행한다.
3. 새 토큰 저장 후 원 요청을 한 번만 재시도한다.
4. refresh도 실패하면 토큰을 지우고 로그인 화면으로 이동한다.

### 3.5 로그아웃

```http
POST /auth/logout
Authorization: Bearer <access_token>
```

```json
{
  "refresh_token": "..."
}
```

성공은 body 없는 `204`다. 성공 후 `sessionStorage`를 비운다.

---

## 4. 공통 TypeScript 타입

```ts
export type Gender = 'M' | 'F';
export type CareStatus = '관찰중' | '치료중' | '추적관찰' | '퇴원';

export type AppointmentStatus =
  | 'scheduled'
  | 'checked_in'
  | 'in_progress'
  | 'completed'
  | 'cancelled';

export type VisitStatus = 'open' | 'completed' | 'cancelled';

export type AnalysisStatus =
  | 'waiting_for_files' // 분석 문서가 없을 때 목록 API가 합성하는 상태
  | 'queued'
  | 'analyzing'
  | 'done'
  | 'failed'
  | 'cancelled'
  | 'superseded';

export type RiskTier = 'Low' | 'High' | 'Critical' | null;
export type RiskStatus = 'pending' | 'assessed' | 'unavailable';

export interface DoctorSummary {
  employee_id: string;
  name: string;
}
```

현재 모델 레지스트리에 위험도 정책이 아직 없으므로 새 실제 분석은 정상 완료돼도
`risk_tier=null`, `risk_status="unavailable"`일 수 있다. 클라이언트는 이를
“미분류”로 표시해야 하며 confidence만으로 위험도를 재계산하지 않는다.

---

## 5. 날짜와 예약 규칙

날짜와 시각은 다음 형식으로 보낸다.

```text
date    YYYY-MM-DD
time    HH:mm
```

서버 검증 규칙:

- 서울 기준 오늘 이전 날짜 금지
- 오늘 예약은 서버 검증 시각보다 미래여야 함
- `08:00`부터 `17:00`까지
- 10분 단위
- 같은 의사의 동일 슬롯은 한 건만 허용

오류:

```text
잘못된 날짜/시간       422
동일 슬롯 선점됨       409
```

클라이언트의 `Today`는 하드코딩하지 않고 로컬 날짜로 계산한다. 화면 제약과
무관하게 서버 응답이 최종 판정이다.

---

## 6. 환자 검색과 접수

### 6.1 기존 환자 검색

```http
GET /patients/search?q=홍길
```

- 이름 앞자리 검색
- 환자번호 `PT-1001`, `pt1001`, `1001` 모두 지원
- 같은 병원의 보관되지 않은 환자만 반환
- 최대 20건

```ts
interface PatientSearchResponse {
  results: Array<{
    patient_id: string;
    name: string;
    gender: Gender;
    birth_date: string;
    last_visit_date: string | null;
    visit_count: number;
  }>;
}
```

검색 input에는 200~300ms debounce를 권장한다. 모달 기본 탭은 기존 환자 검색이다.

### 6.2 신규 환자 등록

```http
POST /patients
Content-Type: multipart/form-data
```

FormData:

```text
name          필수
gender        M | F
birth_date    YYYY-MM-DD
appt_date     YYYY-MM-DD
appt_time     HH:mm
note          선택
files         선택, 같은 key로 0..N회 append
```

보내지 않는 값:

```text
patient_id
doctor_id
hospital_id
autoAnalyze
exam_type
```

```ts
const body = new FormData();
body.append('name', input.name);
body.append('gender', input.gender);
body.append('birth_date', input.birthDate);
body.append('appt_date', input.apptDate);
body.append('appt_time', input.apptTime);
if (input.note.trim()) body.append('note', input.note.trim());
for (const file of input.files) body.append('files', file);
```

파일 허용 형식:

```text
.dcm, .dicom
.nii, .nii.gz
.csv
.png, .jpg, .jpeg
```

파일 한 건의 현재 기본 제한은 100MiB다. 허용되지 않은 형식은 `415`다.

### 6.3 재방문 접수

```http
POST /patients/{patient_id}/appointments
Content-Type: multipart/form-data
```

FormData:

```text
appt_date
appt_time
note          선택
files         선택, 같은 key로 0..N회 append
```

재방문 요청에는 이름, 성별, 생년월일을 보내지 않는다. 접수한 로그인 의사가 이번
예약과 분석 요청 의사가 되지만 환자의 기존 장기 담당의는 변경되지 않는다.

### 6.4 신규·재방문 공통 응답

파일이 있는 경우:

```json
{
  "patient_id": "PT-1041",
  "appointment_id": "A-20260730-0041",
  "visit_id": "V-20260730-0041",
  "analysis": {
    "analysis_id": "AN-20260730-0041",
    "status": "queued",
    "queue_position": 1
  },
  "visit_status": "open",
  "analysis_status": "queued"
}
```

파일이 없는 경우:

```json
{
  "patient_id": "PT-1041",
  "appointment_id": "A-20260730-0041",
  "visit_id": "V-20260730-0041",
  "analysis": null,
  "visit_status": "open",
  "analysis_status": "waiting_for_files"
}
```

등록 체크박스는 사용하지 않는다. 파일이 있으면 항상 분석 큐에 등록되고, 파일이
없으면 방문만 생성된다.

---

## 7. 날짜별 좌측 환자 목록

```http
GET /patients?date=2026-07-29
```

```ts
interface PatientListResponse {
  date: string;
  patients: Array<{
    patient_id: string;
    name: string;
    gender: Gender;
    birth_date: string;
    appt_time: string;
    exam_type: string;
    care_status: CareStatus;
    assigned_doctor: DoctorSummary | null;
    appointment_doctor: DoctorSummary;
    appointment_id: string;
    latest_visit_id: string;
    latest_analysis_id: string | null;
    appointment_status: AppointmentStatus;
    analysis_status: AnalysisStatus;
    queue_position: number | null;
    risk_tier: RiskTier;
    confidence: number | null;
    visit_count: number;
  }>;
}
```

- 취소 예약은 기본 목록에서 제외된다.
- 서버는 예약 시각순으로 반환한다.
- 완료 환자를 하단에 배치하는 기준은 `appointment_status==="completed"`다.
- React 선택 상태와 캐시는 배열 `idx`가 아니라 `patient_id`를 key로 사용한다.

권장 표시:

| analysis_status | 표시 |
|---|---|
| `waiting_for_files` | 파일 대기 |
| `queued` | 대기, `queue_position` 표시 가능 |
| `analyzing` | 분석 중 |
| `done` | 완료 |
| `failed` | 실패 및 재시도 안내 |
| `cancelled` | 취소 |

---

## 8. 환자와 예약 관리

### 8.1 환자 상세

```http
GET /patients/{patient_id}
```

```ts
interface PatientDetail {
  patient_id: string;
  name: string;
  gender: Gender;
  birth_date: string;
  care_status: CareStatus;
  assigned_doctor: DoctorSummary | null;
  visit_count: number;
  created_at: string;
}
```

### 8.2 관리 상태 변경

```http
PATCH /patients/{patient_id}
```

```json
{
  "care_status": "추적관찰"
}
```

### 8.3 예약 이력

```http
GET /patients/{patient_id}/appointments
```

각 항목:

```ts
interface AppointmentHistoryItem {
  appointment_id: string;
  scheduled_at: string;
  scheduled_date: string;
  scheduled_time: string;
  exam_type: string;
  status: AppointmentStatus;
  doctor: DoctorSummary;
  visit_id: string | null;
  analysis_id: string | null;
  analysis_status: AnalysisStatus;
}
```

### 8.4 예약 취소

```http
PATCH /appointments/{appointment_id}
```

```json
{
  "status": "cancelled"
}
```

### 8.5 진료 완료

```json
{
  "status": "completed"
}
```

### 8.6 예약 시각 변경

```json
{
  "scheduled_date": "2026-07-31",
  "scheduled_time": "10:20"
}
```

`status` 변경과 날짜 변경을 한 요청에 같이 보내면 `422`다.

응답:

```ts
interface AppointmentPatchResponse {
  appointment_id: string;
  status: AppointmentStatus;
  scheduled_at: string;
  cancelled_analysis_ids: string[];
  running_analysis_ids: string[];
}
```

예약 취소 시 queued 분석은 함께 취소된다. 이미 analyzing인 분석은 즉시 중단하지
않으며 `running_analysis_ids`에 포함될 수 있다.

---

## 9. 방문과 재분석

### 9.1 환자 방문 이력

```http
GET /patients/{patient_id}/visits
```

```ts
interface VisitHistoryItem {
  visit_id: string;
  appointment_id: string;
  visit_date: string;
  exam_type: string;
  status: VisitStatus;
  doctor: DoctorSummary;
  latest_analysis_id: string | null;
  analysis_status: AnalysisStatus;
  risk_tier: RiskTier;
}
```

### 9.2 방문 상세

```http
GET /patients/{patient_id}/visits/{visit_id}
```

```ts
interface VisitDetail {
  visit_id: string;
  appointment_id: string;
  patient_id: string;
  visit_date: string;
  exam_type: string;
  status: VisitStatus;
  doctor: DoctorSummary;
  analyses: AnalysisResponse[];
}
```

### 9.3 방문 분석 이력

```http
GET /visits/{visit_id}/analyses
```

최신순 `AnalysisResponse[]`를 반환한다.

### 9.4 파일 추가 및 재분석

```http
POST /visits/{visit_id}/analyses
Content-Type: multipart/form-data
```

FormData:

```text
query       선택
files       필수, 1..N
```

응답:

```json
{
  "analysis_id": "AN-20260730-0042",
  "status": "queued",
  "queue_position": 2
}
```

기존 성공 결과를 덮어쓰지 않으며 방문 분석 이력에 새 실행으로 추가된다.

---

## 10. 분석 polling

```http
GET /analyses/{analysis_id}
```

```ts
interface AnalysisImages {
  base: string[];
  heat: string[];
  box: string[];
}

interface DicomMetadataItem {
  file_id: string;
  original_filename: string;
  metadata: {
    modality?: string;
    body_part?: string;
    study_description?: string;
    series_description?: string;
    protocol_name?: string;
    shape?: number[];
    pixel_spacing_mm?: number[];
    slice_thickness_mm?: number;
    window_center?: number;
    window_width?: number;
    age?: string;
    sex?: string;
    patient_ref?: string;
  };
}

interface AnalysisResponse {
  analysis_id: string;
  patient_id: string;
  visit_id: string;
  appointment_id: string;
  status: Exclude<AnalysisStatus, 'waiting_for_files'>;
  queue_position: number | null;
  attempt: number;
  model_name: string | null;
  risk_tier: RiskTier;
  risk_status: RiskStatus;
  confidence: number | null;
  finding: string | null;
  interpretation: string | null;
  recommendation: string | null;
  predictions: unknown;
  error: string | null;
  images: AnalysisImages;
  image_file_ids: AnalysisImages;
  dicom: DicomMetadataItem[];
  dicom_metadata: DicomMetadataItem[]; // 하위 호환 alias
  expires_at: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}
```

권장 polling:

```ts
const TERMINAL = new Set(['done', 'failed', 'cancelled', 'superseded']);

async function pollAnalysis(analysisId: string) {
  while (true) {
    const analysis = await api.get<AnalysisResponse>(
      `/analyses/${analysisId}`,
    );
    if (TERMINAL.has(analysis.status)) return analysis;
    await new Promise(resolve => window.setTimeout(resolve, 2500));
  }
}
```

- 컴포넌트 unmount, 환자 변경, 로그아웃 시 polling을 취소한다.
- 네트워크 오류에는 즉시 무한 재시도하지 말고 backoff를 적용한다.
- `latest_analysis_id`가 null이면 polling하지 않는다.
- 날짜별 목록도 주기적으로 다시 조회하여 좌측 상태를 갱신한다.

---

## 11. 결과 이미지와 서명 URL

`AnalysisResponse.images`는 브라우저가 바로 읽을 수 있는 **상대 서명 URL**이다.

실제 파일 endpoint:

```http
GET /files/{file_id}?exp=<unix-seconds>&sig=<hmac>
```

이 URL은 서버가 발급하며 클라이언트가 `exp`나 `sig`를 직접 만들지 않는다.

```json
{
  "images": {
    "base": ["/files/F-...?exp=...&sig=..."],
    "heat": ["/files/F-...?exp=...&sig=..."],
    "box": ["/files/F-...?exp=...&sig=..."]
  },
  "image_file_ids": {
    "base": ["F-..."],
    "heat": ["F-..."],
    "box": ["F-..."]
  },
  "expires_at": "2026-07-29T15:00:00+00:00"
}
```

반드시 backend base URL을 붙여 사용한다.

```ts
export function toBackendUrl(pathOrUrl: string): string {
  if (/^https?:\/\//i.test(pathOrUrl)) return pathOrUrl;
  return new URL(pathOrUrl, API_BASE_URL).toString();
}
```

서명 URL GET에는 Bearer 헤더가 필요하지 않으므로 현재 CSS
`background-image: url(...)` 구조를 유지할 수 있다.

URL 만료 시 분석 전체 URL을 한 번에 갱신한다.

```http
POST /analyses/{analysis_id}/access-urls
Authorization: Bearer <access_token>
```

응답:

```ts
interface AnalysisAccessUrlsResponse {
  images: AnalysisImages;
  image_file_ids: AnalysisImages;
  expires_at: string;
}
```

`expires_at` 약 30초 전 또는 이미지 `401` 발생 시 한 번 갱신한다. 파일별로
갱신 API를 반복 호출하지 않는다.

### Canvas 주석 저장

`drawImage()` 후 `toDataURL()`을 사용하는 이미지는 `src`보다 먼저
`crossOrigin`을 설정해야 한다.

```ts
const image = new Image();
image.crossOrigin = 'anonymous';
image.src = toBackendUrl(signedUrl);
```

순서가 반대면 cross-origin canvas가 오염되어 `toDataURL()`에서
`SecurityError`가 발생할 수 있다.

서명 URL 전체를 console, analytics, 오류 수집 서비스에 기록하지 않는다.

### DICOM 헤더 메타데이터

`AnalysisResponse.dicom`에 분석 입력 DICOM의 비식별 헤더가 포함된다.
`dicom_metadata`는 초기 연동 버전과의 하위 호환 alias이며 신규 클라이언트는
`dicom`을 사용한다.
상세 DTO와 화면 매핑은
[`client-medical-metadata-rendering-contract.md`](./client-medical-metadata-rendering-contract.md)를
우선하고, 기존 DICOM 하위 호환 계약은
[`client-dicom-metadata-api.md`](./client-dicom-metadata-api.md)를 참고한다.
환자 이름, 원본 생년월일, 원본 Patient ID는 노출하지 않는다.
`patient_ref`는 원본 Patient ID의 단방향 축약 해시다.

개별 파일 메타데이터:

```http
GET /files/{file_id}/metadata
Authorization: Bearer <access_token>
```

파일 본문용 서명 URL과 달리 메타데이터 API에는 Bearer 인증이 필요하다.

---

## 12. 환자 메모

```http
GET /patients/{patient_id}/notes
Authorization: Bearer <access_token>
```

```http
POST /patients/{patient_id}/notes
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "text": "흉부 CT 추가 확인 예정",
  "visit_id": "V-20260729-0045"
}
```

`visit_id`는 선택이며, 지정하면 해당 환자의 방문인지 서버가 검증한다.

```ts
interface ClinicalNote {
  note_id: string;
  patient_id: string;
  visit_id: string | null;
  author_id: string;
  author_name: string;
  source: 'registration' | 'clinical';
  text: string;
  created_at: string;
}
```

---

## 13. 임상 채팅

채팅은 현재 선택한 방문 화면에 저장되지만, Agent에는 같은 환자의 다른 방문
분석 결과와 이전 대화도 함께 전달된다.

클라이언트의 Markdown·이미지 토큰 렌더링 세부 요구사항은
[`client-clinical-chat-requirements.md`](./client-clinical-chat-requirements.md)를
따른다.

```http
GET /visits/{visit_id}/chat
Authorization: Bearer <access_token>
```

해당 방문 화면에 저장된 메시지를 시간순으로 반환한다.

```http
POST /visits/{visit_id}/chat
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "content": "폐렴으로 의심되는 위치를 영상과 같이 설명해줘"
}
```

응답:

```ts
type ChatRole = 'user' | 'assistant';
type ChatImageRole = 'base' | 'heat' | 'box';

interface ChatImageReference {
  token: string;       // 예: AN-20260729-0045:heat:0
  analysis_id: string;
  file_id: string;
  role: ChatImageRole;
  slice_index: number;
  url: string;         // 상대 서명 URL
  expires_at: string;
}

interface ChatMessage {
  message_id: string;
  patient_id: string;
  visit_id: string;
  role: ChatRole;
  content: string;
  context_analysis_ids: string[];
  images: ChatImageReference[];
  created_at: string;
}

interface ChatExchange {
  user: ChatMessage;
  assistant: ChatMessage;
}
```

Assistant 본문에는 관련 영상을 놓을 위치에 다음 토큰이 들어간다.

```md
우하엽의 강조 부위를 확인하세요.

[IMG:AN-20260729-0045:heat:0]

분류 결과와 함께 비교하면...
```

클라이언트는 `content`를 `[IMG:<token>]` 단위로 나눈 뒤, 같은 `token`을 가진
`images[]` 항목의 `url`을 그 위치에 렌더링한다. URL에는 backend base URL을
붙인다. 현재 채팅 UI가 마크다운 이미지를 직접 지원하지 않더라도 이 토큰
매핑 방식이면 텍스트 중간에 기존 이미지 뷰어를 삽입할 수 있다.

`GET /visits/{visit_id}/chat`을 다시 호출하면 이미지 URL이 새로 발급되므로,
과거 메시지의 URL 만료값을 영구 저장하지 않는다. 보존 파일이 사라진 과거
메시지는 텍스트는 반환되지만 `images`가 빈 배열일 수 있다.

---

## 14. 오류 처리

기본 오류 응답:

```json
{
  "detail": "오류 설명"
}
```

Pydantic 검증 오류는 `detail` 배열이다.

| 상태 | 의미 | 클라이언트 동작 |
|---|---|---|
| `401` | 토큰 만료/폐기 또는 파일 서명 오류 | API는 refresh 1회, 파일은 URL 갱신 |
| `403` | 역할 부족 | 권한 안내 |
| `404` | 다른 병원 또는 없는 리소스 | 현재 화면 닫고 목록 갱신 |
| `409` | 예약 슬롯 충돌·상태 충돌 | 입력 유지 후 사용자에게 안내 |
| `413` | 파일 크기 초과 | 파일 선택 유지 후 크기 안내 |
| `415` | 지원하지 않는 파일 형식 | 허용 확장자 안내 |
| `422` | 필드·날짜·예약 규칙 위반 | 해당 입력 필드 안내 |

슬롯 충돌은 다음처럼 객체형 detail일 수 있다.

```json
{
  "detail": {
    "message": "슬롯 충돌",
    "conflict": {
      "doctor": {
        "employee_id": "chest01",
        "name": "김체스트 교수"
      },
      "scheduled_at": "2026-07-30T08:00:00+09:00"
    }
  }
}
```

---

## 15. 권장 API wrapper

```ts
let refreshPromise: Promise<void> | null = null;

async function refreshOnce(): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const refreshToken = sessionStorage.getItem('refresh_token');
      if (!refreshToken) throw new Error('No refresh token');

      const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!response.ok) throw new Error('Refresh failed');

      const tokens = await response.json();
      sessionStorage.setItem('access_token', tokens.access_token);
      sessionStorage.setItem('refresh_token', tokens.refresh_token);
      sessionStorage.setItem('doctor', JSON.stringify(tokens.doctor));
    })().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
  retried = false,
): Promise<Response> {
  const accessToken = sessionStorage.getItem('access_token');
  const headers = new Headers(init.headers);
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (response.status === 401 && !retried && !path.startsWith('/auth/')) {
    await refreshOnce();
    return apiFetch(path, init, true);
  }
  return response;
}
```

`FormData` 요청에는 이 wrapper가 `Content-Type: application/json`을 자동으로
붙이지 않도록 주의한다.

---

## 16. 클라이언트 작업 체크리스트

- [ ] 로그인·회원가입을 실제 `/auth` API로 교체
- [ ] access/refresh token과 doctor를 `sessionStorage`에 저장
- [ ] refresh 동시 요청을 하나로 직렬화
- [ ] 로그아웃 시 서버 세션 폐기 후 로컬 토큰 제거
- [ ] 하드코딩된 `TODAY` 제거
- [ ] 데모 환자 배열을 `GET /patients?date=`로 교체
- [ ] 검색 seam을 `GET /patients/search?q=`로 교체
- [ ] `kind: "new"`를 `POST /patients`에 연결
- [ ] `kind: "revisit"`를 `POST /patients/{id}/appointments`에 연결
- [ ] `files`가 있으면 별도 체크박스 없이 항상 함께 전송
- [ ] 배열 위치 `idx` 대신 `patient_id`를 React 식별자로 사용
- [ ] 방문 이력을 `/patients/{id}/visits`로 교체
- [ ] `latest_analysis_id`를 이용해 분석 polling
- [ ] `waiting_for_files`, `queued`, `analyzing`, `failed`, `done` 표시
- [ ] `risk_status=unavailable`을 “미분류”로 표시
- [ ] `images` 상대 URL에 backend base URL 적용
- [ ] 만료 전 `access-urls` 배치 갱신
- [ ] `new Image()`에서 `crossOrigin`을 `src`보다 먼저 설정
- [ ] 예약 취소·정정·완료를 `PATCH /appointments/{id}`에 연결
- [ ] 환자 관리 상태를 `PATCH /patients/{id}`에 연결
- [ ] 분석의 `dicom`을 영상 정보 영역에 표시
- [ ] 환자 메모를 `GET/POST /patients/{id}/notes`에 연결
- [ ] 채팅을 `GET/POST /visits/{visit_id}/chat`에 연결
- [ ] Assistant의 `[IMG:token]`을 `images[].url`로 인라인 렌더링
- [ ] 채팅 이력 재조회 시 새로 발급된 이미지 URL로 교체

---

## 17. 아직 별도 연동이 필요한 기능

다음 기능은 이 문서 범위의 API에 아직 포함되지 않았다.

- Admin 전용 사용자 관리 화면

현재 기존 `/projects`, `/inference`, `/pipeline`도 Bearer 인증이 필요하고
`/admin`은 `admin` 역할이 필요하다.
