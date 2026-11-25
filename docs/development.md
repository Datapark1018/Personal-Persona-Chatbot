# 개발 안내

현재 실행·설정 방법은 [README](../README.md)를 기준으로 한다. [plan.md](plan.md)는 최초 설계 문서이며 실제 구현과 다른 제안도 포함한다.

## 2026-11-25 공개 전 점검

- README를 설치·개인화·공개 범위·실행 설정 중심으로 정리했다. 실제 구현, 미구현 기능, 검증 범위를 구분했다.
- 숫자 근거 검사가 `2025`에서 `25`를 찾는 식의 부분 일치를 허용하지 않도록 수정했다. 숫자 의미나 단위의 사실성까지 판정하는 기능은 아니다.
- 서버 origin 설정에서 빈 Ollama URL, 잘못된 포트, 공백·제어 문자·역슬래시를 시작 시 거부한다.
- `python tests/smoke_http.py`는 임시 루프백 서버에 실제 HTTP 요청을 보내고 종료한다. 허구 예제로 정적 파일·health·프로필·세션·입력 제한·4턴 SSE·출처·인젝션 차단을 확인한다. 브라우저 렌더링은 검사하지 않는다.
- Windows/Python 3.13에서 회귀 테스트 46개 및 실제 Ollama를 사용하는 HTTP 검사를 통과했다. GitHub CI와 부하 테스트는 이번 확인에 포함하지 않았다.

## 2026-11-19 가드레일

- NeMo Guardrails 0.24.1의 self-check action을 질문·검색 문서·답변에 연결했다. 생성과 검사는 같은 로컬 Ollama Qwen을 사용한다.
- 한·영 패턴, 엄격한 판정 JSON parser, 검사 오류 시 차단, 차단된 요청의 기억 제외를 추가했다.
- SSE 상태 전송 중 연결이 끊겨도 generator를 닫아 검사·생성 작업과 lock을 해제한다.
- 정책·후보 비교·실제 모델 검증·한계는 [guardrails.md](guardrails.md)를 참고한다. Python 지원 범위는 3.11~3.13이며 간접 의존성을 lock에 반영했다.

## 2026-11-18 변경

- `Persona` 스키마가 인물·말투·역할·질문 예시·문서 메타데이터를 관리한다.
- `create_app(Settings(...))`가 각 인스턴스의 프로필·검색·세션·제한·추론 lock을 소유한다. 서로 다른 앱의 상태가 섞이지 않는다.
- 공개 화면은 `/api/persona`의 명시적인 표시 필드만 사용하며 모든 설정 텍스트를 DOM textContent로 렌더링한다.
- `private` 문서는 인덱스, 주제 resolver, prompt 문서, 출처 validator, 공개 API에서 제외한다.
- 모델 답변은 전체 검증 후 SSE로 보낸다. 연결 해제 시 pending inference를 취소하고 lock과 활성 요청을 해제한다.
- 공개 입력 파일은 `persona.example.json`, 로컬 개인 파일은 기본 `persona.local.json`이다. `knowledge/` 아래 예제와 schema 이외 파일은 Git 제외다.
- `/api/session` 조회로 새로고침 시 최근 대화를 복원한다. 원문 이력은 서버 메모리에만 있다.
- 런타임 의존성을 `requirements.lock`으로 고정했다. 테스트는 `requirements-dev.txt`를 사용한다.

## 이전 데이터 이전

기존 `knowledge/persona.json`을 자동으로 읽지 않는다. 파일을 삭제하거나 덮어쓰지도 않는다. 기존 파일이 있다면 다음 순서로 이전한다.

1. `persona.example.json`을 아직 존재하지 않는 `persona.local.json`에 복사한다.
2. 기존 프로필·문서 내용을 새 파일로 옮긴다. 상위 `headline`, `bio`, `suggested_questions`와 필요에 따라 `tone`, `response_mode`, `aliases`를 설정한다.
3. 모든 문서에 `category`, `visibility`, `summary`를 추가한다. 프로젝트는 `category: project`로 지정한다.
4. 예전 `/#about` 등의 `url`은 `null`로 바꿔 자동 `/#doc-{id}`를 사용하거나, 검토한 HTTPS 외부 주소로 바꾼다.
5. `python -m server.validate_persona knowledge/persona.local.json`으로 검증한다.
6. 서버를 재시작하고 기존 이름·프로젝트가 답변이나 UI에 남아 있지 않은지 확인한다.

