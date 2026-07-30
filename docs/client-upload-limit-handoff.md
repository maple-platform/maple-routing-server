# 클라이언트 전달사항 — 의료 파일 업로드 제한

## 변경 이유

현재 클라이언트의 100 MiB 사전 검증 때문에
`VerSe_Vertebrae_CT-sample_ct.nii.gz`(110,889,317 bytes, 105.75 MiB)가
서버 요청 전에 차단됩니다. 현재 모델 샘플 중에는 약 503 MiB인 NIfTI도
있으므로 파일 한 건의 제한을 **512 MiB**로 통일했습니다.

## 서버 정책 조회

로그인 후 다음 API를 한 번 호출하고 세션 동안 캐시하세요.

```http
GET /config/client
Authorization: Bearer {access_token}
```

```json
{
  "upload": {
    "max_file_bytes": 536870912,
    "max_file_mib": 512,
    "allowed_extensions": [
      ".dcm",
      ".dicom",
      ".nii",
      ".nii.gz",
      ".csv",
      ".png",
      ".jpg",
      ".jpeg"
    ]
  }
}
```

## 프론트 변경 요구

- 기존에 하드코딩된 `100 * 1024 * 1024` 제한을 제거합니다.
- 선택한 각 파일의 `file.size`를 `upload.max_file_bytes`와 비교합니다.
- `.nii.gz`는 `path.extname()` 한 번으로 판별하지 말고 파일명 전체의
  소문자 suffix로 허용 여부를 판단합니다.
- 설정 API 호출이 실패한 경우의 임시 fallback은 `536870912`로 둡니다.
- 제한 초과 문구는 `파일당 최대 512 MiB까지 첨부할 수 있습니다.`로
  표시합니다.
- 서버가 `413`을 반환하면 응답의 `detail`을 사용자에게 표시하고 선택한
  파일은 유지합니다.

예시:

```ts
const FALLBACK_MAX_FILE_BYTES = 512 * 1024 * 1024;

const maxFileBytes =
  clientConfig?.upload.max_file_bytes ?? FALLBACK_MAX_FILE_BYTES;

const oversized = files.find((file) => file.size > maxFileBytes);
if (oversized) {
  setError(
    `파일당 최대 ${Math.floor(maxFileBytes / 1024 / 1024)} MiB까지 첨부할 수 있습니다.`,
  );
  return;
}
```

## 완료 조건

- 105.75 MiB `VerSe_Vertebrae_CT-sample_ct.nii.gz`가 선택 단계에서
  차단되지 않는다.
- 환자 등록 요청이 전송되고 서버가 파일을 저장한 뒤 분석 대기열에 넣는다.
- 512 MiB를 초과하는 단일 파일만 클라이언트에서 차단한다.
- 클라이언트와 서버의 제한값이 `/config/client` 응답과 일치한다.

서버는 업로드 본문을 1 MiB 단위로 받아 임시 파일과 GridFS로 스트리밍하므로,
512 MiB 파일 전체를 애플리케이션 메모리에 올리지 않습니다.
