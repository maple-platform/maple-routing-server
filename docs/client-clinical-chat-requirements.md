# 임상 채팅 클라이언트 연동 요구사항

> 대상: `maple-client`의 임상 채팅 패널  
> 백엔드 기준: `GET/POST /visits/{visit_id}/chat`

## 1. 현재 화면의 문제

현재 화면은 다음 두 렌더링 단계를 처리하지 못하고 있다.

1. `**굵게**`, `### 제목` 같은 Markdown이 서식이 아니라 원문으로 표시된다.
2. `[IMG:bbox_overlay]`, `[IMG:gradcam_overlay]`가 이미지로 치환되지 않고
   원문으로 표시되며, 모든 이미지는 답변 하단에 일괄 배치된다.

이미지 수신 자체는 정상이다. 메시지 역할과 이미지 역할을 구분하고,
Assistant 본문의 이미지 토큰 위치에 해당 이미지를 삽입해야 한다.

## 2. 역할은 두 종류이며 서로 다른 값이다

### 2.1 메시지 역할

```ts
type ChatMessageRole = 'user' | 'assistant';
```

- `user`: 의사가 입력한 메시지
- `assistant`: 임상 Agent가 생성한 답변
- 클라이언트가 배열 순서나 메시지 내용으로 역할을 추론하지 않는다.
- `POST` 응답의 `user.role`, `assistant.role`과 `GET` 응답의 각 `role`을
  그대로 사용한다.

### 2.2 이미지 역할

```ts
type ClinicalImageRole = 'base' | 'heat' | 'box';
```

| 서버 역할 | UI 표시명 | 의미 |
|---|---|---|
| `base` | Original | 원본 또는 기본 영상 |
| `heat` | Grad-CAM | 활성도·열지도 영상 |
| `box` | Bounding box | 객체 탐지 박스 영상 |

`ChatMessageRole`과 `ClinicalImageRole`은 별개의 타입이어야 한다. 예를 들어
이미지의 `role="heat"`를 메시지의 `role="assistant"` 자리에 넣지 않는다.

## 3. 사용할 API 계약

### 3.1 방문 채팅 이력

```http
GET /visits/{visit_id}/chat
Authorization: Bearer <access_token>
```

응답은 `ChatMessage[]`이며 시간 오름차순이다.

### 3.2 메시지 전송

```http
POST /visits/{visit_id}/chat
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "content": "폐렴으로 의심되는 위치를 영상과 함께 설명해줘"
}
```

요청에는 `role`을 보내지 않는다. 로그인 의사가 보낸 요청은 서버가
`user`로 기록하고 Agent 응답은 서버가 `assistant`로 기록한다.

응답:

```ts
interface ChatExchange {
  user: ChatMessage;
  assistant: ChatMessage;
}
```

### 3.3 DTO

```ts
interface ChatImageReference {
  token: string; // 예: AN-20260729-0045:heat:0
  analysis_id: string;
  file_id: string;
  role: ClinicalImageRole;
  slice_index: number;
  url: string;
  expires_at: string;
}

interface ChatMessage {
  message_id: string;
  patient_id: string;
  visit_id: string;
  role: ChatMessageRole;
  content: string;
  context_analysis_ids: string[];
  images: ChatImageReference[];
  created_at: string;
}
```

## 4. Assistant 본문 렌더링

### 4.1 표준 토큰

새 채팅 API의 표준 토큰은 다음 형식이다.

```text
[IMG:<analysis_id>:<role>:<slice_index>]
```

예:

```md
우하엽의 활성 영역을 확인하세요.

[IMG:AN-20260729-0045:heat:0]

탐지 모델 결과는 다음과 같습니다.

[IMG:AN-20260729-0045:box:0]
```

`content`의 토큰 문자열과 `images[].token`은 정확히 일치한다.

### 4.2 필수 렌더링 순서

Assistant 메시지 하나마다 다음 순서로 처리한다.

1. `images`를 `token` 기준의 Map으로 만든다.
2. `content`를 `/\[IMG:([A-Za-z0-9._:-]+)\]/g`로 분리한다.
3. 일반 텍스트 조각은 Markdown으로 렌더링한다.
4. 등록된 토큰은 그 위치에서 `ClinicalInlineImage`로 렌더링한다.
5. 본문에서 사용한 이미지는 답변 하단 갤러리에 중복 출력하지 않는다.
6. 본문에 없는 나머지 이미지만 필요할 경우 “관련 영상” 갤러리로 표시한다.

간단한 구현 형태:

```tsx
function AssistantContent({ message }: { message: ChatMessage }) {
  const imageByToken = new Map(
    message.images.map(image => [image.token, image]),
  );
  const parts = splitImageTokens(message.content);

  return (
    <>
      {parts.map((part, index) => {
        if (part.kind === 'image') {
          const image = imageByToken.get(part.token);
          return image ? (
            <ClinicalInlineImage
              key={`${part.token}-${index}`}
              image={image}
            />
          ) : (
            <MissingImageNotice key={`${part.token}-${index}`} />
          );
        }
        return (
          <SafeMarkdown key={`text-${index}`}>
            {part.value}
          </SafeMarkdown>
        );
      })}
    </>
  );
}
```

토큰을 단순 문자열 `replace()`로 HTML에 삽입하지 않는다. React node 배열로
구성해야 URL 및 Markdown 내용이 HTML로 실행되는 것을 막을 수 있다.

### 4.3 Markdown

Assistant 텍스트는 다음 문법을 최소 지원한다.

