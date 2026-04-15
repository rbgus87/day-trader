# day-trader 검증 명령어 모음

시스템 상태를 사실 기반으로 확인하기 위한 명령어 모음.
각 항목의 "기대 결과"가 다르면 코드 또는 이 문서가 outdated.

## 환경
```
python gui.py --selftest
```
기대: 7/7 OK

## 운영 전략
```
findstr /S /C:"MomentumStrategy" gui/workers/engine_worker.py
```
기대: import + strategy_classes에 momentum만 존재

## archive 전략 격리
```
findstr /S /C:"from strategy.flow" /C:"from strategy.pullback" /C:"from strategy.gap" /C:"from strategy.open_break" /C:"from strategy.big_candle" *.py gui/ core/ risk/
```
(strategy/archive/ 제외)
기대: 0건

## 청산 경로
```
findstr /S /C:"exit_reason=" core/ risk/ gui/workers/
```
기대: stop_loss, tp1_hit, trailing_stop, forced_close, rebuild_stop(일회성) 만 존재. time_stop 없음.

## time_stop 완전 제거
```
findstr /S /C:"time_stop" /C:"check_time_stop" *.py core/ risk/ gui/
```
(archive/ scripts/ 제외)
기대: 0건

## dead config keys
```
findstr /S /C:"orb_" /C:"vwap_rsi" /C:"momentum_retest" /C:"momentum_trailing_stop_pct" config/ *.yaml
```
기대: 0건

## DB 상태
```
sqlite3 daytrader.db "SELECT COUNT(*) FROM positions WHERE status='open'"
```
기대: 0 (재조립 중)

## 포지션 사이징 (재조립 대상)
```
findstr /S /C:"0.02" /C:"risk_amount" /C:"position_capital" gui/workers/ core/
```
기대: 재조립 후 제거 또는 config 분리 예정

## 백테스트 사이징
```
findstr /S /C:"position_size" /C:"shares" /C:"capital" /C:"buy_amount" backtest/backtester.py
```
기대: 0건 (1주 단위 비율 시뮬, 자본 개념 없음)

---
마지막 검증일: 2026-04-15
다음 갱신: Phase 2 영역별 재조립 완료 시
