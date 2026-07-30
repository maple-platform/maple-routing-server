# 모델 실행 서버 Runtime 라우팅 안정화 요구서

> 전달 대상: `maple-model-execution-server` 담당자  
> 작성일: 2026-07-29  
> 우선순위: P0  
> 관련 장애: `AN-20260730-0004`

## 1. 요청 요약

라우팅 서버가 모델별 컨테이너 URL을 직접 결정하지 않도록 하고, 모델 실행
서버가 각 모델의 `models/{model_name}/config.yaml`에 선언된 `runtime`을
기준으로 실행 대상을 단일하게 결정해 주세요.

현재 모델 실행 서버에 구현된 `POST /infer/v2`를 정식 연동 계약으로 확정하고,
모델 설정 및 Runtime URL의 사전 검증과 readiness 진단을 추가해 주세요.

## 2. 실제 장애 증거

환자 `PT-1051`, 분석 `AN-20260730-0004`에서 Agent 실행 계획은 정상적으로
생성됐습니다.

```json
{
  "mode": "general",
  "steps": [
    {
      "step_id": "s1",
      "model_name": "FracAtlas_Fracture_Fusion",
      "project": "FracAtlas_Fracture_Fusion",
      "department": "Orthopedics"
    },
    {
      "step_id": "s2",
      "model_name": "AASCE_Scoliosis_Cobb",
      "project": "AASCE_Scoliosis_Cobb",
      "department": "Orthopedics"
    }
  ]
}
```

Agent 호출은 `HTTP 200`이었지만, 이후 추론 게이트웨이에서 다음과 같이
실패했습니다.

```text
Infer request - model_id=Orthopedics/FracAtlas_Fracture_Fusion
target=관리자 작성 예정/run/v2

Container request failed:
Request URL is missing an 'http://' or 'https://' protocol.
```

라우팅 서버 MongoDB의 두 모델에는 다음 값이 저장돼 있었습니다.

```text
docker.service_url = "관리자 작성 예정"
```

동일한 비 URL 값이 현재 모델 레지스트리 DB에서 63건 확인됐습니다.

반면 모델 실행 서버의 설정은 정상입니다.

```yaml
# models/FracAtlas_Fracture_Fusion/config.yaml
runtime: runtime-medical

# models/AASCE_Scoliosis_Cobb/config.yaml
runtime: runtime-medical
```

현재 `models/*/config.yaml` 70건 모두 아래 네 Runtime 중 하나를 선언하고
있음도 확인했습니다.

```text
runtime-basic
runtime-medical
runtime-yolo
runtime-nnunet
```

## 3. 원인

현재 임상 DAG 실행 경로는 레거시 `POST /infer`를 호출하면서 라우팅 서버 DB의
`docker.service_url`을 `params.container_url`로 전달합니다.

이 구조에서는 모델 실행 서버에 올바른 `config.yaml`이 있어도 DB의 오래되거나
임시로 입력된 URL이 우선 사용됩니다. 그 결과 모델 실행 서버가 가지고 있는
정상적인 Runtime 매핑이 우회됩니다.

## 4. 필수 요구사항

### 4.1 `POST /infer/v2`를 정식 실행 계약으로 보장

다음 요청 계약을 유지해 주세요.

```http
POST /infer/v2
Content-Type: application/json
```

```json
{
  "model_name": "FracAtlas_Fracture_Fusion",
  "input_path": "/data/clinical-work/{analysis_id}/input/image.png",
  "output_dir": "/data/clinical-work/{analysis_id}/output/s1",
  "params": {}
}
```

모델 실행 서버가 내부적으로 다음 순서로 실행 대상을 결정해야 합니다.

```text
model_name
  → models/{model_name}/config.yaml
  → runtime
  → RUNTIME_*_URL
  → {runtime_url}/run/v2
```

