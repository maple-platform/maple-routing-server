# 입력 정규화 레이어 설계 (Input Normalization Layer)

> **상태**: 설계 확정 — 구현 착수 기준선
> **브랜치**: `main → dev → input`
> **동시 작업**: `maple-agent-server` (계약 §3 공유)
> **작성일**: 2026-07-02

첨부파일 + 자연어 질의를 해석하는 에이전트를 위한 **입력 해석/정규화 레이어**의 설계 문서.
DICOM·NIfTI 같은 의료 데이터부터 PNG/JPG, (향후) 음성·동영상까지 모달리티 무관하게 수용한다.

---

## 1. 원칙

> **모든 첨부파일을 라우팅 서버가 "VLM이 소비 가능한 형태(이미지 + 텍스트 + 구조화 메타데이터)"로 환원해서 Agent에 넘긴다.**

근거 — 해석 Agent는 **VLM(gemma-4-31B-it)**이다. 픽셀과 토큰만 소비하며, DICOM 볼륨·wav·mp4를 직접 이해하지 못한다.
따라서 무거운 파싱/렌더링은 전부 라우팅 서버가 담당하고, Agent 서버는 순수 VLM 추론으로 유지한다 (의료/미디어 라이브러리 0).

이 원칙은 모달리티마다 동일하게 적용된다:
- 의료 영상 → 렌더링된 슬라이스 이미지 + 메타데이터
- 음성 → ASR 전사 **텍스트** (+ 스펙트로그램 옵션)
- 동영상 → 대표 **키프레임 이미지** + 오디오 트랙 전사 텍스트

---

## 2. 아키텍처 — 플러그인 핸들러 레지스트리

기존 `convert_files_for_general` ([services/inference_service.py](../services/inference_service.py))을 아래 구조로 재설계한다.

```python
class AttachmentHandler(Protocol):
    def can_handle(filename: str, content_type: str) -> bool: ...
    async def parse(file) -> NormalizedAttachment: ...   # 이미지 렌더 + 메타 (general)
    async def extract_metadata(file) -> dict: ...        # 메타만, 렌더 없이 (prediction interpret용, §3.3)

REGISTRY = [
    DicomHandler(),
    NiftiHandler(),
    ImageHandler(),
    CsvHandler(),
    PdfHandler(),
    # 향후 확장 (인터페이스만 선반영):
    # AudioHandler(),  VideoHandler()
]
```

### 확장 포인트

- **새 모달리티 = 핸들러 하나 추가**. 레지스트리에 등록하면 끝.
- `parse()`가 `async`인 이유 — 두 유형의 핸들러를 동일 인터페이스로 수용:
  - **자립형(self-contained)**: pydicom/nibabel 등 로컬 라이브러리로 직접 처리. (현재 5종)
  - **위임형(delegating)**: 무거운 처리를 외부 런타임 컨테이너에 HTTP로 위임. (향후 ASR = `runtime-asr` 호출)
- 덕분에 음성/동영상 처리 위치(라우팅 직접 vs 런타임 컨테이너)는 **지금 결정하지 않아도** 구조가 깨지지 않는다.

---

## 3. 크로스레포 계약 — `attachments[]`

`/agent/plan (mode=general)`의 기존 `images` 필드를 `attachments[]`로 확장한다.
**이 스키마가 `maple-routing-server`와 `maple-agent-server`의 유일한 접점이며, 동시 작업의 기준이다.**

```jsonc
{
  "mode": "general",
  "query": "이 영상 소견을 해석해줘",
  "attachments": [
    {
      "type": "dicom",                          // dicom | nifti | image | csv | pdf  (향후: audio | video)
      "filename": "brain_t1.dcm",
      "images": ["data:image/png;base64,..."],  // VLM이 볼 것. 없으면 []
      "text": "",                               // VLM이 읽을 것 (ASR 전사 / OCR / 문서 추출텍스트). 없으면 ""
      "metadata": {                             // 구조화·비식별화된 부가 정보
        "modality": "MR",
        "body_part": "BRAIN",
        "series_description": "T1 SE",
        "shape": [256, 256],
        "pixel_spacing_mm": [0.9, 0.9],
        "window_center": 40,
        "window_width": 400,
        "mr_params": { "TR": 500, "TE": 14 },
        "age": "045Y",
        "sex": "M"
      },
      "tabular": null                           // csv면 dict 리스트, 아니면 null
    }
  ]
}
```

