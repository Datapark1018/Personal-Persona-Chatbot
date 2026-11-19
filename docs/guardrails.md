# 가드레일 구성

2026-11-19 기준. [README](../README.md)의 실행 방법과 함께 사용한다.

## 검토한 오픈소스

| 저장소 | 이번 선택 |
| --- | --- |
| [NVIDIA NeMo Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) | 채택. 공개 self-check action과 모델 adapter 인터페이스를 사용해 기존 Ollama 요청 흐름에 연결 |
| [Guardrails AI](https://github.com/guardrails-ai/guardrails) | 검토. 이 앱은 이미 Pydantic으로 구조를 검증하므로 이번에는 NeMo의 입력·출력 검사 action을 선택 |
| [LLM Guard](https://github.com/protectai/llm-guard) | 미채택. 확인 시 GitHub에 2026-07-09 archive/read-only로 표시되어 새 런타임 의존성에서 제외 |
| [Meta PurpleLlama](https://github.com/meta-llama/PurpleLlama) | 검토. 이번에는 별도 안전성 모델 도입 없이 기존 로컬 Qwen을 사용하는 범위로 구성 |

공식 API와 설치한 0.24.1의 소스를 확인했다. upstream 소스를 복사해 배포하지 않고 PyPI 패키지를 의존성으로 사용한다. 정책 프롬프트·한영 정규식·Ollama adapter·차단 처리는 이 프로젝트의 구현이다.

참고: [NeMo 입력 self-check action](https://docs.nvidia.com/nemo/guardrails/guardrails-python-sdk/nemoguardrails/library/self_check/input_check/actions), [출력 self-check action](https://docs.nvidia.com/nemo/guardrails/guardrails-python-sdk/nemoguardrails/library/self_check/output_check/actions), [공식 저장소의 v0.24.1](https://github.com/NVIDIA-NeMo/Guardrails/tree/v0.24.1).

## 실제로 실행하는 것

`server/guardrails.py`는 `RailsConfig`, `LLMTaskManager`, 내장 `self_check_input` 및 `self_check_output`을 호출한다. `LLMModel` 프로토콜을 구현한 adapter가 `OLLAMA_URL/api/chat`으로 같은 Qwen 모델을 호출한다. NeMo의 전체 Colang/IORails 대화 엔진, 외부 moderation API, 임베딩 검색은 시작하지 않는다. NeMo를 제거하면 앱 시작이 실패하며, 검사를 생략하는 환경 변수는 없다.

1. 입력: NFKC 정규화·제어 문자 제거 후 패턴 검사. 입력 self-check는 현재 질문과 최근 대화 JSON을 받는다. 검사 payload가 20,000 UTF-8 바이트를 넘으면 오래된 대화 쌍을 먼저 제거한다.
2. 검색 문서: 공개 문서 최대 3개의 ID·제목·본문만 받는다. 제목·본문의 패턴 검사 후 `self_check_input`의 `retrieved_context` variant를 실행한다. 비공개 문서는 검사 모델에도 보내지 않는다.
3. 출력: 기존 JSON·출처·숫자 검증을 통과한 텍스트에 인젝션·비밀 형태 패턴과 `self_check_output`을 실행한다. 승인 전에는 본문이나 출처를 SSE로 보내지 않는다.

검사 모델은 `think=false`, `temperature=0`, `num_predict=48`, JSON Schema `{"blocked": boolean}`으로 요청한다. 반환값의 타입·필수 필드·추가 필드·생성 완료 여부를 검증한 후 NeMo action이 allow/block을 결정한다. 잘못된 판정은 허용으로 해석하지 않는다. 모델의 설명이나 추론 과정은 사용자에게 보내지 않는다.

고정 인사·clarification·fallback은 검토 가능한 앱 템플릿이며 출력 LLM 검사를 거치지 않는다. 정확한 인사 이외의 질문은 근거가 없거나 모호해도 입력 검사를 먼저 통과해야 한다. 앱 소유자의 설정 파일과 프로필은 운영자가 관리하는 설정이며, 악의적인 서버 운영자나 코드 변경을 방어하는 경계는 아니다.

## 실패와 기억

| reason_code | 의미 |
| --- | --- |
| `unsafe_request` | 입력 패턴 또는 NeMo 입력 판정이 차단 |
| `unsafe_context` | 검색된 공개 문서가 차단 |
| `unsafe_output` | 생성 답변 패턴 또는 NeMo 출력 판정이 차단 |
| `guard_unavailable` | 검사 timeout·연결 실패·잘못된 판정, 또는 전체 처리 시간 초과 |
| `invalid_output` | 기존 JSON·출처·숫자 검증 실패 |
| `model_unavailable` | 답변 생성 모델 연결/호출 실패 |

차단·검사 실패·모델 오류 요청은 이력과 주제를 갱신하지 않는다. 정상 답변, 고정 인사, 명확화 요청과 입력 검사를 통과한 근거 없음 안내만 기억한다. 사용자에게는 고정 한국어 안내와 빈 출처를 보낸다.

검사와 답변 생성은 기존 공유 lock 안에서 실행한다. lock 대기 5초, 검사별 25초, 전체 파이프라인 125초로 제한한다. 진행 상태와 10초 heartbeat를 유지하며, SSE 전송 중 연결이 끊겨도 generator를 명시적으로 닫아 검사/생성 작업과 lock을 해제한다.

NeMo가 INFO 수준에서 프롬프트를 기록하는 `llm.call` 및 self-check 결과 logger는 비활성화했다. 앱 감사 로그에는 요청 ID·상태·사유 코드·시간만 기록한다. NeMo 엔진의 tracing/content capture 기능은 사용하지 않는다. Ollama 또는 외부 프록시의 로그 정책은 별도다.

## 검증과 정책 변경

```shell
python -m pip install -r requirements-dev.txt
python -m pip check
python -m unittest discover -s tests -v
python tests/smoke_guardrails.py
python tests/smoke_conversation.py
```

일반 회귀 테스트는 로컬 모델 응답만 mock하고 실제 NeMo action과 parser를 실행한다. API에서 입력·문서·출력 차단, 검사 장애, 잘못된 JSON 판정, 비공개 문서 제외, 이력 오염 방지, 원문 로그 미노출 및 연결 취소를 검증한다. 기존 기능을 포함한 회귀 테스트는 44개다.

`tests/fixtures/guardrail_cases.json`의 21개 사례는 2026-11-19 Windows/Python 3.13과 실제 `qwen3.5:2b-q4_K_M`에서 21/21 기대 결과를 확인했다. 정상 4턴 대화도 세 단계 검사를 포함해 통과했다. 이는 작은 로컬 회귀 집합이며 일반적인 공격 탐지율이나 안전성 인증이 아니다. 다른 모델·양자화·버전에서는 다시 실행해야 한다. 원격 CI 및 지속 부하 테스트는 실행하지 않았다.

정책 수정은 `guardrails/prompts.yml`, 패턴 수정은 `guardrails/patterns.json`에서 한다. 공격 차단 사례와 정상 질문 오탐 사례를 함께 추가한다. 설정 오류나 파일 누락은 시작 실패로 처리한다. NeMo 버전을 올릴 때는 action의 반환 타입·parser·로그 동작과 의존성 lock을 함께 검증해야 한다.

## 남은 한계

- 생성 모델과 판정 모델이 동일한 소형 Qwen이므로 실패가 서로 연관될 수 있다. 별도로 학습된 Prompt Guard 분류기를 설치한 구성은 아니다.
- `temperature=0`은 완벽한 재현성·정확성을 보장하지 않는다. 우회 표현, 인코딩, 긴 대화 또는 여러 문서에 분산된 공격이 통과할 수 있다.
- 패턴 검사는 보안 연구를 설명하는 정상 문서를 차단할 수 있다. 현재 self-check 정책은 보안 관련 단어 자체를 차단하지 않도록 작성했다.
- 출력의 비밀 패턴 검사는 일부 키 형태만 다루며 범용 개인정보 탐지기나 모든 사실의 근거 검증기를 제공하지 않는다.
- 세 단계의 추가 추론과 NeMo의 간접 의존성은 설치 크기와 응답 지연을 늘린다. 지연 벤치마크는 아직 없다.
- 공개 프로필·프로젝트 카드에 표시되는 운영자 작성 텍스트는 채팅 검사 대상이 아니다. 비공개 자료 제외·DOM textContent·도구 실행 없음은 독립적인 경계로 유지한다.
