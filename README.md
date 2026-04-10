# Financial Juice Telegram Bot

Financial Juice RSS 헤드라인을 가져와서 한국어로 번역해 텔레그램으로 보내는 로컬 실행용 봇입니다. 이 버전은 `DeepL API Free`를 사용합니다.

## 구성

- `Financial Juice RSS`에서 최신 헤드라인 수집
- `DeepL API Free` 번역
- 이미지가 있을 때는 사진과 함께 전송
- `Telegram Bot API`로 명령 처리 및 자동 알림
- `SQLite`로 구독 채팅, 처리된 뉴스, 전송 이력 저장

## 폴더 구조

- `main.py`: 실행 진입점
- `financial_juice_bot/config.py`: 환경변수 로드
- `financial_juice_bot/rss.py`: RSS 수집
- `financial_juice_bot/translator_client.py`: DeepL 번역
- `financial_juice_bot/database.py`: SQLite 저장
- `financial_juice_bot/services.py`: 뉴스 처리 서비스
- `financial_juice_bot/bot.py`: 텔레그램 명령 및 자동 발송

## 로컬 실행 방법

필수 버전:

- `Python 3.10+`

1. 가상환경 생성

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

2. 패키지 설치

```powershell
pip install -r requirements.txt
```

3. 번역 엔진 준비

DeepL API Free 키를 발급받아 `.env`에 넣어야 합니다.

4. 텔레그램 봇 토큰 발급

- 텔레그램에서 `@BotFather` 접속
- `/newbot` 실행
- 받은 토큰을 `.env`에 입력

5. 환경변수 파일 생성

`.env.example`을 참고해서 같은 폴더에 `.env` 파일을 만들고 값을 채웁니다.

예시:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCDEF...
TRANSLATOR_ENGINE=deepl
DEEPL_API_KEY=REPLACE_WITH_YOUR_DEEPL_API_KEY
DEEPL_API_BASE_URL=https://api-free.deepl.com
DEEPL_SOURCE_LANG=EN
DEEPL_TARGET_LANG=KO
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
- `/status`: 현재 구독 상태 확인
- `/subscribe`: 자동 알림 재개
- `/unsubscribe`: 자동 알림 중단
- `/cards`: 카드형 게시물 수신 상태 확인
- `/cards on`: 금리 확률/변동성/상관행렬 카드 포함
- `/cards off`: 일반 뉴스형 헤드라인만 수신
- `/original`: 원문 표시 상태 확인
- `/original on`: 원문 줄 표시
- `/original off`: 원문 줄 숨김
- `/time`: 시간 표시 상태 확인
- `/time on`: 시간 줄 표시
- `/time off`: 시간 줄 숨김

## 동작 방식

`/start` 또는 `/subscribe`를 누르면 현재 채팅이 구독됩니다. 그 뒤로는 봇이 전역으로 RSS를 한 번만 확인하고, 새 헤드라인을 SQL에 저장한 뒤 각 사용자에게 그 저장본을 기준으로 보냅니다.

카드형 게시물은 기본값이 `OFF`입니다. 현재는 `Interest Rate Probabilities`, `Implied Volatility`, `Correlation Matrix`, `Currency Strength Chart` 계열을 카드형 게시물로 분류하고 `/cards on`일 때만 전송합니다.

원문 표시와 시간 표시는 기본값이 모두 `ON`입니다. `/original off`이면 원문 줄을 숨기고, `/time off`이면 시간 줄을 숨깁니다.

처음 구독할 때는 바로 예전 헤드라인이 몰아서 오지 않도록 최근 항목들을 전송 완료로 기록해 둡니다. `/latest` 역시 외부 RSS를 다시 호출하지 않고 SQL에 저장된 최근 뉴스만 보여줍니다.

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

## 다음 단계 아이디어

- 관심 키워드 필터링
- 매크로/주식/원자재별 채널 분리
- Dockerfile 추가 후 클라우드 배포
- 관리자 전용 명령으로 강제 재전송/점검
- 금융 표현용 커스텀 glossary 확장
- DeepL 사용량 조회 명령 추가

## 속보 표시

- Financial Juice RSS에는 빨간 속보 여부가 직접 들어오지 않아 라이브 `Startup` 데이터의 `Breaking` 값을 함께 조회합니다.
- 텔레그램은 일반 메시지 글자색을 빨간색으로 바꿀 수 없어서 봇에서는 `<b>Financial Juice [속보]</b>` 형태로 구분합니다.
