# GUI Surgery Guide — P0 + P1 개선

## 개요
day-trader GUI를 Paper Trading 운영에 최적화하는 단계별 수술 가이드.
Claude Code CLI에서 순차적으로 실행할 것.

---

## Phase 1: P0 — Paper Trading 전 필수 (3개 항목)

### P0-1: 전략 콤보박스 — 운영 전략만 노출

**파일**: `gui/widgets/sidebar.py`

**현재 상태**: 
```python
self._strategy_combo.addItems([
    "Auto", "Momentum", "Pullback", "Flow",
    "Gap", "OpenBreak", "BigCandle",
])
```

**변경 내용**:
- 운영 전략만 남기기: `"Auto", "Momentum", "Pullback", "Flow"`
- Gap, OpenBreak, BigCandle은 백테스트 미검증 → 제거
- 전략 탭 콤보(`strategy_tab.py`)와 백테스트 탭 콤보(`backtest_tab.py`)는 그대로 유지 (실험용으로 필요)

**변경 파일 목록**:
1. `gui/widgets/sidebar.py` — `_build_engine_status()` 내 `addItems()` 수정

**검증**: GUI 실행 없이 코드 리뷰로 충분. 콤보 아이템 개수가 4개인지 확인.

---

### P0-2: 포지션 테이블에 종목명 컬럼 추가

**파일**: `gui/widgets/dashboard_tab.py`

**현재 상태**:
```python
columns = ["종목코드", "전략", "진입가", "현재가", "수익률", "손절가", "TP1", "상태"]
```
→ 종목코드만 있어서 005930이 삼성전자인지 바로 알 수 없음.

**변경 내용**:
1. `_build_positions_panel()`: columns에 "종목명" 추가 (인덱스 1, "종목코드" 다음)
2. `update_positions()`: cells 배열에 `row_data.get("name", "")` 추가 (인덱스 1)
3. 종목명 컬럼에 blue 색상 적용: `QColor("#89b4fa")`

**변경 후**:
```python
columns = ["종목코드", "종목명", "전략", "진입가", "현재가", "수익률", "손절가", "TP1", "상태"]
```

**cells 배열 변경**:
```python
cells = [
    (row_data.get("ticker", ""), None),
    (row_data.get("name", ""), QColor("#89b4fa")),  # 신규
    (row_data.get("strategy", ""), None),
    # ... 나머지 동일
]
```

**데이터 소스 수정 필요** (`gui/workers/engine_worker.py`):
- 현재 `_emit_positions()`에서 positions dict에 `name` 필드가 **없음**
- `self._active_strategies` dict에 `{ticker: {"name": ..., ...}}` 형태로 이미 존재
- positions 빌드 시 name을 lookup해서 추가해야 함:
  ```python
  active = self._active_strategies.get(ticker, {})
  positions.append({
      "ticker": ticker,
      "name": active.get("name", ""),  # 추가
      # ... 나머지 동일
  })
  ```

**변경 파일 목록**:
1. `gui/widgets/dashboard_tab.py` — columns + cells 수정
2. `gui/workers/engine_worker.py` — `_emit_positions()` 내 name 필드 추가

---

### P0-3: Sidebar 슬림화 — PnL/거래 정보 제거

**파일**: `gui/widgets/sidebar.py`

**현재 상태**: `_build_engine_status()` 안에 전략 정보 + PnL + 거래 횟수가 모두 포함.

**변경 내용**:
Sidebar에서 아래 요소들을 **제거**:
- `self._strategy_label` ("Strategy: —")
- `self._target_label` ("Target: —") 
- `self._pnl_label` ("PnL: —")
- `self._trades_label` ("거래: 0 / 3")

**이유**: 이 정보들은 이미 Dashboard의 summary cards + status bar에 표시됨. Sidebar는 제어 전용으로 남겨야 220px 안에서 숨 쉴 공간이 생김.

**영향 범위**:
1. `gui/widgets/sidebar.py`:
   - `_build_engine_status()`: 4개 label 생성 코드 제거
   - `update_status()`: 해당 label 업데이트 코드 제거 (단, 메서드 시그니처 유지, dot/status_text 업데이트는 유지)