### 필드 규약

| 필드 | 타입 | 의미 |
|---|---|---|
| `type` | string | 모달리티 식별자 |
| `filename` | string | 원본 파일명 |
| `images` | string[] | VLM 입력 이미지 (data URI). 없으면 `[]` |
| `text` | string | VLM 입력 텍스트 (전사/OCR/추출). 없으면 `""` |
| `metadata` | object | 구조화·비식별화 메타데이터 |
| `tabular` | array\|null | 표 데이터(dict 리스트). CSV 전용 |

> **agent-server 측 책임**: `images` + `text` + `metadata`(+`tabular`)를 VLM 프롬프트로 조립.

### 3.2 `uploaded_types` 병행 유지 (prediction / auto)

`attachments[]`는 **general 모드 전용**이다. prediction / auto 흐름은 지금도 파일 본문 없이 `uploaded_types: list[str]`(카테고리 dedupe 집합)만 보낸다 ([inference_controller.py:227-233](../controllers/inference_controller.py#L227), [:182-186](../controllers/inference_controller.py#L182)). 두 필드는 같은 요청에 공존하지 않으므로 충돌이 없다.

- **파일 타입 감지는 routing-server가 단일 소유.** agent-server는 `attachments[].type`에서 `uploaded_types`를 역산하지 않는다.
- `uploaded_types` = 모델 매칭·자동분류용 카테고리 집합 / `attachments[].type` = 파일별 리치 타입. 역할이 달라 병행한다.

### 3.3 `/agent/interpret` — 원본 스캔 메타 전달 (prediction)

**문제**: prediction 흐름은 `plan → (모델 실행) → interpret` 순인데, interpret은 모델 출력물(분할 오버레이 등)만 받고 원본 스캔의 나이·성별·모달리티·시퀀스가 유실된다. "8세 vs 68세 종양 소견"은 해석 문장이 달라야 하므로 이 정보가 필요하다.

**해결**: routing-server가 저장된 원본(`save_input_dir`)에서 **메타데이터만 추출**(`extract_metadata`, 이미지 렌더 없이)해 interpret의 `execution_context`에 실어 보낸다.

```jsonc
// /agent/interpret 의 execution_context 확장
"execution_context": {
  "mode": "prediction",
  "plan": { ... },
  "attachments_meta": [                    // 신규 — 원본 스캔 메타 (base64 이미지 없음, 텍스트만)
    { "type": "dicom", "filename": "brats_t1.dcm",
      "metadata": { "modality": "MR", "age": "008Y", "sex": "F", "body_part": "BRAIN" } }
  ]
}
```

- `attachments_meta`는 **이미지 없이 메타만** — interpret 페이로드를 가볍게 유지. 표시 이미지는 모델 출력물로 별도 전달됨.
- agent-server: `InterpretRequest` 스키마에 `execution_context.attachments_meta` 수용 필드 추가.

### 3.4 clinical 모드 — 첨부 없음

clinical 모드는 순수 지식 Q&A로, **첨부파일이 들어오지 않는다.** maple-client가 clinical에서 파일 첨부 UI를 원천 차단하며, routing / agent-server는 clinical 요청에 첨부가 없다고 가정한다. (maple-client 작업 항목)

---

## 4. 핸들러 스펙 (현재 구현 대상 5종)

| 타입 | images | text | metadata |
|---|---|---|---|
| **DICOM** | 태그 기반 windowing(WindowCenter/Width, VOI LUT, MONOCHROME1 반전) 적용 후 PNG. 3D 볼륨이면 **3면 대표 슬라이스** | — | modality, body_part, series_description, pixel_spacing, slice_thickness, window_center/width, MR 파라미터(TR/TE), **나이·성별·체중** |
| **NIfTI** | **3면 대표 슬라이스**(axial / coronal / sagittal 중앙) | — | shape, voxel spacing(zooms), orientation(affine → RAS codes), dtype, intensity range |
| **PNG/JPG** | 이미지 그대로(필요시 다운스케일) | (OCR 옵션) | 해상도, EXIF(있으면) |
| **CSV** | (차트 렌더는 옵션) | 요약 통계 | 행·열 수, 컬럼 스키마 · `tabular`에 dict 리스트 |
| **PDF** | 페이지 이미지(옵션) | 추출 텍스트 | 페이지 수 |

### 3D 렌더링 정책

- 기존 "중앙 1슬라이스만" 방식([inference_service.py:414](../services/inference_service.py#L414)) 폐기.
- **3면 대표 슬라이스**(axial/coronal/sagittal 중앙)로 렌더링해 3D 구조 정보를 보존.
- 추후 질의 기반 슬라이스 선택이 필요하면, 라우팅 서버에 "N번 슬라이스 렌더" 엔드포인트를 추가하는 방식으로 확장 (원본을 Agent로 보내지 않음).

### 이미지 토큰 예산

vLLM `max-model-len = 8192`는 **입력(프롬프트+이미지) + 출력 총합의 하드 리밋**이다. 초과 시 에러 또는 이미지/텍스트 truncation → 해석 오류. 또한 Gemma 3 계열은 pan-and-scan으로 큰/비정방형 이미지를 여러 크롭(각 256토큰)으로 쪼개므로 이미지 토큰이 장당 고정이 아니다.

**정책 — 예산 인지형 (인위적 저상한 없음)**:
- 1~2 파일 등 일반 케이스는 **3면 전부 전송** (다 쓴다).
- routing이 토큰을 추정해 요청이 **천장을 넘길 때만** 파일당 axial 1면으로 자동 강등. 메타데이터는 항상 유지.
- **agent-server 레버**: Gemma 3는 128k까지 지원. B200 VRAM 여유가 있으면 `max-model-len`을 상향해 예산 자체를 넓힐 수 있음 (agent-server config 결정).

---

## 5. 비식별화 (De-identification)

원본 파일을 Agent로 보내지 않고 **우리가 선택한 태그만 추출**하므로, 별도 마스킹 단계 없이 **추출 화이트리스트**로 비식별화가 완결된다.

### 원칙: 인구학 정보(임상 유용) ≠ 직접 식별자

| 항목 | 임상 가치 | 식별 위험 | 처리 |
|---|---|---|---|
| **나이(Age)** | 높음 (골연령, 뇌위축 정상범위, 호발연령) | 낮음 | **유지** |
| **성별(Sex)** | 높음 | 없음 | **유지** |
| **체중(Weight)/신장(Size)** | 중간 (용량·정규화) | 낮음 | **유지** |
| 촬영 맥락(StudyDescription, BodyPart, Protocol) | 높음 | 낮음 | **유지** |
| 생년월일(DOB, 전체 날짜) | 나이로 대체 가능 | **높음(직접 식별자)** | 나이로 환산 후 폐기 |
| 환자명(PatientName) | 없음 | 매우 높음 | **폐기** |
| 환자ID/MRN | 없음 | 높음 | **익명 ID로 치환** |
| 기관명·의사명 | 없음 | 중간 | **폐기** |

### 규칙

- **나이**: `PatientAge (0010,1010)` 태그 우선 사용, 없으면 `PatientBirthDate + StudyDate`로 환산. DOB 원본은 폐기.
- **90세 이상 → `"90+"`로 비닝** (HIPAA Safe Harbor 관례: 고령은 준식별자).

---

## 6. Track B — 모델 입력 어댑터 (prediction 흐름)

해석 Agent 흐름과 **독립**된 별도 트랙. prediction 모드에서 원본 데이터는 모델 실행 서버로 가야 하며(모델은 원본 볼륨·비트뎁스·spacing 필요), 모델마다 요구 입력이 다르므로 라우팅 서버가 이를 맞춰준다.

현재: `run_inference`가 "첫 파일 경로 통째 전달 + 느슨한 카테고리 교집합 검증"만 수행 ([inference_service.py:191](../services/inference_service.py#L191)).

### 결정: (a) 선택 + 엄격검증부터

- **(a)** required_data에 맞는 올바른 파일을 선택해 경로 전달 + **타입/개수 엄격 검증**. 실제 전처리는 모델 컨테이너가 계속 담당.
- **(b) 포맷 변환**(예: DICOM 시리즈 → NIfTI stack)은 **요구하는 모델에 한해 케이스별로** 추가.
- (c) 완전 전처리(spacing 리샘플·정규화·방향정렬)는 과도 — 현재 모델들이 내부 전처리를 이미 하므로 채택 안 함.

---

## 7. 구현 스코프

### 지금 구현
- 플러그인 핸들러 레지스트리 골격
- 핸들러 5종: DICOM / NIfTI / PNG·JPG / CSV / PDF
- 비식별화 화이트리스트 추출
- `attachments[]` 계약 생성
- Track B (a): 선택 + 엄격검증

### 나중 (인터페이스만 선반영)
- **audio** 핸들러 (ASR 전사)
- **video** 핸들러 (키프레임 + 오디오 ASR)
- **ASR 처리 위치(B)**: 위임형 런타임 컨테이너(`runtime-asr`) 유력 — Agent에 라이브러리 미설치 원칙과 일관. 미확정.

---

## 8. 두 레포 분담

| 레포 | 책임 |
|---|---|
| **maple-routing-server** (이 브랜치) | 핸들러 레지스트리, 5개 핸들러, 비식별화, `attachments[]` 생성, Track B (a) |
| **maple-agent-server** (동시 작업) | `attachments[]` 수신 → `images` + `text` + `metadata`(+`tabular`)를 VLM 프롬프트로 조립 → 해석 |

---

## 부록 — 확정 원장

| # | 항목 | 결정 |
|---|---|---|
| 1 | 브랜치 | `main → dev → input` |
| 2 | 구조 | 모달리티 무관 정규화 레이어 + 플러그인 핸들러 레지스트리 |
| 3 | 계약 | `attachments[] = {type, filename, images, text, metadata, tabular}` |
| 4 | 렌더링 위치 | 라우팅 서버 (Agent는 순수 VLM, 라이브러리 0) |
| 5 | 3D 렌더 | 3면 대표 슬라이스 (중앙 1장 폐기) |
| 6 | windowing | DICOM 태그 기반 (VOI LUT, MONOCHROME1 반전) |
| 7 | 비식별화 | 화이트리스트 추출: 나이·성별·체중·촬영맥락 유지, 이름·DOB원본·원본ID 제거/치환, 90+ 비닝 |
| 8 | 구현 범위 | DICOM/NIfTI/PNG/JPG/CSV 지금, audio/video 인터페이스만 |
| 9 | Track B | (a) 선택+엄격검증 시작, 필요 모델만 (b) 포맷변환 추가 |
| 10 | ASR 위치 | 미정 — 위임형/자립형 둘 다 수용하는 인터페이스로 보류 |
| 11 | uploaded_types | prediction/auto용으로 병행 유지, 타입 감지는 routing 단일 소유 (§3.2) |
| 12 | interpret 메타 | `execution_context.attachments_meta`로 원본 스캔 메타 전달, 핸들러에 `extract_metadata()` 추가 (§3.3) |
| 13 | 이미지 토큰 | 예산 인지형 — 기본 3면 전송, 천장 초과시만 axial 강등. agent-server가 max-model-len 상향 가능 (§4) |
| 14 | clinical 첨부 | 프론트에서 원천 차단, 첨부 없다고 가정 (§3.4) |
| 15 | 모델 매칭(C) | body_part/modality 활용은 백로그 — A3 메타 추출 후 후속 |
