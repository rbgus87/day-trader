# Daytrading System — Product Requirements Document

> **PRD v1.0** · 2026년 3월  
> 키움증권 REST API 기반 단타 자동매매 시스템  
> Python 3.12 · Windows 로컬 · venv 격리

---

## 문서 정보

| 항목 | 내용 |
|------|------|
| 문서 버전 | v1.0 |
| 작성일 | 2026년 3월 |
| 대상 시스템 | kiwoom_daytrader |
| Python 버전 | 3.12 (venv 격리) |
| 운영 환경 | Windows 로컬 |
| 브로커 | 키움증권 REST API + WebSocket |
| 우선순위 기준 | P0: 필수(블로커) / P1: 높음 / P2: 중간 / P3: 낮음 |

---

## 목차

1. [목표 및 범위](#1-목표-및-범위)
2. [사용자 스토리](#2-사용자-스토리)
3. [기능 요구사항](#3-기능-요구사항)
4. [비기능 요구사항](#4-비기능-요구사항)
5. [기술 제약사항](#5-기술-제약사항)
6. [API 인터페이스](#6-api-인터페이스)
7. [데이터 모델](#7-데이터-모델)
8. [수용 기준 체크리스트](#8-수용-기준-체크리스트)

---

## 1. 목표 및 범위

### 1.1 제품 목표

키움증권 REST API와 WebSocket을 활용하여 하루 1개 종목을 선별하고 당일 매매를 완전 자동화한다.  
기존 퀀트/스윙 시스템의 인프라를 재사용하고, 단타 전용 독립 DB와 venv 환경으로 격리된 구조를 유지하는 것이 핵심 목표다.

### 1.2 Scope In / Out

**✅ In**
- 키움 REST API 주문/조회
- WebSocket 실시간 체결/호가/체결통보
- 4개 전략 구현 (ORB, VWAP, 모멘텀, 눌림목)
- 1종목 집중 투자, 분할 매도
- 포지션/계좌 레벨 리스크 관리
- 텔레그램 알림 (기존 재사용 + 단타 타입 추가)
- 백테스트 엔진 (vectorbt 기반)
- `daytrader.db` 독립 SQLite DB

**❌ Out**
- 멀티 종목 동시 매매
- 타 증권사 API 연동
- 웹 대시보드 UI (Grafana는 선택 사항)
- 자동 전략 파라미터 최적화 (머신러닝 기반)
- 야간/해외 시장 매매

---

## 2. 사용자 스토리

| ID | 스토리 | 우선순위 |
|----|--------|---------|
| US-01 | 나는 매일 아침 자동으로 종목이 선별되어 매매 준비가 완료되기를 원한다 | P0 |
| US-02 | 나는 전략 신호 발생 즉시 자동으로 주문이 집행되기를 원한다 | P0 |
| US-03 | 나는 손절라인에 도달하면 예외 없이 즉시 청산되기를 원한다 | P0 |
| US-04 | 나는 일일 최대 손실 도달 시 시스템이 자동으로 매매를 중단하기를 원한다 | P0 |
| US-05 | 나는 15:10에 미청산 포지션이 자동으로 전량 청산되기를 원한다 | P0 |
| US-06 | 나는 WS 연결이 끊겨도 포지션이 안전하게 관리되기를 원한다 | P0 |
| US-07 | 나는 텔레그램으로 매매 신호, 체결, 손익을 실시간으로 받기를 원한다 | P1 |
| US-08 | 나는 오전 8:30에 자동으로 스크리닝이 실행되기를 원한다 | P1 |
| US-09 | 나는 장 종료 후 일일 성과 보고서를 텔레그램으로 받기를 원한다 | P1 |
| US-10 | 나는 과거 데이터로 전략을 백테스트할 수 있기를 원한다 | P1 |
| US-11 | 나는 전략 조건이 없는 날에도 시스템이 정상 상태임을 알기를 원한다 | P2 |

---

## 3. 기능 요구사항

> 형식: `[우선순위] ID — 제목`

---

### 3.1 인증 및 API 클라이언트

#### `[P0]` F-AUTH-01 — OAuth2 토큰 자동 발급

- **설명**: AppKey/SecretKey를 `.env`에서 로드하여 Bearer Token을 발급한다
- **수용 기준**
  - Token 발급 성공
  - `.env` 파일 없을 시 명확한 에러 메시지 출력 (시스템 시작 중단)

#### `[P0]` F-AUTH-02 — Token 자동 갱신

- **설명**: 만료 전 선제적으로 Token을 갱신하여 매매 중 인증 실패를 방지한다
- **수용 기준**
  - 만료 10분 전 자동 갱신 실행
  - 갱신 실패 시 텔레그램 긴급 알림

#### `[P0]` F-AUTH-03 — REST API 재시도 (`core/retry.py`)

- **설명**: 공통 모듈로 Exponential Backoff + Jitter 재시도를 구현한다. 퀀트/스윙/단타 세 시스템이 동일 모듈을 공유한다
- **수용 기준**
  - `429` → `Retry-After` 헤더 준수
  - 손절 주문 3회 실패 시 텔레그램 긴급 알림 + 수동 개입 요청

---

### 3.2 WebSocket 클라이언트

#### `[P0]` F-WS-01 — 실시간 데이터 구독

- **설명**: 체결, 호가, 체결통보, 조건검색 4개 채널을 동시 구독한다
- **수용 기준**
  - 4채널 동시 구독 성공
  - 수신 틱을 `asyncio.Queue`로 캔들 빌더에 전달

#### `[P0]` F-WS-02 — 자동 재연결

- **설명**: 연결 끊김 감지 시 자동 재연결 및 포지션 긴급 점검을 수행한다
- **수용 기준**
  - 끊김 감지 후 5초 이내 재연결 시도
  - 재연결 실패 시 REST 긴급 청산 + 텔레그램 알림

#### `[P0]` F-WS-03 — Heartbeat 관리

- **설명**: Ping-Pong으로 연결 상태를 주기적으로 확인한다
- **수용 기준**
  - 30초 간격 Ping 발송
  - Pong 미수신 시 재연결 트리거

---

### 3.3 캔들 빌더 (`data/candle_builder.py`)

#### `[P0]` F-CANDLE-01 — 실시간 분봉 생성

- **설명**: WS 틱 데이터로 1분봉/5분봉 캔들을 실시간으로 생성한다
- **수용 기준**
  - 각 분봉 완성 시 전략 엔진 `Queue`에 즉시 전달
  - 틱 대비 OHLCV 정확도 ±0.1% 이내

#### `[P0]` F-CANDLE-02 — VWAP 실시간 계산

- **설명**: 당일 누적 `(price × volume) / 누적 volume`으로 1분봉마다 VWAP을 갱신한다
- **수용 기준**
  - 09:00부터 누적 계산
  - 09:00~09:05 데이터는 VWAP 계산에 포함, 신호 트리거에서는 제외

#### `[P1]` F-CANDLE-03 — 캔들 DB 저장

- **설명**: 완성된 분봉을 `intraday_candles` 테이블에 저장한다
- **수용 기준**
  - `UNIQUE(ticker, tf, ts)` 제약으로 중복 저장 방지
  - 저장 실패 시 로그만 기록, 매매 흐름 중단 없음

---

### 3.4 종목 스크리너

#### `[P0]` F-SCR-01 — 장 전 스크리닝 (08:30)

- **설명**: APScheduler로 08:30에 자동 실행, REST API로 전일 데이터 조회 후 후보 5~10종목을 선정한다
- **수용 기준**
  - 기본 / 기술 / 수급 / 이벤트 4단계 필터 순차 적용
  - 결과를 `screener_results` 테이블에 저장

**필터 기준:**

| 단계 | 조건 |
|------|------|
| 기본 | 시가총액 3,000억 이상, 일 평균 거래대금 50억 이상, 관리/투자주의 제외 |
| 기술 | 20일 이평 상향 배치, 전일 거래량 +50% 이상, ATR(14) 일중 변동성 2% 이상 |
| 수급 | 기관 순매수 상위 우선, 외국인 순매수 전환 가산점 |
| 이벤트 | 실적 발표 / 주요 공시 예정 종목 제외 |

#### `[P0]` F-SCR-02 — 전략 자동 선택

- **설명**: 시장 상황을 판단하여 당일 전략을 자동 선택한다
- **수용 기준**
  - 우선순위 순서 적용: ORB > 모멘텀 > VWAP > 눌림목
  - **폴백**: 어떤 조건도 미해당 시 "당일 매매 없음" + 텔레그램 알림

**선택 기준:**

| 조건 | 전략 |
|------|------|
| KOSPI +0.5% 이상 갭 | ORB |
| 특정 섹터 ETF +1.5% 이상 | 모멘텀 브레이크아웃 |
| 지수 변동 ±0.5% 이내 | VWAP 회귀 |
| 개별 종목 상승 후 조정 | 눌림목 매매 |
| 조건 없음 | 당일 매매 없음 |

#### `[P1]` F-SCR-03 — 장 중 실시간 스크리닝

- **설명**: WS 데이터로 거래량 급등, 호가 매수벽, 체결강도를 실시간 모니터링한다
- **수용 기준**
  - 5분 거래량이 20분 평균의 3배 이상 시 알림

#### `[P0]` F-SCR-04 — 개장 초반 신호 차단

- **설명**: 09:00~09:05 구간에서 전략 신호 발생을 차단한다
- **수용 기준**
  - 09:05 이전 신호는 `Queue`에 넣지 않음
  - ORB 레인지 측정 시작 시점은 09:05

---

### 3.5 전략 엔진 (`strategy/`)

#### `[P0]` F-STR-05 — 전략 베이스 클래스 (`base_strategy.py`)

- **설명**: ABC로 공통 인터페이스를 강제하여 신규 전략 추가 시 파이프라인 자동 연결을 보장한다
- **수용 기준**
  - `generate_signal()`, `get_stop_loss()`, `get_take_profit()` 구현 강제
  - `is_tradable_time()` 기본 구현 제공 (09:05 이전 차단)

```python
class BaseStrategy(ABC):
    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수/매도 신호 생성. 신호 없으면 None 반환"""

    @abstractmethod
    def get_stop_loss(self, entry_price: float) -> float:
        """전략별 손절가 계산"""

    @abstractmethod
    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """1차, 2차 익절가 반환 (tp1, tp2)"""

    def is_tradable_time(self) -> bool:
        """09:05 이전 신호 차단 — 기본 구현 제공"""
```

#### `[P0]` F-STR-01 — ORB 전략

- **설명**: 09:05~09:15 오프닝 레인지 형성 후 상단 돌파 + 거래량 확인 시 매수 신호를 생성한다
- **수용 기준**
  - 레인지 상단 돌파 + 거래량 전일 대비 150% 이상 + 5분봉 종가 확인 → 신호 발생
  - 손절: 매수가 -1.5%
  - 1차 익절: +2% → 50% 매도 → 손절 본전 이동
  - 2차 익절: 고점 대비 -1.0% 트레일링

#### `[P1]` F-STR-02 — VWAP 회귀 전략

- **설명**: VWAP 하단 터치 후 반등 시 매수, VWAP +1σ 도달 시 1차 익절한다
- **수용 기준**
  - RSI(14) 40~60 필터 + VWAP 상향 돌파 조합 필수
  - 손절: VWAP -1σ 이탈 또는 -1.2%

#### `[P1]` F-STR-03 — 모멘텀 브레이크아웃

- **설명**: 전일 고점 돌파 후 리테스트 지지 확인 → 재돌파 시 진입한다
- **수용 기준**
  - 전일 거래량 200% 이상 + 현재가 > 전일 고가 스크리닝 통과 필수

#### `[P1]` F-STR-04 — 눌림목 매매

- **설명**: 당일 +3% 이상 종목의 5분 이평 터치 후 음봉→양봉 전환 시 진입한다
- **수용 기준**
  - 20분 이평 정배열 확인 필수
  - 손절: 20분 이평 이탈 또는 -1.5%

---

### 3.6 주문 실행기 (`core/order_manager.py`)

#### `[P0]` F-ORD-01 — 분할 매수

- **설명**: 1차(50~60%) 진입 후 돌파 확인 시 2차(나머지) 매수를 실행한다
- **수용 기준**
  - 1차 체결 확인 후 2차 주문 발송
  - 1차 미체결 시 2차 보류

#### `[P0]` F-ORD-02 — 분할 매도

- **설명**: 1차 목표가 도달 시 50% 매도, 이후 트레일링 스톱으로 나머지를 관리한다
- **수용 기준**
  - 1차 매도 체결 확인 후 손절라인을 본전으로 이동

#### `[P0]` F-ORD-03 — 중복 주문 방지

- **설명**: `asyncio.Lock`으로 동일 종목 중복 매수 시그널을 차단한다
- **수용 기준**
  - 주문 처리 중 동일 종목 신호 무시
  - 처리 완료 후 Lock 해제

#### `[P0]` F-ORD-04 — 체결 확인

- **설명**: REST 주문 후 WS 체결통보를 5초 대기, 미확인 시 REST로 재조회한다
- **수용 기준**
  - 5초 타임아웃 후 REST 재조회 1회
  - 이후에도 미확인 시 텔레그램 알림

#### `[P0]` F-ORD-05 — 주문 유형 매핑

- **설명**: 상황별 최적 주문 유형을 적용한다
- **수용 기준**
  - 최유리 지정가 API 지원 시 진입에 적용, 미지원 시 시장가 폴백

| 상황 | 주문 유형 | 이유 |
|------|-----------|------|
| 진입 | 최유리 지정가 | 즉시 체결 + 슬리피지 최소화 |
| 손절 | 시장가 | 속도 우선 |
| 익절 1차 | 지정가 | 목표가 선주문 |
| 익절 2차 트레일링 | 조건부 시장가 | 고점 -X% 즉시 체결 |
| 15:10 강제 청산 | 시장가 | 속도 우선 |

---

### 3.7 리스크 관리 (`risk/risk_manager.py`)

#### `[P0]` F-RISK-01 — 개별 종목 손절

- **설명**: 매수가 대비 -1.5~2% 도달 즉시 시장가 매도를 실행한다
- **수용 기준**
  - 전략 파라미터로 손절률 설정
  - asyncio 이벤트 루프 내 즉시 주문 발송

#### `[P0]` F-RISK-02 — 일일 최대 손실 차단

- **설명**: 당일 누적 손실 -2% 도달 시 매매 중단 및 전 포지션 청산
- **수용 기준**
  - 실시간 손실 계산
  - 도달 즉시 신규 신호 차단 + 텔레그램 알림

#### `[P0]` F-RISK-03 — 강제 청산 (15:10)

- **설명**: APScheduler 15:10 트리거로 미청산 포지션 전량을 시장가 청산한다
- **수용 기준**
  - WS 체결통보로 전량 체결 확인
  - 체결 미확인 시 REST 재조회

#### `[P1]` F-RISK-04 — 연속 손실 제한

- **설명**: 3일 연속 손실 시 다음날 포지션 사이즈를 50%로 자동 축소한다
- **수용 기준**
  - `daily_pnl` 테이블에서 최근 3일 손실 여부 확인
  - `config/settings.py`의 포지션 사이즈 파라미터 자동 조정

#### `[P0]` F-RISK-05 — 장애 복구

- **설명**: 시스템 재시작 시 REST API로 미청산 포지션을 자동 감지하고 처리한다
- **수용 기준**
  - 시작 시 `positions` 테이블 open 상태와 API 잔고 대조
  - 불일치 시 텔레그램 알림 + 수동 처리 대기

---

### 3.8 알림 시스템 (`notification/telegram_bot.py`)

#### `[P1]` F-NOTI-01 — 매매 알림

- **설명**: 신호 발생, 체결, 손절, 익절 시 텔레그램 메시지를 발송한다
- **수용 기준**
  - 이벤트 발생 후 3초 이내 발송
  - 각 이벤트별 포맷 정의

#### `[P0]` F-NOTI-02 — 긴급 알림

- **설명**: 손절 주문 실패, WS 재연결 실패, 일일 손실 도달 시 즉시 발송한다
- **수용 기준**
  - 일반 Queue 우회, 즉시 발송 처리

#### `[P1]` F-NOTI-03 — 일일 보고서

- **설명**: 15:30에 당일 매매 성과 보고서를 자동 발송한다
- **수용 기준**
  - 총 수익률, 승률, 전략별 성과, 현재 잔고 포함

#### `[P2]` F-NOTI-04 — 매매 없음 알림

- **설명**: 전략 조건 미해당 시 시장 상황 요약과 함께 당일 매매 없음을 알린다
- **수용 기준**
  - 08:55~09:00 사이 전략 미선택 확정 시 발송

---

### 3.9 백테스트 (`backtest/backtester.py`)

#### `[P0]` F-BT-02 — 분봉 데이터 수집 배치 (선행 작업)

- **설명**: 키움 REST API로 과거 분봉을 수집하여 `intraday_candles`에 저장한다
- **수용 기준**
  - 1회 900개 제한 → 페이지네이션 처리
  - Rate Limit 준수, 야간/주말 배치 실행
  - 2년치 주요 종목 1분봉 수집 완료

#### `[P1]` F-BT-01 — 전략 백테스트

- **설명**: `intraday_candles` DB에서 과거 분봉을 로드하여 전략을 시뮬레이션한다
- **수용 기준**
  - 수수료/슬리피지/09:00~09:05 차단 로직 실전과 동일 적용
  - KPI 자동 계산 (승률, Profit Factor, MDD, Sharpe)

---

## 4. 비기능 요구사항

### 4.1 성능

| 항목 | 요구사항 |
|------|---------|
| 주문 지연 | 신호 발생 → REST 주문 발송 500ms 이내 |
| WS 처리 | 틱 수신 → Queue 전달 10ms 이내 (이벤트 루프 블로킹 없음) |
| 캔들 생성 | 1분봉 완성 → 전략 신호 판단 1초 이내 |
| DB 쓰기 | 체결 내역 저장 비동기 처리, 매매 지연 없음 |

### 4.2 안정성

| 항목 | 요구사항 |
|------|---------|
| WS 재연결 | 끊김 감지 후 5초 이내 재연결 시도 |
| Token 갱신 | 만료 10분 전 선제 갱신, 실패 시 즉각 알림 |
| 장애 복구 | 재시작 후 미청산 포지션 자동 감지 및 처리 |
| 손절 신뢰성 | 손절 주문 3회 실패 시 긴급 알림, 미처리 불가 상태 방지 |

### 4.3 보안

| 항목 | 요구사항 |
|------|---------|
| AppKey/SecretKey | `.env` 파일 저장, git 커밋 절대 금지 (`.gitignore` 포함) |
| `.env.example` | 키 구조만 포함한 템플릿 파일 git 관리 |
| 로그 마스킹 | 로그에 AppKey, Token 값 마스킹 처리 |

### 4.4 유지보수

| 항목 | 요구사항 |
|------|---------|
| 전략 확장성 | `base_strategy.py` ABC 상속으로 신규 전략 파이프라인 자동 연결 |
| 파라미터 관리 | 손절률, 익절 목표, 스코어링 가중치 → `config/settings.py` 단일 관리 |
| 공통 모듈 | `retry.py`, `auth.py` → 퀀트/스윙/단타 세 시스템 단일 소스 유지 |
| 로깅 | loguru JSON 구조화 로그, 일별 rotation, 기존 시스템과 동일 포맷 |

---

## 5. 기술 제약사항

| 항목 | 내용 | 비고 |
|------|------|------|
| Python | 3.12 (venv) | 3.14는 라이브러리 호환 리스크로 미채택 |
| 운영 환경 | Windows 로컬 | `asyncio.WindowsSelectorEventLoopPolicy` 필수 설정 |
| 이벤트 루프 | SelectorEventLoop | ProactorEventLoop → websockets 충돌 가능 |
| 기술적 지표 | pandas-ta | ta-lib Windows 설치 복잡 → pandas-ta 대체 |
| DB | SQLite (`daytrader.db`) | `quant.db` / `swing.db` 완전 분리 |
| Rate Limit | 키움 REST API 제한 준수 | 초당 요청 횟수 openapi.kiwoom.com 확인 |
| WS 구독 수 | 키움 API 제한 확인 필요 | 한투 기준 40개, 키움 별도 확인 |
| 분봉 조회 | 1회 최대 900개 | 페이지네이션 처리 필수 |
| NXT 거래소 | 계좌번호 형식 상이 가능 | `123456_NX` 형식 확인 필요 |

---

## 6. API 인터페이스

### 6.1 REST API

| 엔드포인트 | HTTP | 용도 | 비고 |
|-----------|------|------|------|
| `/oauth2/token` | POST | Token 발급 | AppKey+SecretKey → Bearer Token |
| `/주식시세` | GET | 현재가/분봉/일봉 | 장 전 스크리닝, 백테스트 수집 |
| `/호가` | GET | 10단계 호가 | 호가창 분석 |
| `/주문` | POST | 매수/매도 | 신규/정정/취소 |
| `/계좌잔고` | GET | 보유종목+예수금 | 포지션 관리, 장애 복구 |
| `/체결내역` | GET | 당일 체결 | 일일 보고서 생성 |

### 6.2 WebSocket 구독 채널

| 채널 | 용도 | 주요 필드 |
|------|------|----------|
| 주식체결 | 실시간 틱 수신 | 체결시각, 현재가, 체결량, 누적거래량 |
| 주식호가 | 실시간 호가 | 매수/매도 10단계 호가+잔량 |
| 체결통보 | 내 주문 체결 | 주문번호, 체결가, 체결량, 매수/매도 구분 |
| 조건검색 | 조건 종목 편입/이탈 | 실시간 편입/이탈 종목 |

### 6.3 asyncio 파이프라인 흐름

```
[WS 수신 태스크]         ← websockets, 틱 수신
    ↓ asyncio.Queue      (raw tick)
[캔들 빌더 태스크]        ← 1분/5분봉 생성, VWAP 계산
    ↓ asyncio.Queue      (완성 캔들 + 지표)
[전략 엔진 태스크]        ← 신호 판단 (CPU-bound → run_in_executor)
    ↓ asyncio.Queue      (주문 신호)
[주문 실행 태스크]        ← REST API 호출, asyncio.Lock 중복 방지
    ↓
[체결 확인 태스크]        ← WS 체결통보 대기 (5초 타임아웃 후 REST 재조회)
```

---

## 7. 데이터 모델

### 7.1 DB 구성

> `daytrader.db` — `quant.db` / `swing.db` 와 완전 분리된 독립 SQLite 파일

| 테이블 | 용도 |
|--------|------|
| `trades` | 체결 내역 (매수/매도 각 1행) |
| `positions` | 현재 포지션 (진입 시 생성, 청산 시 closed) |
| `daily_pnl` | 일별 성과 집계 |
| `intraday_candles` | 1분/5분봉 실시간 저장 + 백테스트용 |
| `screener_results` | 날짜별 스크리닝 후보 종목 기록 |
| `system_log` | WS 재연결, 주문 실패 등 중요 이벤트 |

### 7.2 스키마

```sql
-- trades
CREATE TABLE trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    strategy     TEXT NOT NULL,   -- orb/vwap/momentum/pullback
    side         TEXT NOT NULL,   -- buy/sell
    order_type   TEXT NOT NULL,   -- market/limit/best_limit
    price        REAL NOT NULL,
    qty          INTEGER NOT NULL,
    amount       REAL NOT NULL,
    pnl          REAL,            -- 청산 확정 시
    pnl_pct      REAL,
    exit_reason  TEXT,            -- tp1/tp2/trailing/stop/force_close
    traded_at    TEXT NOT NULL,   -- ISO8601
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

-- positions
CREATE TABLE positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    qty           INTEGER NOT NULL,
    remaining_qty INTEGER NOT NULL,
    stop_loss     REAL NOT NULL,
    tp1_price     REAL,
    tp2_price     REAL,
    trailing_pct  REAL,
    status        TEXT DEFAULT 'open',  -- open/closed
    opened_at     TEXT NOT NULL,
    closed_at     TEXT
);

-- intraday_candles
CREATE TABLE intraday_candles (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT NOT NULL,
    tf      TEXT NOT NULL,   -- 1m/5m
    ts      TEXT NOT NULL,   -- ISO8601 캔들 시작 시각
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  INTEGER,
    vwap    REAL,
    UNIQUE(ticker, tf, ts)
);

-- daily_pnl
CREATE TABLE daily_pnl (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL UNIQUE,  -- YYYY-MM-DD
    strategy      TEXT,
    total_trades  INTEGER DEFAULT 0,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    win_rate      REAL,
    total_pnl     REAL DEFAULT 0,
    max_drawdown  REAL,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

-- screener_results
CREATE TABLE screener_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    score         REAL,
    strategy_hint TEXT,
    selected      INTEGER DEFAULT 0,  -- 1: 최종 선택
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

-- system_log
CREATE TABLE system_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT NOT NULL,  -- INFO/WARN/ERROR/CRITICAL
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

---

## 8. 수용 기준 체크리스트

개발 완료 후 아래 항목을 순서대로 검증한다.

### Phase 1 — 기반 구축

- [ ] `py -3.12 --version` → `3.12.x` 출력
- [ ] `py -3.14 --version` → `3.14.x` 출력 (기존 환경 유지)
- [ ] `.venv\Scripts\activate` 후 `python --version` → `3.12.x`
- [ ] `pip list`에 `pandas-ta`, `aiohttp`, `websockets` 포함
- [ ] `main.py`에 `WindowsSelectorEventLoopPolicy` 설정 확인
- [ ] `.env` 파일 존재, `.gitignore`에 `.env` 포함
- [ ] `daytrader.db` 생성, 6개 테이블 스키마 확인
- [ ] 키움 API Token 발급 성공
- [ ] 텔레그램 테스트 메시지 수신 확인
- [ ] WS 4채널 구독 후 틱 데이터 수신 확인

### Phase 2 — 전략 구현

- [ ] ORB 전략 09:05 이전 신호 발생 없음 확인
- [ ] ORB 전략 백테스트 완료 (KPI: 승률 55% 이상, Profit Factor 1.5 이상)
- [ ] VWAP / 모멘텀 / 눌림목 전략 구현 및 단위 테스트 통과
- [ ] 전략 선택 자동화 — 폴백(당일 매매 없음) 동작 확인
- [ ] 스크리너 08:30 자동 실행 확인

### Phase 3 — 리스크 관리

- [ ] 손절라인 도달 시 즉시 시장가 주문 발송 확인
- [ ] 일일 손실 -2% 도달 시 신규 신호 차단 확인
- [ ] 15:10 강제 청산 동작 확인
- [ ] WS 재연결 후 포지션 안전 유지 확인
- [ ] 주문 실패 시 텔레그램 긴급 알림 수신 확인
- [ ] 모의투자 2주 페이퍼 트레이딩 완료

### Phase 4 — 실전 배치

- [ ] 소액 실전 투입 전 위 모든 항목 통과
- [ ] Windows 자동 시작 설정 (작업 스케줄러 또는 `.bat`)
- [ ] `daytrader.db` 백업 정책 수립

---

*이 문서는 개발 진행에 따라 갱신됩니다. 변경 시 버전과 날짜를 업데이트하세요.*
