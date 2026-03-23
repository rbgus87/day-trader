# PROJECT.md - 단타 자동매매 시스템

## 프로젝트 정보

```yaml
project_name: "day-trader"
description: "키움증권 REST API 기반 단타 자동매매 시스템"
version: "0.1.0"
project_type: "cli"
platforms: [cli]
```

---

## ⚡ CLI 스택 설정

```yaml
cli:
  type: "daemon"                # 장 시간 동안 상주하는 데몬형 도구

  language: "Python"
  python:
    version: "3.14"
    async: "asyncio"            # SelectorEventLoop (Windows)
    packaging: "pip"            # pip + requirements.txt
    framework: "custom"         # 자체 asyncio 기반 파이프라인

  core_dependencies:
    - aiohttp                   # REST API 클라이언트
    - websockets                # WebSocket 실시간 데이터
    - pandas                    # 데이터 처리
    - numpy                     # 수치 연산
    - pandas-ta                 # 기술적 지표 (ta-lib 대체)
    - python-dotenv             # 환경변수 관리
    - python-telegram-bot       # 텔레그램 알림
    - apscheduler               # 스케줄링 (08:30 스크리닝, 15:10 청산)
    - loguru                    # 구조화 로깅
    - vectorbt                  # 백테스트 엔진

  dev_dependencies:
    - pytest                    # 테스트
    - pytest-asyncio            # 비동기 테스트

  database:
    engine: "sqlite"
    file: "daytrader.db"        # quant.db / swing.db 와 완전 분리

  distribution:
    binary: false
    cross_compile: false
    targets:
      - windows-x64             # Windows 로컬 전용
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer                  # CLI 데몬 — UI 없음
    - frontend                  # 웹 프론트엔드 없음
    - accessibility             # CLI 데몬 — 접근성 해당 없음
  auto_security_review: true    # API 키, 주문 로직 보안 필수
  default_mode: "hybrid"
```

## 프로젝트 컨벤션

```yaml
conventions:
  code_style:
    python:
      formatter: "black"
      linter: "ruff"
      type_checker: "none"      # 초기 단계 — 필요 시 mypy 추가

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## 아키텍처

```yaml
architecture:
  pattern: "asyncio pipeline"
  # WS 수신 → Queue → 캔들 빌더 → Queue → 전략 엔진 → Queue → 주문 실행

  modules:
    config/: "설정 및 파라미터 (settings.py)"
    core/: "인증, REST 클라이언트, WS 클라이언트, 주문 관리, 재시도"
    strategy/: "전략 엔진 (ORB, VWAP, 모멘텀, 눌림목)"
    screener/: "종목 스크리너 (장 전/장 중)"
    risk/: "리스크 관리 (손절, 일일 한도, 강제 청산)"
    data/: "캔들 빌더, DB 매니저"
    notification/: "텔레그램 알림"
    backtest/: "백테스트 엔진 (vectorbt)"
    tests/: "pytest 테스트"

  async_note: |
    Windows 환경에서 asyncio.WindowsSelectorEventLoopPolicy 필수 설정
    ProactorEventLoop → websockets 충돌 방지
```

## 환경변수

```yaml
env_vars:
  required:
    - KIWOOM_APP_KEY            # 키움 API AppKey
    - KIWOOM_SECRET_KEY         # 키움 API SecretKey
    - KIWOOM_ACCOUNT_NO         # 계좌번호
    - TELEGRAM_BOT_TOKEN        # 텔레그램 봇 토큰
    - TELEGRAM_CHAT_ID          # 텔레그램 채팅 ID
  optional:
    - LOG_LEVEL                 # 로그 레벨 (기본: INFO)
    - DEBUG                     # 디버그 모드
    - NO_COLOR                  # 컬러 출력 비활성화
```

## 성능 요구사항

```yaml
performance:
  order_latency: 500            # ms — 신호 → REST 주문 발송
  ws_tick_processing: 10        # ms — 틱 수신 → Queue 전달
  candle_to_signal: 1000        # ms — 분봉 완성 → 전략 판단
  db_write: "async"             # 비동기 — 매매 지연 없음
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
  reference_docs:
    - docs/daytrading_prd.md    # PRD 상세
    - docs/dev_setup.md         # 개발 환경 세팅 가이드
```
