# Financial Juice Telegram Bot

Financial Juice RSS 헤드라인을 가져와서 한국어로 번역하고, 간단한 설명을 붙여 텔레그램으로 보내는 로컬 실행용 봇입니다.

## 구성

- `Financial Juice RSS`에서 최신 헤드라인 수집
- `Ollama` 로컬 모델로 한국어 번역 + 간단한 설명 생성
- `Telegram Bot API`로 명령 처리 및 자동 알림
- `SQLite`로 구독 채팅, 처리된 뉴스, 전송 이력 저장

## 폴더 구조

- `main.py`: 실행 진입점
- `financial_juice_bot/config.py`: 환경변수 로드
- `financial_juice_bot/rss.py`: RSS 수집
- `financial_juice_bot/ollama_client.py`: Ollama 호출
- `financial_juice_bot/database.py`: SQLite 저장
- `financial_juice_bot/services.py`: 뉴스 처리 서비스
- `financial_juice_bot/bot.py`: 텔레그램 명령 및 자동 발송

## 로컬 실행 방법

1. 가상환경 생성

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

2. 패키지 설치

```powershell
pip install -r requirements.txt
```

3. Ollama 모델 준비

```powershell
ollama pull martain7r/finance-llama-8b:q4_k_m
```

현재 기본값은 금융 특화 모델인 `martain7r/finance-llama-8b:q4_k_m`입니다. 다른 모델을 쓰고 싶다면 `.env`의 `OLLAMA_MODEL` 값만 바꾸면 됩니다.
이 모델은 응답이 조금 느릴 수 있어서 `OLLAMA_TIMEOUT_SECONDS=180` 정도를 함께 두는 것을 권장합니다.

4. 텔레그램 봇 토큰 발급

- 텔레그램에서 `@BotFather` 접속
- `/newbot` 실행
- 받은 토큰을 `.env`에 입력

5. 환경변수 파일 생성

`.env.example`을 참고해서 같은 폴더에 `.env` 파일을 만들고 값을 채웁니다.

예시:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCDEF...
OLLAMA_MODEL=martain7r/finance-llama-8b:q4_k_m
OLLAMA_TIMEOUT_SECONDS=180
RSS_MIN_FETCH_INTERVAL_SECONDS=90
```

6. 실행

```powershell
python main.py
```

## 텔레그램 명령어

- `/start`: 현재 채팅을 구독하고 자동 알림 시작
- `/latest`: 최근 헤드라인 번역 결과 확인
- `/latest 5`: 최근 5개까지 확인
- `/llm on`: LLM 설명 표시 켜기
- `/llm off`: LLM 설명 표시 끄기
- `/llm`: 현재 LLM 설명 설정 확인
- `/status`: 현재 구독 상태 확인
- `/subscribe`: 자동 알림 재개
- `/unsubscribe`: 자동 알림 중단

## 동작 방식

`/start` 또는 `/subscribe`를 누르면 현재 채팅이 구독됩니다. 그 뒤로는 봇이 전역으로 RSS를 한 번만 확인하고, 새 헤드라인을 SQL에 저장한 뒤 각 사용자에게 그 저장본을 기준으로 보냅니다.

처음 구독할 때는 바로 예전 헤드라인이 몰아서 오지 않도록 최근 항목들을 전송 완료로 기록해 둡니다. `/latest` 역시 외부 RSS를 다시 호출하지 않고 SQL에 저장된 최근 뉴스만 보여줍니다.

채팅별로 `/llm on`, `/llm off`를 사용하면 번역 아래 붙는 LLM 설명 문장을 표시하거나 숨길 수 있습니다. 이 설정은 자동 알림과 `/latest` 결과에 모두 적용됩니다.

## 429 대응

Financial Juice RSS는 짧은 시간에 여러 번 요청하면 `429 Too Many Requests`를 반환할 수 있습니다. 이 프로젝트는 다음 방식으로 이를 줄입니다.

- 외부 RSS 호출은 전역 동기화에서만 수행
- 각 사용자 명령은 SQL 저장본만 조회
- 최근 성공 RSS 결과는 메모리에 캐시
- `RSS_MIN_FETCH_INTERVAL_SECONDS` 이내에는 재요청 방지
- `429` 발생 시 `RSS_RATE_LIMIT_COOLDOWN_SECONDS` 동안 백오프

기본값:

- `POLL_INTERVAL_SECONDS=60`
- `RSS_MIN_FETCH_INTERVAL_SECONDS=90`
- `RSS_RATE_LIMIT_COOLDOWN_SECONDS=180`

