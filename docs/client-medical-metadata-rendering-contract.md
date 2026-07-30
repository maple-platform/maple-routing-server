# 의료 메타데이터 백엔드 주도 렌더링 계약

> 대상: `maple-client` 영상 정보·임상 정보 패널  
> 적용 파일: DICOM, NIfTI  
> 이 문서는 기존 DICOM 고정 필드 UI 요구사항을 대체한다.
> 문서 상태: 신규 목표 계약. 현재 운영 응답의 `dicom`은 전환 기간 동안 유지된다.

## 1. 변경 목적

프론트에서 다음과 같은 고정 필드를 직접 정의하지 않는다.

```text
모달리티
촬영 장비
시리즈
슬라이스 두께
FOV
Recon Kernel
kVp / mAs
```

파일 종류와 실제 메타데이터에 따라 표시 가능한 값이 달라지기 때문이다.

변경 후에는 백엔드가 다음 정보를 완성해서 반환한다.

- 섹션 종류와 제목
- 섹션 표시 순서
- 항목 라벨
- 사용자에게 보여줄 최종 문자열
- 데이터 출처
- 관련 파일 ID

프론트는 응답 배열을 순서대로 반복 렌더링한다. 새로운 메타데이터 항목이
추가돼도 프론트 코드에 필드 대응표를 추가하지 않는다.

## 2. 핵심 정책

1. **신체 정보가 항상 첫 번째 섹션이다.**
2. 백엔드는 값이 실제로 존재하는 항목만 반환한다.
3. 프론트는 누락된 항목을 임의로 생성하거나 `—` 행으로 추가하지 않는다.
4. 백엔드가 만든 `display_value`를 프론트가 다시 포맷하지 않는다.
5. DICOM과 NIfTI는 동일한 `medical_metadata.sections` 계약을 사용한다.
6. 원본 의료 파일의 모든 태그를 그대로 노출하지 않는다.
7. 비식별 화이트리스트를 통과한 항목만 표시 계약에 포함한다.
8. DICOM 헤더 값으로 환자 DB의 기본정보를 자동 변경하지 않는다.

## 3. 응답 계약

```ts
type MetadataSource =
  | 'patient'
  | 'dicom'
  | 'nifti'
  | 'derived'
  | 'clinical';

type MetadataSectionScope = 'patient' | 'file';

interface MedicalMetadataItem {
  key: string;
  label: string;
  display_value: string;
  source: MetadataSource;

  // 선택: 단위가 별도 의미를 가질 때만 제공
  unit?: string;
}

interface MedicalMetadataSection {
  key: string;
  title: string;
  order: number;
  scope: MetadataSectionScope;

  // 환자 공통 섹션이면 null
  file_id: string | null;
  filename: string | null;
  file_type: 'dicom' | 'nifti' | null;

  items: MedicalMetadataItem[];
}

interface MedicalMetadataPresentation {
  schema_version: '1.0';
  sections: MedicalMetadataSection[];
}
```

분석 관련 응답에 다음 필드가 추가되는 것을 목표 계약으로 한다.

```ts
interface AnalysisResponse {
  // 기존 분석 필드 생략
  medical_metadata: MedicalMetadataPresentation;
}
```

## 4. 전체 응답 예시

```json
{
  "medical_metadata": {
    "schema_version": "1.0",
    "sections": [
      {
        "key": "patient",
        "title": "신체 정보",
        "order": 10,
        "scope": "patient",
        "file_id": null,
        "filename": null,
        "file_type": null,
        "items": [
          {
            "key": "birth_date",
            "label": "생년월일",
            "display_value": "2000-04-11",
            "source": "patient"
          },
          {
            "key": "age",
            "label": "나이",
            "display_value": "만 26세",
            "source": "patient"
          },
          {
            "key": "sex",
            "label": "성별",
            "display_value": "여",
            "source": "patient"
          }
        ]
      },
      {
        "key": "image:F-28b04973",
        "title": "영상 정보",
        "order": 20,
        "scope": "file",
        "file_id": "F-28b04973",
        "filename": "sample_02.dcm",
        "file_type": "dicom",
        "items": [
          {
            "key": "modality",
            "label": "모달리티",
            "display_value": "CR",
            "source": "dicom"
          },
          {
            "key": "shape",
            "label": "해상도",
            "display_value": "1024 × 1024",
            "source": "dicom"
          },
          {
            "key": "pixel_spacing",
            "label": "Pixel Spacing",
            "display_value": "0.143 × 0.143 mm",
            "source": "dicom"
          },
          {
            "key": "fov",
            "label": "FOV",
            "display_value": "146.4 × 146.4 mm",
            "source": "derived"
          }
        ]
      },
      {
        "key": "acquisition:F-28b04973",
        "title": "촬영 정보",
        "order": 30,
        "scope": "file",
        "file_id": "F-28b04973",
        "filename": "sample_02.dcm",
        "file_type": "dicom",
        "items": [
          {
            "key": "series",
            "label": "시리즈",
            "display_value": "view: PA",
            "source": "dicom"
          }
        ]
      }
    ]
  }
}
```

