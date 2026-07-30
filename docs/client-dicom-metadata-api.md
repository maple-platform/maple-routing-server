# DICOM 메타데이터 API 프론트엔드 전달서

> 기준 서버: Maple Routing Server `:8100`  
> 인증: JWT Bearer  
> 표준 응답 필드: `dicom`

> **전환 안내:** DICOM/NIfTI 공통 백엔드 주도 렌더링 계약은
> [`client-medical-metadata-rendering-contract.md`](./client-medical-metadata-rendering-contract.md)를
> 우선한다. 이 문서는 기존 `dicom` API의 하위 호환 명세로 유지한다.

## 1. 요약

DICOM을 첨부해 생성한 분석은 비식별 DICOM 헤더를 `dicom` 배열로 반환한다.

```ts
interface DicomFileSummary {
  file_id: string;
  original_filename: string;
  metadata: DicomMetadata;
}
```

- DICOM이 없으면 `dicom: []`이다.
- DICOM이 여러 개면 업로드 순서대로 여러 항목이 들어간다.
- 신규 프론트엔드는 `dicom`을 사용한다.
- `dicom_metadata`는 초기 연동 버전용 alias다. 내용은 `dicom`과 동일하며
  새 코드에서는 사용하지 않는다.

## 2. 메타데이터가 포함되는 기존 API

다음 API는 별도 요청 없이 응답에 `dicom`을 포함한다.

| API | `dicom` 위치 |
|---|---|
| `GET /patients?date=YYYY-MM-DD` | `patients[].dicom` |
| `GET /patients/{patient_id}` | 최상위 `dicom` |
| `GET /patients/{patient_id}/appointments` | 각 예약의 `dicom` |
| `GET /patients/{patient_id}/visits` | 각 방문의 `dicom` |
| `GET /patients/{patient_id}/visits/{visit_id}` | `analyses[].dicom` |
| `GET /visits/{visit_id}/analyses` | 각 분석의 `dicom` |
| `GET /analyses/{analysis_id}` | 최상위 `dicom` |

모든 요청에는 다음 헤더가 필요하다.

```http
Authorization: Bearer <access_token>
```

환자 화면에서 이미 분석 응답을 받았다면 개별 파일 메타데이터 API를 다시
호출할 필요가 없다.

## 3. 실제 분석 응답 예시

```json
{
  "analysis_id": "AN-20260730-0001",
  "status": "done",
  "dicom": [
    {
      "file_id": "F-28b04973-5de0-49d0-94bd-4cc532f6a711",
      "original_filename": "sample_02.dcm",
      "metadata": {
        "modality": "CR",
        "body_part": "CHEST",
        "series_description": "view: PA",
        "shape": [1024, 1024],
        "pixel_spacing_mm": [0.143, 0.143],
        "age": "075Y",
        "sex": "M",
        "patient_ref": "be8bcfa9dd1f"
      }
    }
  ]
}
```

## 4. 개별 파일 메타데이터 API

분석 응답에서 받은 `dicom[].file_id`로 개별 파일 정보를 조회할 수 있다.

```http
GET /files/{file_id}/metadata
Authorization: Bearer <access_token>
```

예:

```http
GET /files/F-28b04973-5de0-49d0-94bd-4cc532f6a711/metadata
```

실제 응답:

```json
{
  "file_id": "F-28b04973-5de0-49d0-94bd-4cc532f6a711",
  "patient_id": "PT-1047",
  "visit_id": "V-20260730-0001",
  "analysis_id": "AN-20260730-0001",
  "kind": "input",
  "role": "source",
  "original_filename": "sample_02.dcm",
  "content_type": "application/octet-stream",
  "extension": "dcm",
  "size_bytes": 132448,
  "dicom_metadata": {
    "modality": "CR",
    "body_part": "CHEST",
    "series_description": "view: PA",
    "shape": [1024, 1024],
    "pixel_spacing_mm": [0.143, 0.143],
    "age": "075Y",
    "sex": "M",
    "patient_ref": "be8bcfa9dd1f"
  }
}
```

이 API의 `dicom_metadata`는 해당 파일 하나의 헤더 객체다. 분석 응답의
`dicom` 배열 및 하위 호환 alias와 형태가 다르므로 주의한다.

## 5. TypeScript 타입

모든 DICOM 태그는 원본 파일에 따라 없을 수 있다. `metadata` 내부 필드는
전부 optional로 처리한다.

