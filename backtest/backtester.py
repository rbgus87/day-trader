"""backtest/backtester.py — 전략 백테스트 엔진 (pure pandas, vectorbt 미사용).

PRD F-BT-01: intraday_candles DB에서 과거 분봉 로드, 전략 시뮬레이션, 수수료 + 슬리피지, KPI 계산.
"""

import math
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from config.settings import BacktestConfig, TradingConfig
from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs
from core.exit_logic import compute_momentum_fade, get_time_decay_multiplier
from data.db_manager import DbManager
from strategy.base_strategy import BaseStrategy, Signal


def compute_atr_pct_from_candles(candles: pd.DataFrame, length: int = 14) -> float | None:
    """분봉 캔들에서 일봉 ATR% 계산 (마지막 유효 ATR 반환).

    분봉을 일봉 OHLC로 압축한 뒤 ATR(length) / close 로 백분율 환산.
    length+1일 미만이면 None 반환.
    """
    if candles.empty:
        return None
    df = candles.copy()
    if "date" not in df.columns:
        df["date"] = df["ts"].dt.date
    daily = (
        df.groupby("date")
        .agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
        .reset_index()
    )
    if len(daily) < length + 1:
        return None
    from core.indicators import calculate_atr, calculate_atr_pct
    atr = calculate_atr(daily, length=length)
    atr_pct_series = calculate_atr_pct(atr, daily["close"])
    valid = atr_pct_series.dropna()
    # calculate_atr_pct는 백분율(5.0 = 5%) → 소수로 변환 (0.05)
    return float(valid.iloc[-1]) / 100.0 if not valid.empty else None


def calc_sizing_position_value(
    config: Any,
    atr_pct: float,
    capital: float,
) -> float:
    """변동성 기반 포지션 금액 계산.

    Returns:
        position_value: 해당 거래에 투입할 금액 (₩)
    """
    risk_amount = capital * config.risk_per_trade_pct
    multiplier = config.sizing_atr_multiplier
    pos_val = risk_amount / (atr_pct * multiplier)
    min_val = capital * config.sizing_min_pct
    max_val = capital * config.sizing_max_pct
    return max(min_val, min(max_val, pos_val))


def build_intraday_blocked_by_date(
    db_path: str,
    block_threshold: float = -0.01,
    resume_threshold: float = -0.005,
) -> dict[str, dict[str, bool]]:
    """index_candles 일봉 기반 날짜별 장중 차단 여부 근사.

    근사 방법: 당일 (close - open) / open 으로 등락률 산출.
    히스테리시스는 일 단위로 적용 (일봉이라 분 단위 쿨다운 불필요).

    Returns:
        {"20260410": {"kospi": True, "kosdaq": False}, ...}
        True = 차단(신규 매수 불가), False = 허용
    """
    import sqlite3

    result: dict[str, dict[str, bool]] = {}
    conn = sqlite3.connect(db_path)
    try:
        for index_code, market in (("001", "kospi"), ("101", "kosdaq")):
            cur = conn.execute(
                "SELECT dt, open, close FROM index_candles "
                "WHERE index_code=? ORDER BY dt ASC",
                (index_code,),
            )
            rows = cur.fetchall()
            blocked = False
            for dt, open_p, close in rows:
                if not open_p or open_p <= 0 or not close or close <= 0:
                    result.setdefault(dt, {})[market] = blocked
                    continue
                change = (close - open_p) / open_p
                if not blocked:
                    if change < block_threshold:
                        blocked = True
                else:
                    if change >= resume_threshold:
                        blocked = False
                result.setdefault(dt, {})[market] = blocked
    finally:
        conn.close()
    return result


def build_market_strong_by_date(
    db_path: str,
    ma_length: int = 5,
) -> dict[str, dict[str, bool]]:
    """index_candles에서 코스피/코스닥 MA 기반 날짜별 강세 맵 생성.

    Returns:
        {"20260410": {"kospi": True, "kosdaq": False}, ...}
        MA 계산에 필요한 과거 데이터가 부족한 초반 날짜는 맵에 포함되지 않음.
    """
    import sqlite3

    result: dict[str, dict[str, bool]] = {}
    conn = sqlite3.connect(db_path)
    try:
        for index_code, market in (("001", "kospi"), ("101", "kosdaq")):
            cur = conn.execute(
                "SELECT dt, close FROM index_candles "
                "WHERE index_code=? ORDER BY dt ASC",
                (index_code,),
            )
            rows = cur.fetchall()
            if len(rows) <= ma_length:
                logger.warning(
                    f"index {index_code} 데이터 부족: {len(rows)}건 (MA{ma_length} 요구 > {ma_length}건)"
                )
                continue
            # 각 날짜 i에 대해 직전 ma_length일의 평균으로 MA 계산
            for i in range(ma_length, len(rows)):
                dt, close = rows[i]
                recent = [rows[j][1] for j in range(i - ma_length, i)]
                ma = sum(recent) / ma_length
                strong = close > ma
                result.setdefault(dt, {})[market] = strong
    finally:
        conn.close()
    return result