- 문단과 줄바꿈
- `**굵게**`
- `*` 또는 `-` 목록
- `###` 제목

Markdown 안의 임의 HTML 실행은 비활성화한다. 현재 화면처럼 `**`, `###`가
그대로 보이면 완료로 간주하지 않는다.

## 5. 이미지 역할 규격

백엔드는 기존 `/inference` Agent 응답의 `original`,
`segmentation_overlay`, `gradcam_overlay`, `bbox_overlay`를 각각 표준 역할
`base`, `base`, `heat`, `box`로 정규화한 뒤 반환한다.

클라이언트에는 별도 대응표를 두지 않는다.

- 레거시 `/inference`: `[IMG:base|heat|box]`와
  `interpretation_images`의 동일 키를 직접 매칭한다.
- 임상 채팅: 본문의 `[IMG:<token>]`과 `images[].token`을 정확히 매칭하고
  `images[].role`은 표시 종류에만 사용한다.

브라우저에 남아 있는 정규화 전 응답은 새 API 조회로 교체한다.

## 6. 이미지 컴포넌트 요구사항

- URL은 상대 경로일 수 있으므로 backend base URL을 붙인다.
- `role`에 따라 Original, Grad-CAM, Bounding box 라벨을 표시한다.
- 썸네일 클릭 시 기존 우측 이미지 뷰어 또는 확대 모달을 연다.
- `analysis_id`, `slice_index`를 유지해 어떤 분석의 몇 번째 이미지인지
  식별할 수 있어야 한다.
- CSS `background-image`를 사용할 수 있다. 서명 URL은 Bearer 헤더 없이
  접근 가능하다.
- Canvas에 그린 뒤 `toDataURL()`을 사용한다면 `crossOrigin="anonymous"`를
  `src` 설정보다 먼저 적용한다.
- 이미지 로딩 실패 또는 URL 만료 시 채팅 이력을 다시 조회하여 새 URL을
  받는다.
- 서명 URL 전체를 console이나 오류 수집 서비스에 기록하지 않는다.

## 7. 상태 관리

- 이미지 Map은 전역 Map이 아니라 `message_id`별로 관리한다.
- 방문 변경 시 이전 방문의 요청과 렌더링 상태를 정리한다.
- 전송 성공 시 `ChatExchange.user`, `ChatExchange.assistant`를 각각 한 번만
  추가한다.
- optimistic user message를 사용했다면 서버가 반환한 `user.message_id`로
  교체하여 중복을 방지한다.
- 전송 중에는 같은 메시지 중복 전송을 막는다.
- `GET` 재조회 시 `message_id`를 기준으로 목록을 교체하거나 병합한다.

## 8. 오류 및 예외 표시

- 본문에 토큰이 있지만 대응 이미지가 없으면 토큰 원문을 그대로 노출하지
  않고 “관련 영상을 불러올 수 없습니다”를 표시한다.
- 이미지가 있지만 본문 토큰이 없으면 답변 하단 “관련 영상” 영역에 한 번만
  표시한다.
- `401`: access token refresh 후 API를 한 번 재시도한다.
- 이미지 `401`: 메시지 전송을 재시도하지 말고 `GET .../chat`으로 URL만
  갱신한다.
- `502`: Agent 답변 실패 안내와 재전송 동작을 제공한다.

## 9. 완료 조건

- [ ] user와 assistant 말풍선이 `message.role`에 따라 구분된다.
- [ ] `**굵게**`, 목록, `### 제목`이 Markdown 서식으로 표시된다.
- [ ] `[IMG:...]` 문자열이 화면에 그대로 남지 않는다.
- [ ] base/heat/box 이미지가 Assistant가 지정한 문단 위치에 표시된다.
- [ ] 본문에 삽입한 이미지가 답변 하단에 다시 중복 표시되지 않는다.
- [ ] 클라이언트 코드에 legacy 이미지 역할 대응표가 없다.
- [ ] 이미지 라벨이 Original, Grad-CAM, Bounding box로 구분된다.
- [ ] 썸네일 클릭 시 확대 또는 기존 이미지 뷰어가 열린다.
- [ ] 방문 재진입 후 `GET /chat`으로 동일 대화가 복원된다.
- [ ] 만료된 이미지 URL이 채팅 이력 재조회 후 정상 갱신된다.

## 10. 검수 예시

다음 Assistant 응답을 사용해 검수한다.

```json
{
  "role": "assistant",
  "content": "**탐지 결과**\\n\\n[IMG:AN-1:box:0]\\n\\n### 분류 결과\\n\\n[IMG:AN-1:heat:0]",
  "images": [
    {
      "token": "AN-1:box:0",
      "analysis_id": "AN-1",
      "file_id": "F-box",
      "role": "box",
      "slice_index": 0,
      "url": "/files/F-box?exp=...&sig=...",
      "expires_at": "2026-07-29T17:00:00+09:00"
    },
    {
      "token": "AN-1:heat:0",
      "analysis_id": "AN-1",
      "file_id": "F-heat",
      "role": "heat",
      "slice_index": 0,
      "url": "/files/F-heat?exp=...&sig=...",
      "expires_at": "2026-07-29T17:00:00+09:00"
    }
  ]
}
```

예상 결과:

1. “탐지 결과”는 굵게 표시된다.
2. 바로 아래에 Bounding box 이미지가 표시된다.
3. “분류 결과”는 제목으로 표시된다.
4. 바로 아래에 Grad-CAM 이미지가 표시된다.
5. 이미지 두 장이 답변 맨 아래에 다시 나타나지 않는다.