```ts
export interface DicomMetadata {
  modality?: string;
  body_part?: string;
  study_description?: string;
  series_description?: string;
  protocol_name?: string;

  // [rows, columns]
  shape?: [number, number] | number[];

  // [row spacing, column spacing], millimetres
  pixel_spacing_mm?: [number, number] | number[];
  slice_thickness_mm?: number;
  window_center?: number;
  window_width?: number;

  mr_params?: {
    TR?: number;
    TE?: number;
  };

  // 비식별 인구학 정보
  age?: string;       // 예: "075Y", 90세 이상은 "90+"
  sex?: string;       // 일반적으로 "M", "F", "O"
  weight_kg?: number;
  height_m?: number;

  // 원본 Patient ID의 단방향 축약 해시
  patient_ref?: string;
}

export interface DicomFileSummary {
  file_id: string;
  original_filename: string;
  metadata: DicomMetadata;
}

export interface MedicalFileMetadataResponse {
  file_id: string;
  patient_id: string;
  visit_id: string;
  analysis_id: string;
  kind: string;
  role: string;
  original_filename: string;
  content_type: string;
  extension: string;
  size_bytes: number;
  dicom_metadata: DicomMetadata | null;
}
```

## 6. 권장 화면 매핑

| UI 라벨 | 값 |
|---|---|
| Modality | `modality` |
| Body Part | `body_part` |
| Study | `study_description` |
| Series | `series_description` |
| Protocol | `protocol_name` |
| Resolution | `shape` → `1024 × 1024` |
| Pixel Spacing | `pixel_spacing_mm` → `0.143 × 0.143 mm` |
| Slice Thickness | `slice_thickness_mm` → `N mm` |
| Window | `window_center`, `window_width` |
| MR TR / TE | `mr_params.TR`, `mr_params.TE` |
| DICOM Age | `age` |
| DICOM Sex | `sex` |

표시 예시:

```ts
function formatPair(
  value: number[] | undefined,
  unit = '',
): string {
  if (!value?.length) return '—';
  return `${value.join(' × ')}${unit}`;
}

const resolution = formatPair(metadata.shape);
const pixelSpacing = formatPair(metadata.pixel_spacing_mm, ' mm');
```

필드가 없으면 빈 문자열이 아니라 `—` 또는 “정보 없음”을 표시한다.

## 7. 개인정보 및 정합성 주의사항

서버는 다음 직접 식별자를 반환하지 않는다.

- Patient Name
- 원본 Patient Birth Date
- 원본 Patient ID

`patient_ref`는 같은 DICOM Patient ID를 내부적으로 비교하기 위한 축약
해시다. 일반 사용자 화면에 환자번호처럼 표시하지 않는다.

`metadata.age`와 `metadata.sex`는 DICOM 파일 헤더 값이고, MAPLE 환자 DB의
`birth_date`, `gender`와 다른 출처다.

- DICOM 값을 이용해 환자 기본정보를 자동으로 덮어쓰지 않는다.
- 값이 다르면 “DICOM 헤더와 환자 정보가 다름” 정도의 경고만 표시한다.
- 최종 환자 기본정보의 정정은 별도 사용자 확인 절차를 거친다.

## 8. 파일 본문 API와의 차이

메타데이터 조회:

```http
GET /files/{file_id}/metadata
Authorization: Bearer <access_token>
```

분석 결과 이미지 본문 조회:

```http
GET /files/{file_id}?exp=<unix-seconds>&sig=<hmac>
```

- `/metadata`는 Bearer 인증 방식이다.
- 파일 본문은 서버가 발급한 서명 URL 방식이다.
- `file_id`만 붙여 `/files/{file_id}`를 호출하면 안 된다.
- 메타데이터 API가 파일 본문 URL을 발급하지는 않는다.

## 9. 오류 처리

| 상태 | 의미 | 프론트 처리 |
|---|---|---|
| `401` | access token 없음·만료 | refresh 후 1회 재시도 |
| `404` | 파일 없음 또는 다른 병원 파일 | 메타데이터 영역을 닫고 목록 갱신 |
| `422` | 잘못된 경로·파라미터 | 요청 구성 오류로 처리 |

## 10. 완료 조건

- [ ] 신규 코드는 `dicom`을 사용하고 `dicom_metadata` alias에 의존하지 않는다.
- [ ] `dicom: []`일 때 화면이 깨지지 않는다.
- [ ] 여러 DICOM 파일을 배열 순서대로 선택할 수 있다.
- [ ] optional 필드가 없을 때 `—`를 표시한다.
- [ ] `shape`, `pixel_spacing_mm`의 두 값을 `×`로 구분해 표시한다.
- [ ] `patient_ref`를 환자번호로 노출하지 않는다.
- [ ] DICOM age/sex로 환자 DB 값을 자동 변경하지 않는다.
- [ ] 개별 `/metadata` 요청에 Bearer 토큰을 보낸다.
- [ ] 파일 본문 요청과 메타데이터 요청의 인증 방식을 혼동하지 않는다.