호출자가 `container_url`, `container_endpoint`를 전달하지 않아도 실행돼야
합니다. `/infer/v2`에서는 호출자가 전달한 임의의 컨테이너 URL이
`config.yaml`의 `runtime` 결정을 덮어쓸 수 없어야 합니다.

### 4.2 Runtime URL 형식 검증

다음 환경변수는 서버 시작 또는 readiness 검사 시 검증해 주세요.

```text
RUNTIME_BASIC_URL
RUNTIME_MEDICAL_URL
RUNTIME_YOLO_URL
RUNTIME_NNUNET_URL
```

검증 규칙:

- 값이 설정돼 있어야 한다.
- `http://` 또는 `https://`로 시작해야 한다.
- URL의 hostname이 비어 있으면 안 된다.
- 지원하지 않는 `runtime` 이름은 허용하지 않는다.
- 문자열 `"관리자 작성 예정"`, 공백, placeholder 값은 URL로 취급하지 않는다.

잘못된 URL을 실제 `httpx` 호출까지 전달해 502를 발생시키지 말고, 설정 오류로
명확하게 구분해 주세요.

권장 상태 코드:

| 상황 | 상태 코드 |
|---|---:|
| 존재하지 않는 `model_name` | 404 |
| `config.yaml` 구문 또는 필수 필드 오류 | 422 |
| 지원하지 않는 `runtime` | 422 |
| Runtime URL 미설정 또는 형식 오류 | 503 |
| Runtime 연결 실패 | 502 |
| Runtime 타임아웃 | 504 |

오류 응답에는 최소한 `model_name`, `runtime`, 오류 종류를 포함해 주세요.
비밀번호나 토큰 등 환경변수의 민감 정보는 노출하면 안 됩니다.

### 4.3 모델 설정 전체 사전 검증

서버 시작 시 `models/*/config.yaml`을 전수 검사해 주세요.

필수 필드:

```text
model_name
execution_mode
runtime
runner_path
inference_path
model_path
```

추가 검증:

- 디렉터리명과 `model_name`이 일치한다.
- `runtime`이 지원 목록에 존재한다.
- `runner_path`, `inference_path`, `model_path`가 컨테이너에서 접근 가능하다.
- 동일한 `model_name`이 중복되지 않는다.

잘못된 모델이 하나라도 있을 때의 정책은 다음 중 하나로 명시해 주세요.

1. 서버 시작 실패
2. 해당 모델만 비활성화하고 readiness 응답에 오류 목록 제공

운영 환경에서는 조용히 건너뛰는 동작은 허용하지 않습니다.

### 4.4 Readiness 및 진단 정보 제공

`GET /health`는 프로세스 생존 확인으로 유지하고, 별도의 readiness 진단을
제공해 주세요.

권장 예시:

```http
GET /ready
```

```json
{
  "status": "ready",
  "models": {
    "total": 70,
    "valid": 70,
    "invalid": 0
  },
  "runtimes": {
    "runtime-basic": "ready",
    "runtime-medical": "ready",
    "runtime-yolo": "ready",
    "runtime-nnunet": "ready"
  },
  "errors": []
}
```

Runtime 상태 확인은 각 Runtime의 `/health`를 사용하고 짧은 타임아웃을
적용해 주세요. 응답에는 Runtime URL 전체 대신 Runtime 이름과 상태만
노출해도 됩니다.

### 4.5 레거시 `POST /infer` 방어

기존 호출자를 위해 `POST /infer`를 당장 제거하지 않더라도 다음 검증을
추가해 주세요.

- `params.container_url`이 유효한 HTTP(S) URL인지 호출 전에 검사한다.
- placeholder나 프로토콜 없는 문자열은 422로 거절한다.
- 오류 메시지에 모델 ID와 잘못된 설정 필드를 명시한다.
- 레거시 API임을 문서와 로그에 표시한다.

단, 임상 DAG의 최종 연동 대상은 `/infer/v2`입니다.

## 5. 응답 계약

