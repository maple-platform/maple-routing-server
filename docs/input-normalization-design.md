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
    async def parse(file) -> NormalizedAttachment: ...

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
| 10 | ASR 위치(B) | 미정 — 위임형/자립형 둘 다 수용하는 인터페이스로 보류 |