class Backtester:
    """순수 pandas 기반 단타 전략 백테스터."""

    def __init__(
        self,
        db: DbManager,
        config: TradingConfig,
        commission: float | None = None,
        tax: float | None = None,
        slippage: float | None = None,
        backtest_config: BacktestConfig | None = None,
        ticker_market: str = "unknown",
        market_strong_by_date: dict[str, dict[str, bool]] | None = None,
        atr_db_path: str = "daytrader.db",
        intraday_blocked_by_date: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        self._db = db
        self._config = config
        # 우선순위: 파라미터 > BacktestConfig > 글로벌 상수
        bt = backtest_config or BacktestConfig()
        self._entry_fee = commission if commission is not None else bt.commission
        self._exit_fee = commission if commission is not None else bt.commission
        self._tax = tax if tax is not None else bt.tax
        self._slippage = slippage if slippage is not None else bt.slippage
        # ADR-009: 공유 비용 모델 (PaperOrderManager와 동일 경로)
        self._costs = TradeCosts(
            commission_rate=self._entry_fee,
            slippage_rate=self._slippage,
            tax_rate=self._tax,
        )
        # 시장 필터 (Phase 1 Day 4)
        # market_strong_by_date: {"20260410": {"kospi": True, "kosdaq": False}, ...}
        self._ticker_market = ticker_market
        self._market_strong_by_date = market_strong_by_date or {}
        # ATR 조회용 DB 경로 (Phase 2 Day 7 버그픽스):
        # ProcessPool 워커에서 db=None으로 생성 시 ATR 트레일이 꺼지던 문제 해결
        self._atr_db_path = atr_db_path
        # 당일 상한가 (_setup_strategy_day 에서 prev_close × limit_up_pct로 계산)
        self._current_limit_up: float | None = None
        # 장중 필터: {date_str: {"kospi": blocked, "kosdaq": blocked}}
        # build_intraday_blocked_by_date()로 생성. None이면 장중 필터 비적용.
        self._intraday_blocked_by_date = intraday_blocked_by_date
        # 시그널 스코어링 (signal_scoring_enabled=True 시 활성)
        self._scorer = None
        if getattr(config, "signal_scoring_enabled", False):
            from core.signal_scorer import SignalScorer
            self._scorer = SignalScorer(
                w_volume_ratio=getattr(config, "score_weight_volume_ratio", 25.0),
                w_adx_strength=getattr(config, "score_weight_adx_strength", 25.0),
                w_breakout_pct=getattr(config, "score_weight_breakout_pct", 20.0),
                w_close_position=getattr(config, "score_weight_close_position", 15.0),
                w_atr_normalized=getattr(config, "score_weight_atr_normalized", 15.0),
            )
        self._signal_min_score = getattr(config, "signal_min_score", 60.0)

    # ------------------------------------------------------------------
    # 데이터 로드
    # ------------------------------------------------------------------

    async def load_candles(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """intraday_candles 테이블에서 분봉 데이터 조회.

        Args:
            ticker:     종목 코드 (예: '005930')
            start_date: 시작 날짜/시각 문자열 (예: '2026-01-01' or '2026-01-01T09:00:00')
            end_date:   종료 날짜/시각 문자열

        Returns:
            columns: ts, open, high, low, close, volume, vwap
        """
        rows = await self._db.fetch_all(
            "SELECT ts, open, high, low, close, volume, vwap "
            "FROM intraday_candles "
            "WHERE ticker=? AND tf='1m' AND ts BETWEEN ? AND ? "
            "ORDER BY ts ASC",
            (ticker, start_date, end_date),
        )

        if not rows:
            logger.warning(f"캔들 없음: ticker={ticker} {start_date}~{end_date}")
            return pd.DataFrame(
                columns=["ts", "open", "high", "low", "close", "volume", "vwap"]
            )

        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"])
        # 전략이 사용하는 time 컬럼 추가 (HH:MM 형식)
        df["time"] = df["ts"].dt.strftime("%H:%M")
        logger.info(f"캔들 로드: ticker={ticker} rows={len(df)}")
        return df

    # ------------------------------------------------------------------
    # 백테스트 실행
    # ------------------------------------------------------------------

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: BaseStrategy,
    ) -> dict[str, Any]:
        """캔들 데이터에 전략을 적용해 시뮬레이션하고 KPI를 반환.

        Args:
            candles:  load_candles() 에서 반환된 DataFrame
            strategy: BaseStrategy 구현체

        Returns:
            KPI dict (calculate_kpi 결과) + 'trades' 키 포함
        """
        if candles.empty:
            logger.warning("빈 캔들 데이터 — 백테스트 스킵")
            return {**self.calculate_kpi([]), "trades": []}

        # 0-기반 정수 인덱스 보장 (look-ahead bias 방지)
        candles = candles.reset_index(drop=True)

        trades: list[dict] = []
        position: dict | None = None          # 현재 보유 포지션
        breakout_price_day: float | None = None  # 당일 최초 돌파 시점 가격

        for idx, row in candles.iterrows():
            ts = row["ts"]
            # 백테스트 모드: 시뮬레이션 시각을 전략에 주입
            if hasattr(ts, "time"):
                strategy.set_backtest_time(ts.time())

            tick = {
                "ticker": "BACKTEST",
                "price": float(row["close"]),
                "time": ts.strftime("%H%M") if hasattr(ts, "strftime") else str(ts)[11:16].replace(":", ""),
                "volume": int(row.get("volume", 0)),
            }

            candles_so_far = (
                candles.iloc[: idx + 1]  # type: ignore[misc]
                if isinstance(idx, int)
                else candles.loc[:idx]
            )

            # ── 포지션 없음 → 진입 신호 탐색 ──────────────────────────
            if position is None:
                # 당일 최초 돌파 시점 가격 추적 (고점 진입 방지용 breakout_price)
                if breakout_price_day is None:
                    _pday_high = getattr(strategy, "_prev_day_high", 0.0)
                    _pday_close = getattr(strategy, "_prev_day_close", 0.0)
                    _min_bp = getattr(self._config, "min_breakout_pct", 0.0)
                    _breakout_ref = _pday_high
                    # 갭업 기준가 조정: 당일 시가가 gap_threshold 이상 갭업이면 시가를 기준으로
                    if (
                        getattr(self._config, "gap_breakout_adjust_enabled", False)
                        and _pday_close > 0
                        and not candles_so_far.empty
                    ):
                        _today_open = float(candles_so_far.iloc[0].get("open", 0))
                        _gap_thr = getattr(self._config, "gap_threshold_pct", 0.03)
                        if _today_open > 0:
                            _gap_pct = (_today_open - _pday_close) / _pday_close
                            if _gap_pct >= _gap_thr:
                                _breakout_ref = _today_open
                    if _breakout_ref > 0 and float(row.get("high", 0)) >= _breakout_ref * (1 + _min_bp):
                        breakout_price_day = _breakout_ref * (1 + _min_bp)
                signal: Signal | None = strategy.generate_signal(
                    candles_so_far, tick, breakout_price=breakout_price_day
                )
                if signal is not None and signal.side == "buy" and self._scorer is not None:
                    score = self._scorer.score(signal.context)
                    if score.total < self._signal_min_score:
                        signal = None
                if signal is not None and signal.side == "buy":
                    strategy.on_entry()
                    entry_price_raw = float(row["close"])
                    # ADR-009: 공유 cost_model (슬리피지 + 수수료)
                    entry_price, net_entry = apply_buy_costs(entry_price_raw, self._costs)

                    stop_loss = strategy.get_stop_loss(entry_price)
                    tp1 = strategy.get_take_profit(entry_price)

                    # ADR-010: atr_tp_enabled=false → Pure trailing (TP1 우회)
                    pure_trail = not getattr(self._config, "atr_tp_enabled", True)
                    position = {
                        "entry_ts": row["ts"],
                        "entry_price": entry_price,
                        "net_entry": net_entry,
                        "stop_loss": stop_loss,
                        "tp1_price": None if pure_trail else tp1,
                        "limit_up_price": self._current_limit_up,
                    }
                    if pure_trail:
                        position["tp1_hit"] = True
                        position["remaining_ratio"] = 1.0
                        position["highest_price"] = float(row["high"])
                    logger.debug(
                        f"[BT] 진입 ts={row['ts']} price={entry_price:.1f} "
                        f"sl={stop_loss:.1f} tp1={'off' if pure_trail else f'{tp1:.1f}'}"
                    )

            # ── 포지션 보유 중 → 청산 조건 확인 ──────────────────────
            else:
                low = float(row["low"])
                high = float(row["high"])
                close = float(row["close"])

                # 상한가 즉시 청산 (stop_loss 체크 전, 최우선)
                lu = position.get("limit_up_price")
                if (
                    getattr(self._config, "limit_up_exit_enabled", False)
                    and lu
                    and high >= lu
                ):
                    exit_price = lu
                    remaining = position.get("remaining_ratio", 1.0)
                    exit_price_slipped, net_exit = apply_sell_costs(exit_price, self._costs)
                    pnl = (net_exit - position["net_entry"]) * remaining
                    pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "limit_up_exit",
                    })
                    logger.debug(
                        f"[BT] 상한가 청산 ts={row['ts']} exit={exit_price_slipped:.1f} "
                        f"pnl={pnl:.1f} ({pnl_pct:.2%})"
                    )
                    position = None
                    strategy.on_exit()
                    continue

                # 손절 확인 (캔들 저가 기준)
                if low <= position["stop_loss"]:
                    exit_price = position["stop_loss"]
                    remaining = position.get("remaining_ratio", 1.0)
                    exit_price_slipped, net_exit = apply_sell_costs(exit_price, self._costs)
                    pnl = (net_exit - position["net_entry"]) * remaining
                    pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                    # ADR-017: BE 발동 후 stop 터치면 breakeven_stop 라벨
                    if (
                        position.get("breakeven_active")
                        and position["stop_loss"] >= position["entry_price"]
                    ):
                        exit_reason = "breakeven_stop"
                    else:
                        exit_reason = "stop_loss"
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": exit_reason,
                    })
                    logger.debug(
                        f"[BT] 손절 ts={row['ts']} exit={exit_price_slipped:.1f} "
                        f"pnl={pnl:.1f} ({pnl_pct:.2%}) ratio={remaining:.0%}"
                    )
                    position = None
                    strategy.on_exit()

                # TP1 확인 (캔들 고가 기준) — 분할매도
                elif not position.get("tp1_hit") and position["tp1_price"] and high >= position["tp1_price"]:
                    tp1_price = position["tp1_price"]
                    tp1_slipped, net_tp1 = apply_sell_costs(tp1_price, self._costs)
                    tp1_ratio = self._config.tp1_sell_ratio  # 50%
                    pnl = (net_tp1 - position["net_entry"]) * tp1_ratio
                    pnl_pct = (net_tp1 - position["net_entry"]) / position["net_entry"]
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": tp1_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "tp1_hit",
                    })
                    logger.debug(
                        f"[BT] TP1 분할매도 ts={row['ts']} exit={tp1_slipped:.1f} "
                        f"pnl={pnl:.1f} ratio={tp1_ratio:.0%}"
                    )
                    # 나머지 포지션 유지: 손절선 본전 이동 + tp1_hit 마킹
                    position["tp1_hit"] = True
                    position["remaining_ratio"] = 1.0 - tp1_ratio
                    position["stop_loss"] = position["entry_price"]  # 본전 손절
                    position["highest_price"] = high

                    # 마지막 캔들에서 TP1 히트 시 나머지도 강제 청산
                    if idx == len(candles) - 1:
                        remaining = position["remaining_ratio"]
                        fc_slipped, net_fc = apply_sell_costs(close, self._costs)
                        fc_pnl = (net_fc - position["net_entry"]) * remaining
                        fc_pnl_pct = (net_fc - position["net_entry"]) / position["net_entry"]
                        trades.append({
                            "entry_ts": position["entry_ts"],
                            "exit_ts": row["ts"],
                            "entry_price": position["entry_price"],
                            "exit_price": fc_slipped,
                            "pnl": fc_pnl,
                            "pnl_pct": fc_pnl_pct,
                            "exit_reason": "forced_close",
                        })
                        position = None
                        strategy.on_exit()

                # 트레일링 스톱 / 강제청산 (TP1 히트 후)
                elif position.get("tp1_hit"):
                    # 고점 갱신
                    if high > position.get("highest_price", 0):
                        position["highest_price"] = high
                        # time_decay multiplier — candle ts 기준 (백테스트 결정성)
                        candle_ts = row["ts"]
                        if not isinstance(candle_ts, datetime):
                            candle_ts = pd.to_datetime(candle_ts).to_pydatetime()
                        decay = get_time_decay_multiplier(
                            candle_ts,
                            self._config.time_decay_phases,
                            self._config.time_decay_trailing_enabled,
                        )
                        effective_multiplier = self._config.atr_trail_multiplier * decay
                        floor = self._config.time_decay_min_pct_floor
                        effective_min_pct = max(
                            self._config.atr_trail_min_pct * decay, floor,
                        )
                        # Phase 2 Day 7: ATR 기반 Chandelier 트레일링 (폴백: 고정 trailing_stop_pct)
                        new_stop = None
                        if getattr(self._config, "atr_trail_enabled", False):
                            try:
                                from core.indicators import (
                                    calculate_atr_trailing_stop,
                                    get_latest_atr,
                                )
                                ticker = getattr(self, "_current_ticker", None)
                                as_of = None
                                try:
                                    as_of = pd.to_datetime(row["ts"]).strftime("%Y-%m-%d")
                                except Exception:
                                    pass
                                # db가 None이면 _atr_db_path (default "daytrader.db") 사용
                                db_path = (
                                    self._db.db_path if self._db is not None
                                    else self._atr_db_path
                                )
                                atr_pct = (
                                    get_latest_atr(db_path, ticker, as_of)
                                    if ticker else None
                                )
                                if atr_pct is not None:
                                    new_stop = calculate_atr_trailing_stop(
                                        peak_price=position["highest_price"],
                                        atr_pct=atr_pct,
                                        multiplier=effective_multiplier,
                                        min_pct=effective_min_pct,
                                        max_pct=self._config.atr_trail_max_pct,
                                    )
                            except Exception as e:
                                logger.warning(f"[BT] ATR 트레일 실패 폴백: {e}")
                                new_stop = None
                        if new_stop is None:
                            # time_decay 일관성 위해 effective_min_pct를 lower bound로
                            trailing_pct = max(
                                effective_min_pct,
                                self._config.trailing_stop_pct,
                            )
                            new_stop = position["highest_price"] * (1 - trailing_pct)
                        # 트레일링 스톱은 위로만 움직임 (기존 stop보다 낮아지면 유지)
                        position["stop_loss"] = max(position["stop_loss"], new_stop)

                    # ADR-017: Breakeven Stop (BE3) — peak_return ≥ trigger 시
                    # stop을 entry × (1 + offset)로 상향. trailing과 max 비교로 공존.
                    if (
                        getattr(self._config, "breakeven_enabled", False)
                        and not position.get("breakeven_active", False)
                    ):
                        _entry = position["entry_price"]
                        _peak = position.get("highest_price", _entry)
                        _trig = getattr(self._config, "breakeven_trigger_pct", 0.03)
                        if _entry > 0 and (_peak - _entry) / _entry >= _trig:
                            _off = getattr(self._config, "breakeven_offset_pct", 0.01)
                            _be_stop = _entry * (1.0 + _off)
                            position["stop_loss"] = max(position["stop_loss"], _be_stop)
                            position["breakeven_active"] = True

                    remaining = position.get("remaining_ratio", 0.5)
                    # 트레일링 스톱 체크
                    if low <= position["stop_loss"]:
                        exit_price = position["stop_loss"]
                        exit_price_slipped, net_exit = apply_sell_costs(exit_price, self._costs)
                        pnl = (net_exit - position["net_entry"]) * remaining
                        pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                        exit_reason = (
                            "breakeven_stop"
                            if position.get("breakeven_active")
                            and position["stop_loss"] >= position["entry_price"]
                            and position["stop_loss"] <= position["entry_price"] * 1.02
                            else "trailing_stop"
                        )
                        trades.append({
                            "entry_ts": position["entry_ts"],
                            "exit_ts": row["ts"],
                            "entry_price": position["entry_price"],
                            "exit_price": exit_price_slipped,
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "exit_reason": exit_reason,
                        })
                        logger.debug(
                            f"[BT] 트레일링 청산 ts={row['ts']} exit={exit_price_slipped:.1f} "
                            f"pnl={pnl:.1f} ratio={remaining:.0%}"
                        )
                        position = None
                        strategy.on_exit()
                    # 모멘텀 둔화 청산 (수익 포지션 + 보유 15분+ + ROC ≤ -0.5%)
                    elif compute_momentum_fade(
                        entry_price=position["entry_price"],
                        current_price=close,
                        entry_time=(
                            position["entry_ts"]
                            if isinstance(position["entry_ts"], datetime)
                            else pd.to_datetime(position["entry_ts"]).to_pydatetime()
                        ),
                        candle_closes=candles_so_far["close"].tolist()[
                            -(self._config.momentum_fade_lookback + 1):
                        ],
                        now=(
                            row["ts"]
                            if isinstance(row["ts"], datetime)
                            else pd.to_datetime(row["ts"]).to_pydatetime()
                        ),
                        lookback=self._config.momentum_fade_lookback,
                        threshold=self._config.momentum_fade_threshold,
                        min_hold_min=self._config.momentum_fade_min_hold_min,
                        min_profit=self._config.momentum_fade_min_profit,
                        enabled=self._config.momentum_fade_exit_enabled,
                    ):
                        exit_price_slipped, net_exit = apply_sell_costs(close, self._costs)
                        pnl = (net_exit - position["net_entry"]) * remaining
                        pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                        trades.append({
                            "entry_ts": position["entry_ts"],
                            "exit_ts": row["ts"],
                            "entry_price": position["entry_price"],
                            "exit_price": exit_price_slipped,
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "exit_reason": "momentum_fade",
                        })
                        logger.debug(
                            f"[BT] momentum_fade ts={row['ts']} exit={exit_price_slipped:.1f} "
                            f"pnl={pnl:.1f} ({pnl_pct:.2%})"
                        )
                        position = None
                        strategy.on_exit()
                    # 횡보 포지션 조기 청산
                    elif getattr(self._config, "stale_position_exit_enabled", False):
                        _check_min = getattr(self._config, "stale_position_check_minutes", 30)
                        _min_profit = getattr(self._config, "stale_position_min_profit", 0.005)
                        _entry_ts = position["entry_ts"]
                        _now_ts = row["ts"]
                        _entry_dt = (
                            _entry_ts if isinstance(_entry_ts, datetime)
                            else pd.to_datetime(_entry_ts).to_pydatetime()
                        )
                        _now_dt = (
                            _now_ts if isinstance(_now_ts, datetime)
                            else pd.to_datetime(_now_ts).to_pydatetime()
                        )
                        _hold_min = (_now_dt - _entry_dt).total_seconds() / 60
                        _pnl_pct_cur = (close - position["entry_price"]) / position["entry_price"]
                        if _hold_min >= _check_min and _pnl_pct_cur < _min_profit:
                            exit_price_slipped, net_exit = apply_sell_costs(close, self._costs)
                            pnl = (net_exit - position["net_entry"]) * remaining
                            pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                            trades.append({
                                "entry_ts": position["entry_ts"],
                                "exit_ts": row["ts"],
                                "entry_price": position["entry_price"],
                                "exit_price": exit_price_slipped,
                                "pnl": pnl,
                                "pnl_pct": pnl_pct,
                                "exit_reason": "stale_exit",
                            })
                            logger.debug(
                                f"[BT] stale_exit ts={row['ts']} hold={_hold_min:.0f}min "
                                f"pnl={pnl:.1f} ({pnl_pct:.2%})"
                            )
                            position = None
                            strategy.on_exit()
                        elif idx == len(candles) - 1:
                            exit_price_slipped, net_exit = apply_sell_costs(close, self._costs)
                            pnl = (net_exit - position["net_entry"]) * remaining
                            pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                            trades.append({
                                "entry_ts": position["entry_ts"],
                                "exit_ts": row["ts"],
                                "entry_price": position["entry_price"],
                                "exit_price": exit_price_slipped,
                                "pnl": pnl,
                                "pnl_pct": pnl_pct,
                                "exit_reason": "forced_close",
                            })
                            position = None
                            strategy.on_exit()
                    # 마지막 캔들 강제 청산 (나머지)
                    elif idx == len(candles) - 1:
                        exit_price_slipped, net_exit = apply_sell_costs(close, self._costs)
                        pnl = (net_exit - position["net_entry"]) * remaining
                        pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                        trades.append({
                            "entry_ts": position["entry_ts"],
                            "exit_ts": row["ts"],
                            "entry_price": position["entry_price"],
                            "exit_price": exit_price_slipped,
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "exit_reason": "forced_close",
                        })
                        position = None
                        strategy.on_exit()

                # 마지막 캔들 강제 청산 (TP1 미히트 상태)
                elif idx == len(candles) - 1:
                    exit_price_slipped, net_exit = apply_sell_costs(close, self._costs)
                    pnl = net_exit - position["net_entry"]
                    pnl_pct = pnl / position["net_entry"]
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "forced_close",
                    })
                    position = None
                    strategy.on_exit()

        # 백테스트 시각 초기화
        strategy.set_backtest_time(None)

        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        logger.info(
            f"백테스트 완료: total_trades={kpi['total_trades']} "
            f"win_rate={kpi['win_rate']:.1%} total_pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    # ------------------------------------------------------------------
    # KPI 계산
    # ------------------------------------------------------------------

    def calculate_kpi(self, trades: list[dict]) -> dict[str, Any]:
        """거래 목록으로 KPI를 계산한다.

        Args:
            trades: run_backtest() 가 축적한 trade dict 리스트.
                    각 dict 에는 최소 'pnl', 'pnl_pct' 키가 필요하다.

        Returns:
            total_trades, wins, win_rate, profit_factor,
            total_pnl, max_drawdown, sharpe_ratio
        """
        total_trades = len(trades)

        if total_trades == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        pnl_series = [t["pnl"] for t in trades]
        wins = sum(1 for p in pnl_series if p > 0)
        win_rate = wins / total_trades

        gross_profit = sum(p for p in pnl_series if p > 0)
        gross_loss = abs(sum(p for p in pnl_series if p < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        total_pnl = sum(pnl_series)

        # 최대 낙폭 (peak-to-trough)
        max_drawdown = self._calc_max_drawdown(pnl_series)

        # 샤프 비율 (연간화, 거래 단위)
        sharpe_ratio = self._calc_sharpe(pnl_series)

        return {
            "total_trades": total_trades,
            "wins": wins,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe_ratio,
        }

    # ------------------------------------------------------------------
    # 다일 백테스트
    # ------------------------------------------------------------------

    async def run_multi_day(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        strategy: BaseStrategy,
    ) -> dict[str, Any]:
        """여러 날에 걸쳐 하루씩 전략을 리셋하며 백테스트.

        Args:
            ticker:     종목코드
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date:   종료 날짜 (YYYY-MM-DD)
            strategy:   BaseStrategy 구현체 (매일 reset())

        Returns:
            통합 KPI + trades
        """
        self._current_ticker = ticker
        all_candles = await self.load_candles(ticker, start_date, f"{end_date} 23:59:59")
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        # 날짜별 그루핑
        all_candles["date"] = all_candles["ts"].dt.date
        all_trades: list[dict] = []
        prev_day_df: pd.DataFrame | None = None

        for date, day_candles in all_candles.groupby("date"):
            day_df = day_candles.drop(columns=["date"]).reset_index(drop=True)
            strategy.reset()

            # 전략별 전일 데이터 자동 설정
            self._setup_strategy_day(strategy, day_df, prev_day_df)

            result = self.run_backtest(day_df, strategy)
            day_trades = result.get("trades", [])
            all_trades.extend(day_trades)
            logger.info(
                f"[{date}] trades={len(day_trades)} "
                f"pnl={sum(t['pnl'] for t in day_trades):.0f}"
            )
            prev_day_df = day_df

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        logger.info(
            f"다일 백테스트 완료: days={len(all_candles['date'].unique())} "
            f"total_trades={kpi['total_trades']} "
            f"win_rate={kpi['win_rate']:.1%} total_pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: BaseStrategy,
    ) -> dict[str, Any]:
        """이미 로드된 캔들 DataFrame으로 다일 백테스트 (DB 재로드 없음).

        시장 필터가 활성화된 경우 ticker_market + market_strong_by_date 기반으로
        약세 시장 날짜는 매매를 건너뛴다 (prev_day_df는 여전히 갱신).
        """
        self._current_ticker = ticker
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        df = all_candles.copy()
        if "date" not in df.columns:
            df["date"] = df["ts"].dt.date
        all_trades: list[dict] = []
        prev_day_df: pd.DataFrame | None = None

        # 변동성 기반 사이징 준비
        _vol_sizing = getattr(self._config, "volatility_sizing_enabled", False)
        _ticker_atr_pct: float | None = None
        if _vol_sizing:
            _ticker_atr_pct = compute_atr_pct_from_candles(all_candles)
            if _ticker_atr_pct is None or _ticker_atr_pct <= 0:
                _vol_sizing = False  # ATR 미가용 → fallback
        _initial_capital = float(getattr(self._config, "initial_capital", 1_000_000))

        market_filter_enabled = getattr(self._config, "market_filter_enabled", False)
        blacklist_enabled = getattr(self._config, "blacklist_enabled", False)
        bl_lookback = getattr(self._config, "blacklist_lookback_days", 5)
        bl_threshold = getattr(self._config, "blacklist_loss_threshold", 3)
        # Phase 3 Day 11.5: 연속 손실 휴식 (backtester 자체 추적, DB 미사용)
        rest_enabled = getattr(self._config, "consecutive_loss_rest_enabled", False)
        rest_threshold = getattr(self._config, "consecutive_loss_threshold", 3)

        # 일별 PnL → 연속 손실 카운터
        daily_pnl_by_date: dict = {}

        for date, day_candles in df.groupby("date"):
            day_df = day_candles.drop(columns=["date"]).reset_index(drop=True)

            # 시장 필터: 약세 시장 종목은 해당 날짜 매매 건너뛰기 (또는 사이즈 축소)
            skip_day = False
            size_factor = 1.0
            if market_filter_enabled and self._ticker_market in ("kospi", "kosdaq"):
                date_key = date.strftime("%Y%m%d")
                strong = self._market_strong_by_date.get(date_key)
                # strong이 없으면(데이터 누락) 보수적으로 허용
                if strong is not None and not strong.get(self._ticker_market, True):
                    if getattr(self._config, "market_regime_reduce_enabled", False):
                        size_factor = getattr(self._config, "market_regime_reduce_size", 0.5)
                    else:
                        skip_day = True

            # 장중 필터: 일봉 close/open 기반 근사 (intraday_blocked_by_date 있을 때만)
            if (
                not skip_day
                and self._intraday_blocked_by_date is not None
                and getattr(self._config, "intraday_market_filter_enabled", False)
                and self._ticker_market in ("kospi", "kosdaq")
            ):
                date_key = date.strftime("%Y%m%d")
                intraday_map = self._intraday_blocked_by_date.get(date_key, {})
                if intraday_map.get(self._ticker_market, False):
                    skip_day = True

            # Phase 2 Day 10: 블랙리스트 — 최근 lookback일 내 손실 ≥ threshold면 당일 skip
            if not skip_day and blacklist_enabled:
                from datetime import timedelta as _td
                cutoff = date - _td(days=bl_lookback)
                recent_losses = 0
                for t in all_trades:
                    try:
                        exit_ts = t.get("exit_ts")
                        if exit_ts is None:
                            continue
                        exit_date = (
                            exit_ts.date() if hasattr(exit_ts, "date")
                            else pd.to_datetime(exit_ts).date()
                        )
                    except Exception:
                        continue
                    if cutoff <= exit_date < date and t.get("pnl", 0.0) < 0:
                        recent_losses += 1
                if recent_losses >= bl_threshold:
                    skip_day = True

            # Phase 3 Day 11.5: 이전 N일 연속 손실 시 당일 휴식
            if not skip_day and rest_enabled:
                past_dates = sorted(
                    (d for d in daily_pnl_by_date.keys() if d < date),
                    reverse=True,
                )
                consecutive = 0
                for d in past_dates:
                    if daily_pnl_by_date[d] < 0:
                        consecutive += 1
                    else:
                        break
                if consecutive >= rest_threshold:
                    skip_day = True

            if skip_day:
                prev_day_df = day_df
                continue

            strategy.reset()
            self._setup_strategy_day(strategy, day_df, prev_day_df)
            result = self.run_backtest(day_df, strategy)
            day_trades = result.get("trades", [])
            if size_factor != 1.0:
                for t in day_trades:
                    t["pnl"] = t.get("pnl", 0.0) * size_factor
                    t["size_factor"] = size_factor
            # 변동성 사이징: pnl_pct 기반으로 ₩ PnL 재산정
            if _vol_sizing and _ticker_atr_pct:
                pos_val = calc_sizing_position_value(
                    self._config, _ticker_atr_pct, _initial_capital
                )
                for t in day_trades:
                    t["pnl"] = t.get("pnl_pct", 0.0) * pos_val
                    t["position_value"] = pos_val
                    t["atr_pct"] = _ticker_atr_pct
            all_trades.extend(day_trades)
            # Phase 3 Day 11.5: 당일 PnL 집계 (연속 손실 휴식용)
            daily_pnl_by_date[date] = sum(t.get("pnl", 0.0) for t in day_trades)
            prev_day_df = day_df

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        return kpi

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _setup_strategy_day(
        self,
        strategy: BaseStrategy,
        day_df: pd.DataFrame,
        prev_day_df: pd.DataFrame | None,
    ) -> None:
        """전략별 당일/전일 데이터를 자동 설정한다."""
        # Phase 2 Day 6: ATR 조회용 ticker 주입 (run_multi_day_cached가 알고 있음)
        if hasattr(strategy, "set_ticker") and hasattr(self, "_current_ticker"):
            strategy.set_ticker(self._current_ticker)
        # Momentum: 전일 고가/거래량/종가 설정
        if hasattr(strategy, "set_prev_day_data") and prev_day_df is not None:
            prev_high = float(prev_day_df["high"].max())
            prev_volume = int(prev_day_df["volume"].sum())
            prev_close = float(prev_day_df.iloc[-1]["close"])
            strategy.set_prev_day_data(prev_high, prev_volume, prev_close)

        # 시간대별 거래량 비율용 전일 분봉 주입
        if hasattr(strategy, "set_prev_day_candles") and prev_day_df is not None:
            strategy.set_prev_day_candles(prev_day_df)

        # 당일 상한가 계산 (전일 종가 × 1.30, 호가 절사)
        self._current_limit_up = None
        if (
            getattr(self._config, "limit_up_exit_enabled", False)
            and prev_day_df is not None
            and not prev_day_df.empty
        ):
            try:
                from core.price_utils import calculate_limit_up_price
                prev_close = float(prev_day_df.iloc[-1]["close"])
                lu_pct = getattr(self._config, "limit_up_pct", 0.30)
                lu = calculate_limit_up_price(prev_close, lu_pct)
                if lu > 0:
                    self._current_limit_up = float(lu)
            except Exception:
                self._current_limit_up = None

        # ORB: 전일 거래량 설정
        if hasattr(strategy, "set_prev_day_volume") and prev_day_df is not None:
            prev_volume = int(prev_day_df["volume"].sum())
            strategy.set_prev_day_volume(prev_volume)

        # Pullback: 당일 시가 설정
        if hasattr(strategy, "set_open_price") and not day_df.empty:
            open_price = float(day_df.iloc[0]["open"])
            strategy.set_open_price(open_price)

        # GapStrategy: 전일 종가 설정
        if hasattr(strategy, "set_prev_close") and prev_day_df is not None:
            strategy.set_prev_close(float(prev_day_df.iloc[-1]["close"]))

    @staticmethod
    def _calc_max_drawdown(pnl_series: list[float]) -> float:
        """누적 PnL 곡선에서 peak-to-trough 최대 낙폭 계산."""
        if not pnl_series:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnl_series:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    @staticmethod
    def _calc_sharpe(pnl_series: list[float], ann_factor: float = 252.0) -> float:
        """거래별 PnL 기준 샤프 비율 (연간화).

        Args:
            pnl_series:  거래별 손익 리스트
            ann_factor:  연간화 계수 (기본 252 거래일)
        """
        n = len(pnl_series)
        if n < 2:
            return 0.0

        mean_r = sum(pnl_series) / n
        variance = sum((r - mean_r) ** 2 for r in pnl_series) / (n - 1)
        std_r = math.sqrt(variance)

        if std_r == 0:
            return 0.0

        return (mean_r / std_r) * math.sqrt(ann_factor)