원본 파일에 촬영 장비, 슬라이스 두께, Recon Kernel, kVp/mAs가 없다면 해당
항목은 응답에 포함되지 않는다.

## 5. 섹션 순서

백엔드는 다음 순서로 `sections`를 내려준다.

| 순서 | key | 기본 제목 | 내용 |
|---:|---|---|---|
| 10 | `patient` | 신체 정보 | 환자 DB 기본정보 및 보유 중인 신체 정보 |
| 20 | `image:{file_id}` | 영상 정보 | 형식, 크기, spacing, orientation 등 |
| 30 | `acquisition:{file_id}` | 촬영 정보 | 장비, 시리즈, 촬영 조건 등 |
| 40 | `technical:{file_id}` | 기술 정보 | affine, dtype, intensity 등 |
| 50 | `clinical` | 임상 정보 | 별도 임상 측정값이 실제로 저장된 경우 |

배열 순서가 최종 표시 순서다. `order`는 디버깅과 병합을 위한 안정적인
우선순위 값이며, 프론트가 임의로 다른 순서로 재정렬하지 않는다.

## 6. 신체 정보 정책

신체 정보는 의료 파일보다 환자 DB 값을 우선한다.

가능한 항목:

| key | 라벨 | 우선 출처 |
|---|---|---|
| `birth_date` | 생년월일 | 환자 DB |
| `age` | 나이 | 환자 DB 생년월일에서 계산 |
| `sex` | 성별 | 환자 DB |
| `height` | 키 | 임상 DB, 없으면 DICOM |
| `weight` | 몸무게 | 임상 DB, 없으면 DICOM |

혈압, 맥박, 체온, 산소포화도는 별도 입력 또는 EMR 연동 데이터가 있을 때만
포함한다. 현재 데이터가 없다면 백엔드는 해당 행을 만들지 않는다.

DICOM의 age/sex와 환자 DB 값이 달라도 환자 DB를 자동 수정하지 않는다.
필요하면 별도의 불일치 경고 항목을 내려준다.

```json
{
  "key": "demographic_warning",
  "label": "정보 확인",
  "display_value": "DICOM 헤더와 환자 정보가 다릅니다.",
  "source": "derived"
}
```

## 7. DICOM에서 표시 가능한 항목

원본 태그가 있을 때만 포함한다.

### 영상 정보

- 파일 형식
- 모달리티
- Body Part
- 해상도
- Pixel Spacing
- 계산 FOV
- 슬라이스 두께
- Window Center / Width

### 촬영 정보

- 제조사 및 장비 모델
- Study / Series / Protocol
- 촬영 일시
- Reconstruction Kernel
- kVp
- mAs
- MR TR / TE

CR/X-ray에는 슬라이스 두께나 Recon Kernel이 없는 것이 정상이다. CT/MR도
원본 헤더에 태그가 없으면 반환하지 않는다.

## 8. NIfTI에서 표시 가능한 항목

NIfTI도 같은 섹션 계약을 사용한다.

```json
{
  "key": "image:F-nifti",
  "title": "영상 정보",
  "order": 20,
  "scope": "file",
  "file_id": "F-nifti",
  "filename": "brain_t1.nii.gz",
  "file_type": "nifti",
  "items": [
    {
      "key": "format",
      "label": "파일 형식",
      "display_value": "NIfTI",
      "source": "nifti"
    },
    {
      "key": "shape",
      "label": "볼륨 크기",
      "display_value": "240 × 240 × 155",
      "source": "nifti"
    },
    {
      "key": "voxel_spacing",
      "label": "Voxel Spacing",
      "display_value": "1 × 1 × 1 mm",
      "source": "nifti"
    },
    {
      "key": "orientation",
      "label": "Orientation",
      "display_value": "RAS",
      "source": "nifti"
    }
  ]
}
```

기술 정보에 포함할 수 있는 값:

- affine matrix
- 데이터 타입
- 차원 수
- intensity 범위

NIfTI에는 일반적으로 촬영 장비, kVp/mAs, 환자 생년월일, 활력징후가 없다.
따라서 해당 섹션이나 항목은 생성하지 않는다.

## 9. 여러 의료 파일 처리

한 분석에 DICOM 또는 NIfTI가 여러 개 있으면 파일별 섹션을 반복한다.