`POST /infer/v2` 성공 응답은 모든 Runtime에서 동일한 최상위 구조를
유지해야 합니다.

```json
{
  "status": "ok",
  "result": {
    "result_type": "classification",
    "predictions": [],
    "data": {}
  },
  "output_images": [],
  "model_output": {},
  "metadata": {
    "model_name": "FracAtlas_Fracture_Fusion",
    "runtime": "runtime-medical"
  }
}
```

모델별 추가 필드는 `result.data` 또는 `model_output` 아래에 둘 수 있습니다.
다만 다음 값은 모델마다 위치가 달라지지 않아야 합니다.

- `status`
- `result.result_type`
- `result.predictions`
- `output_images`
- `metadata.model_name`
- `metadata.runtime`

## 6. 테스트 요구사항

최소 다음 자동 테스트를 추가해 주세요.

1. `FracAtlas_Fracture_Fusion`이 `runtime-medical`로 해석된다.
2. `AASCE_Scoliosis_Cobb`이 `runtime-medical`로 해석된다.
3. `RSNA_Pneumonia_YOLO26x`가 `runtime-yolo`로 해석된다.
4. `VerSe_Vertebrae_CT`가 `runtime-nnunet`으로 해석된다.
5. `/infer/v2` 요청에 컨테이너 URL 없이도 정상 Runtime URL이 선택된다.
6. 알 수 없는 모델은 404를 반환한다.
7. 알 수 없는 Runtime은 422를 반환한다.
8. Runtime 환경변수 누락 및 잘못된 URL은 503을 반환한다.
9. 레거시 `/infer`에 `"관리자 작성 예정"`을 보내면 실제 HTTP 호출 없이
   422를 반환한다.
10. 전체 `models/*/config.yaml` 검증 테스트가 통과한다.

## 7. 완료 조건

- `POST /infer/v2`로 두 문제 모델을 호출했을 때 게이트웨이 로그가 다음처럼
  기록된다.

```text
model_name=FracAtlas_Fracture_Fusion runtime=runtime-medical
target=http://runtime-medical:8000/run/v2
```

- 호출 경로 어디에도 라우팅 서버 DB의 `docker.service_url`이 필요하지 않다.
- `"관리자 작성 예정"`이 URL로 사용되는 요청은 발생하지 않는다.
- 전체 모델 설정 검증 결과가 `70 valid / 0 invalid`이다.
- 네 Runtime의 readiness가 확인된다.
- 위 테스트가 CI에서 통과한다.
- `/infer/v2` 요청·응답 및 오류 계약이 README 또는 API 문서에 반영된다.

## 8. 시스템 간 책임 구분

### 모델 실행 서버 담당

- `model_name → config.yaml → runtime → runtime URL` 결정
- `/infer/v2` 계약과 응답 정규화
- 모델 설정 및 Runtime 환경변수 검증
- Runtime readiness 제공
- 레거시 `/infer`의 URL 유효성 검증

### 라우팅 서버 담당

- 임상 DAG 실행을 `/infer`에서 `/infer/v2`로 전환
- DB `docker.service_url`을 추론 실행 판단에 사용하지 않도록 제거
- 기존 DB의 placeholder 63건을 정리하거나 표시 전용 필드로 전환
- 실패한 `AN-20260730-0004`를 수정 후 재대기열 등록

따라서 모델 실행 서버 작업만으로 전체 장애가 종결되는 것은 아닙니다.
모델 실행 서버가 위 계약을 보장한 뒤 라우팅 서버도 `/infer/v2`로 호출 경로를
전환해야 합니다.

## 9. 이번 장애와 별개인 검토사항

Agent가 CT NIfTI 입력에 방사선 사진용 `FracAtlas_Fracture_Fusion`과
`AASCE_Scoliosis_Cobb`을 선택한 적절성은 별도 모델 선택 정책 문제입니다.
이는 이번 URL 형식 502의 직접 원인이 아니므로 본 요구서 범위에서는
분리합니다.