이전 파일과 개인 파일은 현재 `.gitignore` 대상이다. 이미 커밋한 데이터는 Git 이력에 남으므로 제외 규칙만으로 비공개 처리되지 않는다. 로컬 파일도 암호화하지 않는다.

## 검증

2026-11-18 Windows/Python 3.13의 새 가상환경에서 설치·`pip check`와 회귀 테스트 29개가 통과했다. 실제 Qwen의 기본 예제 4턴 및 별도 인물·프로젝트 답변도 확인했다. 브라우저에서는 동적 프로필, 질문 전송, 새로고침 시 이력 복원과 새 대화를 확인했다. GitHub 원격 CI 및 장시간 부하 검증은 아직 실행하지 않았다.

```shell
python -m pip install -r requirements-dev.txt
python -m pip check
python -m server.validate_persona knowledge/persona.example.json
python -m unittest discover -s tests -v
node --check web/app.js
```

`tests/smoke_conversation.py`는 실제 Ollama와 기본 데모를 사용한다. 일반 테스트에는 모델 다운로드나 외부 네트워크가 필요 없다. CI는 Windows/Ubuntu/macOS와 Python 3.11/3.13을 대상으로 구성했으며 이 문서만으로 원격 CI 성공을 주장하지 않는다.

`tests/smoke_guardrails.py`는 실제 Ollama를 사용해 한·영 입력, 검색 문서의 간접 인젝션, 출력과 정상 질문 21개를 검사한다. 2026-11-19 실제 Qwen에서 21/21을 확인했으며, 가드레일을 거치는 정상 4턴 대화도 통과했다.

같은 날 가드레일 회귀 테스트를 포함한 44개 테스트와 `pip check`, JavaScript 구문 검사, 예제 persona 검증을 통과했다. 실행용 `.venv`에도 갱신된 의존성을 설치했다.

JSON Schema를 변경했다면 아래 명령으로 파일을 갱신한다. 스키마 일치 여부는 테스트한다.

```shell
python -c "from pathlib import Path; from server.persona import write_schema; write_schema(Path('knowledge/persona.schema.json'))"
```

## 운영

- 예: `python -m uvicorn server.app:app --host 127.0.0.1 --port 8000 --workers 1`.
- HTTPS reverse proxy 뒤에서는 `PUBLIC_ORIGIN=https://your-domain.example`, `COOKIE_SECURE=1`을 지정한다. 브라우저와 API는 같은 origin을 사용한다.
- 프록시 IP 전달 헤더를 신뢰할 범위를 Uvicorn 설정에서 제한한다. Ollama에는 사설 경로로만 연결한다.
- SSE의 proxy buffering을 끄고 유휴 타임아웃을 10초 heartbeat보다 충분히 길게 둔다. 추론 자체 제한은 앱에서 처리한다.
- `uvicorn.error.portfolio` logger에 요청 ID·상태·사유·소요 시간을 기록한다. 질문·답변·예외 원문은 앱 감사 로그에 쓰지 않는다. 접근 로그의 IP 및 운영 로그 보관은 배포 환경에서 결정한다.
- `/health/live`는 프로세스만, `/health/ready`는 3초 제한의 `/api/show` 호출로 지정 모델의 completion 지원을 확인한다. 결과는 10초 캐시하며 오류 상세는 공개하지 않는다.
- 정상 대화 이력은 12턴·8,000자, 모델 입력은 20,000 UTF-8 바이트 예산을 둔다. 이 값은 토큰 수 측정이 아니므로 큰 문서에서는 fallback이 발생할 수 있다.

## 남은 한계

전체 의미적 사실성 검증, 실제 Qwen 토크나이저를 사용하는 예산, 다중 사용자 인증·공유 저장소, 100문항 품질 평가 및 장시간 부하 측정은 아직 완료하지 않았다. 응답의 출처 ID·숫자 일치만으로 문장의 진실성을 보장하지 않는다.