2. `gui/main_window.py`:
   - `_on_status_updated()`: sidebar.update_status() 호출은 그대로 유지 (dot 상태 업데이트 용도로 여전히 필요)

**주의사항**: 
- `update_status()`에서 `self._pnl_label`과 `self._trades_label` 참조하는 코드를 제거할 때, 메서드 상단의 `running`, `halted` 파싱과 dot 업데이트 로직은 반드시 유지
- `self._strategy_label`과 `self._target_label`도 `update_status()`에서 참조하므로 함께 제거

---

## Phase 2: P1 — Paper Trading 중 추가

### P1-1: 대시보드에 일중 PnL 미니 차트

**파일**: `gui/widgets/dashboard_tab.py`

**구현 방향**:
- `pyqtgraph` 라이브러리 사용 (matplotlib보다 실시간 업데이트에 적합)
- `requirements.txt`에 `pyqtgraph` 추가
- Dashboard 레이아웃을 2컬럼으로 변경: 왼쪽(positions+trades), 오른쪽(PnL chart + watchlist)
- PnL 데이터는 `_on_pnl_updated()` 또는 `_on_status_updated()`에서 시계열로 축적
- 차트 높이 120px 정도, 0선 기준으로 양수=green, 음수=red fill

**구현 상세**:
1. `DashboardTab._build_ui()` 레이아웃 변경:
   ```
   summary_bar (가로 4카드)
   ─────────────────────────────
   QSplitter (Horizontal)
   ├── left_panel (Vertical splitter)
   │   ├── positions_panel
   │   └── trades_panel
   └── right_panel (Vertical)
       ├── pnl_chart (pyqtgraph PlotWidget, 120px)
       └── watchlist_panel (나머지 공간)
   ```
2. `update_pnl_chart(timestamp, pnl_value)` 메서드 추가
3. `main_window.py`의 `_on_pnl_updated()` → `dashboard_tab.update_pnl_chart()` 연결

**데이터 구조**:
```python
self._pnl_series: list[tuple[float, float]] = []  # (timestamp, cumulative_pnl)
```

---

### P1-2: 감시 종목 패널

**파일**: `gui/widgets/dashboard_tab.py`

**구현 방향**:
- 우측 패널 하단에 QTableWidget (컬럼: 종목코드, 종목명, ATR%, 서지비율, 전략적합도)
- screener_tab의 `update_candidates()`와 동일한 데이터를 받되, top 5만 표시
- `main_window.py`의 `_on_candidates_updated()`에서 dashboard에도 전달

**구현 상세**:
1. `_build_watchlist_panel()` 메서드 추가
2. `update_watchlist(candidates: list[dict])` public 메서드 추가
3. `main_window.py`의 `_on_candidates_updated()`에 추가:
   ```python
   self.dashboard_tab.update_watchlist(candidates[:5])
   ```

---

### P1-3: 백테스트 Progress 연동

**파일**: `backtest/backtester.py`, `gui/main_window.py`

**현재 문제**: `_on_run_backtest()`에서 progress를 10%로만 설정하고 완료까지 업데이트 없음.

**구현 방향**:
- `Backtester.run_multi_day()`에 `progress_callback: Callable[[int], None] | None = None` 파라미터 추가
- 각 날짜 처리 완료 시 `progress_callback(percentage)` 호출
- `main_window.py`의 `_run_backtest_async()`에서 콜백을 QTimer.singleShot으로 UI 스레드에 전달

---

## Phase 3: P2 — 운영 안정화 후 (참고용)

### P2-1: 전략 탭 잠금 모드
- 엔진 실행 중 → 전략 파라미터 SpinBox/ComboBox 전부 `setEnabled(False)`
- `_on_engine_started()` / `_on_engine_stopped()`에서 토글

### P2-2: Trade History CSV Export
- Dashboard에 "CSV 내보내기" 버튼 추가
- `_trades_table`의 데이터를 pandas DataFrame → CSV 저장

