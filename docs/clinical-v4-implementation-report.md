# Clinical Backend 요구사항 구현·검증 보고서

> 검증일: 2026-07-29  
> 검증 DB: `maple_db`  
> 검증 대상: 환자 46명, 분석 33건

## 1. DICOM 헤더

구현:

- 업로드 시 비식별 DICOM 헤더를 `medical_files.dicom_metadata`에 저장한다.
- 응답의 표준 `dicom` 키에 입력 DICOM 메타데이터를 포함한다.
- 초기 구현의 `dicom_metadata`는 하위 호환 alias로 함께 반환한다.
- 환자 날짜 목록, 환자 상세, 예약 이력, 방문 이력, 방문 상세, 분석 상세에서
  동일한 메타데이터를 확인할 수 있다.
- `GET /files/{file_id}/metadata`를 추가했다. Bearer 인증이 필요하다.
- 환자 이름, 원본 생년월일, 원본 Patient ID는 노출하지 않는다.
- 원본 Patient ID는 축약 해시 `patient_ref`로만 노출한다.

검증:

- 활성 DICOM 33건 중 메타데이터 저장 33건
- API 응답 키 예: `modality`, `body_part`, `series_description`, `shape`,
  `pixel_spacing_mm`, `age`, `sex`, `patient_ref`
- 직접 식별자 키 노출 0건

## 2. 이미지 토큰

구현:

- 백엔드가 레거시 Agent 역할을 다음 표준 역할로 정규화한다.
  - `original`, `segmentation_overlay` → `base`
  - `gradcam_overlay` → `heat`
  - `bbox_overlay`, detection 계열 → `box`
- `/inference`의 본문 `[IMG:...]`와 `interpretation_images` 키를 동시에
  정규화한다.
- 같은 역할의 이미지가 여러 장이면 `base`, `base:1`, `base:2`처럼 손실 없이
  고유 키를 만든다.
- 임상 채팅은 본문 토큰과 `images[].token`의 정확한 일치를 사용한다.

검증:

- `bbox_overlay/gradcam_overlay` 입력이 `[IMG:box]/[IMG:heat]`와 동일 키의
  이미지 Map으로 변환되는 자동 테스트를 추가했다.

## 3. 환자 메모

구현:

- `GET /patients/{patient_id}/notes`
- `POST /patients/{patient_id}/notes`
- 작성자는 JWT 로그인 의사로 고정한다.
- 선택 `visit_id`가 해당 환자 방문인지 검증한다.
- 기존 접수 메모 6건을 `note_id`, `author_id`, `author_name`, `source`, `text`
  표준 스키마로 변환했다.

검증:

- 기존 메모 조회 성공
- 임상 메모 작성 `201`
- 무인증 요청 `401`
- 검증용 임상 메모 1건을 PT-1045에 영구 기록했다.

## 4. 임상 채팅

구현:

- `GET /visits/{visit_id}/chat`
- `POST /visits/{visit_id}/chat`
- 같은 환자의 전체 방문 분석과 과거 대화를 Agent 문맥에 포함한다.
- 관련 이미지를 서명 URL과 함께 반환한다.

검증:

- PT-1045 채팅 이력 `200`, 메시지 2건
- 무인증 요청 `401`
- base/heat/box 이미지 토큰 및 파일 본문 `200 image/png`

## 5. Risk policy

구현:

- 서버 권한의 `RiskPolicyService`를 추가했다.
- Agent risk 값이나 공통 confidence 임계값은 최종 위험도에 사용하지 않는다.
- 모델 레지스트리의 명시적 label/detection 규칙만 평가한다.
- Admin 설정 API:
  `PUT /admin/models/{department}/{project}/risk-policy`
- ChestXray14와 RSNA YOLO 정책을 모델 레지스트리에 등록했다.
- 기존 `done/unavailable` 실제 분석 2건을 저장 결과만으로 재평가했다.

검증:

- done 29건: `assessed` 29건, `unavailable` 0건
- failed 4건은 실패 상태이므로 위험도 미분류 유지
- Admin 정책 API `200`
- doctor 역할의 `/admin` 접근은 `403`

## 6. 시드 분석 이미지

구현:

- 이미지가 없던 `demo-seed` 분석에 원본 DICOM과 base/heat/box 데모 자산을
  연결했다.
- 각 분석은 별도 GridFS 파일 ID와 메타데이터를 갖는다.
- 시드 스크립트를 다시 실행해도 중복 파일을 만들지 않는다.

검증:

- done 분석 29건 중 이미지 빈 분석 0건
- 대표 시드 `AN-20260729-0001`: base/heat/box 각 1장, DICOM 메타데이터 1건

## 7. 생년월일

확인 결과:

- 서버는 `birth_date`와 `appt_date`를 별도 Pydantic 필드와 별도 DB 필드로
  처리한다.
- `appt_date`를 `birth_date`로 복사하는 코드 경로는 없다.
- 등록일과 생년월일이 같은 레코드는 현재 PT-1043~PT-1046 4건이다.
- 네 건 모두 이름이 test 계열인 수동 등록 데이터이며, 서버는 클라이언트가
  전송한 생년월일을 그대로 저장했다.

조치:

- 실제 생년월일을 알 수 없으므로 임의 날짜로 덮어쓰지 않았다.
- 새 환자 등록에서는 미래 생년월일을 계속 `422`로 차단한다.
- 클라이언트는 `birth_date` FormData에 예약일이나 기본 Today 값이 들어가지
  않도록 수정해야 한다.

## 8. Electron CORS

구현:

- 허용 origin: `http://localhost:3000`, `app://maple`
- 허용 method와 header를 명시 목록으로 제한했다.
- credentials를 허용한다.
- 노출 응답 헤더: `Content-Length`, `Content-Type`, `ETag`
- 와일드카드 origin/method/header를 제거했다.

검증:

- `Origin: app://maple` preflight `200`
- `Authorization`, `Content-Type` 허용 확인
- `Origin: https://evil.example` preflight `400`

## 전체 회귀 검증

- Python compile 성공
- 자동 테스트 23개 전부 통과
- `git diff --check` 통과
- 분석→의료 파일 참조 오류 0건
- 활성 파일→GridFS 본문 누락 0건
- note_id, message_id 중복 0건
- 과거 실패 업로드에서 남은 `orphaned` 파일 1건은 활성 데이터가 아니며,
  물리 삭제 금지 정책에 따라 삭제하지 않았다.
- 기존 `/projects`, `/inference`, `/pipeline` 무인증 접근 보호 유지
- `/admin` admin 역할 보호 유지
- 파일 서명 URL 강제 및 감사 로그 TTL 유지

## 데이터 마이그레이션

재실행 가능한 명령:

```bash
python -m scripts.apply_clinical_v4_data
```

이 작업은 다음을 멱등 적용한다.

- 모델 risk policy 등록
- 기존 unavailable 분석 위험도 재평가
- 기존 메모 스키마 변환
- 기존 DICOM 헤더 백필
- 시드 분석 의료 파일 연결
