# 단타 매매 시스템 — 개발 환경 세팅 가이드

> Python 3.14 · Windows 로컬 · venv 격리
> 최종 수정: 2026년 3월

---

## 0. 사전 확인

```bash
python --version     # 3.14.x
```

---

## 1. venv 생성 (완료됨)

```bash
cd D:\project\day-trader
py -3.14 -m venv .venv
.venv\Scripts\activate
```

---

## 2. 의존성 설치 (완료됨)

```bash
pip install -r requirements.txt
```

> **참고**: vectorbt와 pandas-ta는 Python 3.14 미지원 (numba 의존성).
> 백테스트 시 Python 3.12 venv 별도 생성 권장.

---

## 3. .env 파일 설정

`.env.example`을 복사하여 `.env` 생성:

```bash
copy .env.example .env
```

**.env** 파일 편집:
```
KIWOOM_APP_KEY=발급받은_AppKey
KIWOOM_SECRET_KEY=발급받은_SecretKey
KIWOOM_ACCOUNT_NO=계좌번호
TELEGRAM_BOT_TOKEN=텔레그램_봇_토큰
TELEGRAM_CHAT_ID=텔레그램_채팅_ID
LOG_LEVEL=INFO
DEBUG=false
```

### 키움 API 키 발급
1. [키움증권 OpenAPI](https://openapi.kiwoom.com) 접속
2. 앱 등록 → AppKey, SecretKey 발급
3. 모의투자 계좌번호 확인

### 텔레그램 봇 설정
1. BotFather에게 `/newbot` 명령
2. 봇 토큰 발급
3. 채팅 ID 확인: `https://api.telegram.org/bot<TOKEN>/getUpdates`

---

## 4. DB 초기화

```bash
python -c "
import asyncio
from data.db_manager import DbManager
async def init():
    db = DbManager('daytrader.db')
    await db.init()
    await db.close()
    print('DB 초기화 완료')
asyncio.run(init())
"
```

---

## 5. 연동 테스트

### 인증 테스트
```bash
python -c "
import asyncio
from core.auth import TokenManager
async def test():
    tm = TokenManager(
        app_key='YOUR_APP_KEY',
        secret_key='YOUR_SECRET_KEY',
        base_url='https://openapi.koreainvestment.com:9443'
    )
    token = await tm.get_token()
    print(f'Token: {token[:10]}...')
asyncio.run(test())
"
```

### 텔레그램 테스트
```bash
python -c "
import asyncio
from config.settings import AppConfig
from notification.telegram_bot import TelegramNotifier
async def test():
    config = AppConfig()
    notifier = TelegramNotifier(config.telegram)
    ok = await notifier.send('🧪 단타 시스템 테스트 메시지')
    print(f'발송 결과: {ok}')
asyncio.run(test())
"
```

### 현재가 조회 테스트
```bash
python -c "
import asyncio
from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
async def test():
    config = AppConfig()
    tm = TokenManager(config.kiwoom.app_key, config.kiwoom.secret_key, config.kiwoom.rest_base_url)
    client = KiwoomRestClient(config.kiwoom, tm)
    result = await client.get_current_price('005930')
    price = result.get('output', {}).get('stck_prpr', 'N/A')
    print(f'삼성전자 현재가: {price}원')
asyncio.run(test())
"
```

---

## 6. 시스템 실행

```bash
# 가상환경 활성화
.venv\Scripts\activate

# 시스템 시작 (장 전에 실행)
python main.py
```

**스케줄:**
- 08:30 — 자동 스크리닝 시작
- 09:05 — 매매 신호 활성화
- 15:10 — 미청산 포지션 강제 청산
- 15:30 — 일일 보고서 발송

---

## 7. 테스트 실행

```bash
# 전체 테스트
pytest tests/ -v

# 특정 모듈 테스트
pytest tests/test_orb_strategy.py -v

# 커버리지
pytest tests/ --cov=. --cov-report=term-missing
```

---

## 8. 환경 검증 체크리스트

```
[ ] python --version → 3.14.x
[ ] .venv\Scripts\activate 후 pip list에 aiohttp, websockets 포함
[ ] .env 파일 존재, .gitignore에 .env 포함
[ ] main.py에 WindowsSelectorEventLoopPolicy 설정
[ ] daytrader.db 생성 확인 (DB 초기화 후)
[ ] 텔레그램 테스트 메시지 수신 확인
[ ] 키움 API Token 발급 성공 확인
[ ] pytest tests/ → 125 passed
```

---

## 자주 발생하는 문제

### vectorbt/pandas-ta 설치 실패
Python 3.14에서 numba 미지원. 백테스트용으로 Python 3.12 venv 별도 생성:
```bash
py -3.12 -m venv .venv312
.venv312\Scripts\activate
pip install vectorbt pandas-ta
```

### asyncio RuntimeError (Windows)
main.py 상단의 WindowsSelectorEventLoopPolicy 설정 확인

### 모의투자 vs 실거래
- 모의투자: `TTTC0802U` (매수), `TTTC0801U` (매도)
- 실거래: `TTTC0802U` → `VTTC0802U` 등 tr_id 변경 필요