```text
신체 정보
영상 정보 · chest_01.dcm
촬영 정보 · chest_01.dcm
영상 정보 · chest_02.dcm
촬영 정보 · chest_02.dcm
```

프론트는 `section.file_id`와 `section.filename`을 이용해 파일 선택 UI 또는
파일별 제목을 구성할 수 있다.

같은 파일의 섹션을 합치거나 서로 다른 파일의 값을 하나로 덮어쓰지 않는다.

## 10. 프론트 렌더링 요구사항

```tsx
function MedicalMetadataPanel({
  metadata,
}: {
  metadata: MedicalMetadataPresentation;
}) {
  return (
    <>
      {metadata.sections.map(section => (
        <MetadataSection
          key={section.key}
          title={section.title}
          filename={section.filename}
        >
          {section.items.map(item => (
            <MetadataRow
              key={item.key}
              label={item.label}
              value={item.display_value}
            />
          ))}
        </MetadataSection>
      ))}
    </>
  );
}
```

프론트에서 하지 않을 작업:

- `if (dicom)`, `if (nifti)`로 필드 목록 분기
- DICOM 태그명과 UI 라벨 대응표 관리
- FOV, 나이, 단위 문자열 재계산
- 값이 없는 고정 행 생성
- 섹션 순서 재정의
- DICOM 값으로 환자 상태 변경

## 11. 데이터가 없을 때

`medical_metadata.sections`가 빈 배열이면 메타데이터 패널 전체를 숨기거나
“표시할 메타데이터가 없습니다”를 표시한다.

섹션은 존재하지만 `items`가 비어 있는 응답은 백엔드 오류로 간주한다.
백엔드는 빈 섹션을 내려주지 않는다.

## 12. 개인정보

백엔드는 원본 파일의 다음 값을 표시 계약에 포함하지 않는다.

- Patient Name
- 원본 Patient ID
- 원본 Patient Birth Date
- Institution 내부 식별자
- Referring Physician 등 직접 식별 가능 정보

원본 메타데이터 객체를 재귀적으로 그대로 렌더링하면 안 된다.
반드시 `medical_metadata.sections[].items`만 화면에 표시한다.

## 13. API 적용 위치

다음 응답에 `medical_metadata`를 포함하는 것을 목표로 한다.

| API | 위치 |
|---|---|
| `GET /patients?date=` | `patients[].medical_metadata` |
| `GET /patients/{patient_id}` | 최상위 |
| `GET /patients/{patient_id}/appointments` | 각 예약 |
| `GET /patients/{patient_id}/visits` | 각 방문 |
| `GET /patients/{patient_id}/visits/{visit_id}` | `analyses[]` |
| `GET /visits/{visit_id}/analyses` | 각 분석 |
| `GET /analyses/{analysis_id}` | 최상위 |
| `GET /files/{file_id}/metadata` | 최상위 |

목록 성능에 문제가 생기면 날짜별 환자 목록에는 신체 정보와 대표 영상 정보만
포함하고, 상세 화면에서 전체 섹션을 반환할 수 있다. 이 경우에도 DTO 형태는
동일하게 유지한다.

## 14. 전환 정책

백엔드 전환 기간:

1. 기존 `dicom`, `dicom_metadata`를 유지한다.
2. 새 `medical_metadata`를 함께 반환한다.
3. 프론트는 `medical_metadata`를 우선 사용한다.
4. 새 UI 검수가 끝나면 기존 DICOM 고정 필드 렌더링을 제거한다.
5. 기존 필드 삭제는 별도 공지 후 진행한다.

프론트 임시 선택:

```ts
if (response.medical_metadata?.sections?.length) {
  renderBackendDrivenMetadata(response.medical_metadata);
} else {
  renderLegacyMetadata(response.dicom);
}
```

이 fallback은 전환 기간에만 사용한다. 새 필드 목록을 프론트에 다시
하드코딩하지 않는다.

## 15. 완료 조건

- [ ] 신체 정보가 항상 가장 먼저 표시된다.
- [ ] 프론트가 `sections`와 `items`를 순서대로 반복 렌더링한다.
- [ ] 프론트에 DICOM/NIfTI별 고정 필드 배열이 없다.
- [ ] `display_value`를 그대로 표시한다.
- [ ] 값이 없는 항목은 행 자체가 나타나지 않는다.
- [ ] 여러 파일의 메타데이터가 서로 덮어써지지 않는다.
- [ ] DICOM과 NIfTI가 동일한 컴포넌트를 사용한다.
- [ ] 원본 메타데이터 객체를 그대로 화면에 출력하지 않는다.
- [ ] 환자 DB 값과 DICOM 값이 달라도 자동 수정하지 않는다.
- [ ] 메타데이터가 없을 때 빈 상태가 정상 표시된다.