### P2-3: 전략 탭에서 미검증 전략 비활성화 표시
- Gap, OpenBreak, BigCandle 페이지에 "⚠ 미검증" 워닝 라벨 추가
- 파라미터 편집은 가능하되, 시각적으로 실험 단계임을 표시

---

## CLI 실행 프롬프트

### Prompt 1: P0 일괄 실행
```
P0 GUI 수술을 진행해줘. docs/GUI_SURGERY_GUIDE.md의 Phase 1 (P0-1, P0-2, P0-3) 3개 항목을 모두 적용해.

구체적으로:
1. sidebar.py: 전략 콤보에서 Gap, OpenBreak, BigCandle 제거 → "Auto", "Momentum", "Pullback", "Flow" 4개만 남기기

2. dashboard_tab.py: positions 테이블에 "종목명" 컬럼 추가 (인덱스 1, "종목코드" 다음, blue 색상 QColor("#89b4fa")). update_positions()의 cells 배열도 수정.
   + engine_worker.py: _emit_positions() 에서 positions dict에 name 필드 추가. self._active_strategies.get(ticker, {}).get("name", "") 로 조회.

3. sidebar.py: _build_engine_status()에서 _strategy_label, _target_label, _pnl_label, _trades_label 4개 생성 코드 제거. update_status()에서 해당 참조 코드도 제거. 단, running/halted 상태 dot 업데이트 로직은 반드시 유지.

변경 후 pytest 전체 실행해서 기존 테스트 깨지는 거 없는지 확인해줘.
```

### Prompt 2: P1-1 PnL 차트 추가
```
대시보드에 일중 PnL 미니 차트를 추가해줘.

1. requirements.txt에 pyqtgraph 추가
2. dashboard_tab.py 레이아웃을 2컬럼 구조로 변경:
   - 왼쪽: 기존 positions + trades (QSplitter Vertical)
   - 오른쪽: PnL 차트 (높이 120px) + 나머지 공간은 빈 QWidget (추후 감시종목용)
3. PnL 차트: pyqtgraph PlotWidget, 0선 기준 green/red fill, x축=시간, y축=원
4. update_pnl_chart(timestamp: float, value: float) 메서드 추가
5. main_window.py의 _on_pnl_updated()에서 dashboard_tab.update_pnl_chart() 호출하도록 연결
   - pnl_updated 시그널이 float만 넘기므로, time.time()으로 timestamp 직접 생성

pyqtgraph가 import 안 되면 graceful fallback으로 빈 QFrame 표시.
pytest 돌려서 깨지는 거 없는지 확인.
```

### Prompt 3: P1-2 감시종목 패널
```
대시보드 우측 하단(PnL 차트 아래)에 감시 종목 패널을 추가해줘.

1. dashboard_tab.py에 _build_watchlist_panel() 메서드 추가
   - QTableWidget: 컬럼 = ["종목코드", "종목명", "ATR%", "서지", "점수"]
   - 최대 5행, 읽기전용, 행 번호 숨김
2. update_watchlist(candidates: list[dict]) public 메서드 추가
   - expected keys: ticker, name, atr_pct, volume_surge, score
3. P1-1에서 만든 우측 패널의 빈 QWidget 자리에 watchlist_panel 배치
4. main_window.py의 _on_candidates_updated()에 추가:
   self.dashboard_tab.update_watchlist(candidates[:5])

pytest 확인.
```

---

## 검증 체크리스트

- [ ] Sidebar 전략 콤보: 4개 아이템 (Auto, Momentum, Pullback, Flow)
- [ ] Positions 테이블: 9개 컬럼 (종목명 추가)
- [ ] Sidebar에서 PnL/거래/Strategy/Target 라벨 제거됨
- [ ] update_status()의 dot 업데이트 로직 정상 동작
- [ ] pytest 전체 통과
- [ ] (P1) PnL 차트 위젯 존재, pyqtgraph 미설치 시 fallback
- [ ] (P1) 감시종목 테이블 최대 5행 표시
