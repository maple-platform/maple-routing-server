# 에이전틱 general 모드 설계 (Agentic General Pipeline)

> **상태**: Step 2(라우팅) 구현 완료 · interpret 계약 확장(#7)은 후속
> **브랜치**: `feat/agentic-general`
> **동시 작업**: `maple-agent-server`(Step 1·3), `maple-model-execution-server`(provides/requires)

general 모드를 "VLM 단독 분석"에서 **"모델 자동 발견 → 실행 → VLM 종합 해석"** 3-step
에이전틱 오케스트레이션으로 재설계한다.

---

## 1. 3-Step 흐름

```
Step 1 (Agent, LLM)      Step 2 (Routing, AI-server)     Step 3 (Agent, VLM)
─────────────────        ───────────────────────         ───────────────────
자연어 의도 분석          execution_plan.steps[] 수신      원본이미지 + 결과 + XAI
ChromaDB 모델 탐색   Res  → DAG 실행 (병렬+순차)      Res  → VLM 종합 분석
(유사도≥0.65, 최대5)  ─→  → 결과 집계             ─→   → 최종 판독문
실행계획 수립(병렬/순차)      (예측·확률·이미지·XAI)          (model_suggestion 포함)
유효성 검증
        └── Req ──────────────┘         └── Req ──────────────┘
```

- **Step 1·3 = agent-server**, **Step 2 = 이 라우팅 서버**.
- 모델 0개면 `fallback_vlm_only` → Step 2/3 없이 VLM 단독 답변.

---

## 2. plan 응답 계약 (`/agent/plan` mode=general)

```jsonc
{
  "mode": "general",
  "execution_plan": {                    // ← 라우팅이 파싱하는 키
    "steps": [
      {
        "step_id": "s1",                 // DAG 노드 식별자
        "model_name": "...",
        "department": "...",             // 실행 호출 키
        "project": "...",                // 실행 호출 키
        "task_type": "...",
        "result_type": "image",
        "required_data": ["nifti"],      // 입력 파일 선택/검증
        "depends_on": []                 // 선행 step_id 목록 (DAG)
      },
      { "step_id": "s2", ..., "depends_on": ["s1"] }
    ]
  },
  "fallback_vlm_only": false,            // true면 steps:[] + message에 VLM 답변
  "message": ""                          // fallback 시 VLM 답변
}
```

### 확정된 계약 결정
| # | 항목 | 결정 |
|---|---|---|
| 1 | plan 응답 키 | **`execution_plan.steps[]`** (기존 prediction과 동일 구조, 라우팅이 파싱) |
| 2 | step 필드셋 | `step_id, model_name, department, project, task_type, result_type, required_data, depends_on` |
| 3 | DAG 의미 | `depends_on:[]`→즉시 / `["s1"]`→s1 후. 병렬은 위상정렬로 도출 |
| 4 | fallback | `fallback_vlm_only:true` → steps:[] + message 그대로 통과 (실행/interpret 없음) |
| 5 | provides/requires | 모델 등록 시 주입해야 agent가 depends_on 도출 (미기재 시 전부 병렬) |
| 6 | plan 요청 파일정보 | `attachments[].type` + `uploaded_types` 전달 → required_data 매칭 |

---

## 3. Step 2 — DAG 실행기 (라우팅)

`pipeline_service.run_dag(steps, save_input_dir, save_output_dir)`

- **위상 검증**: depends_on 참조 유효성 + Kahn 사이클 검사. 실패 시 error.
- **실행**: step마다 asyncio 태스크. 각 태스크는 자기 `depends_on` 태스크를 먼저 await → 실행.
  - depends_on 없는 노드들은 동시(병렬) 시작
  - 의존 노드는 선행 완료 즉시 시작 (barrier 낭비 없음, 최대 병렬)
- **입력 구성** (`_resolve_step_input`):
  - 루트: `required_data`에 맞는 원본 파일 선택 (Track B `select_input_file`)
  - 의존: 원본 경로 + 선행 출력의 ROI (있으면 `{image_path, roi}`)
- **집계**: step_results[] (step_id·model·result_type·predictions·model_output·images).
  일부 실패 시 `status: "partial"` + `errors[]`, 전부 실패 시 error.

## 4. general 모드 배선 (controller)

```
1. 원본 저장 (save_input_dir)
2. build_attachments_from_dir → attachments[] (plan용, fallback VLM용)
3. plan(general, attachments, uploaded_types)
4. fallback_vlm_only 또는 steps 없음 → message 그대로 반환
5. run_dag(steps) → 집계
6. interpret(step_results, execution_context={plan, attachments_meta})
7. { interpretation, images, predictions, step_results } 반환
```

---

## 5. 크로스레포 분담

| 레포 | 책임 |
|---|---|
| **maple-agent-server** | Step 1(의도·ChromaDB 탐색·계획·검증), Step 3(VLM 종합). `execution_plan.steps[]`·`fallback_vlm_only` 반환 |
| **maple-routing-server** (이 브랜치) | Step 2(DAG 실행·집계), general 배선, 등록 시 provides/requires 주입 |
| **maple-model-execution-server** | 각 모델 meta.json에 `provides`/`requires` 태그 기재 (체인 형성 근거) |

---

## 6. 후속 (#7) — interpret 계약 확장

집계 후 종합 판독을 위해 `/agent/interpret`가 **원본 이미지 채널**을 받아야 한다.
현재 `execution_context.attachments_meta`(메타만)는 전달되나, 원본 스캔 이미지는 미전달.
`InterpretRequest`에 원본 이미지 채널 추가 후, controller가 attachments의 images를 실어 보낸다.
(원본이미지 + 메타 + 집계 모델결과·XAI → VLM 통합 해석)
