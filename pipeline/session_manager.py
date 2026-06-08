"""pipeline/session_manager.py — 일일 세션 관리.

강제 청산 / 일일 보고서 / 일일 리셋 / OHLCV 갱신 / 토큰 갱신 /
분봉 수집 / 유니버스 갱신 / 시장 필터 갱신 로직을 engine_worker에서 분리.
PyQt6 미사용 — market_status 변경은 on_market_status 콜백으로 전달.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import Callable

from loguru import logger

from pipeline.trading_state import TradingState


class SessionManager:
    """일일 세션 생명주기 전담."""

    def __init__(
        self,
        risk_manager,
        order_manager,
        order_tracker,
        shadow_tracker,
        candle_builder,
        market_filter,
        config,
        notifier,
        db,
        rest_client,
        ws_client,
        token_manager,
        state: TradingState,
        paper_mode: bool,
        on_trade_executed: Callable[[dict], None],
        on_market_status: Callable[[bool, bool], None] | None = None,
    ):
        self._risk_manager = risk_manager
        self._order_manager = order_manager
        self._order_tracker = order_tracker
        self._shadow_tracker = shadow_tracker
        self._candle_builder = candle_builder
        self._market_filter = market_filter
        self._config = config
        self._notifier = notifier
        self._db = db
        self._rest_client = rest_client
        self._ws_client = ws_client
        self._token_manager = token_manager
        self._state = state
        self._paper_mode = paper_mode
        self._on_trade_executed = on_trade_executed
        self._on_market_status = on_market_status
        self._process_start = datetime.now()

    # ── 강제 청산 ──

    async def force_close(self) -> None:
        if self._state.force_close_in_progress:
            logger.warning("강제 청산 이미 실행 중 — 중복 호출 무시")
            return
        self._state.force_close_in_progress = True
        try:
            logger.warning("15:10 강제 청산 시작")
            for ticker, pos in list(self._risk_manager.get_open_positions().items()):
                if pos.remaining_qty > 0:
                    close_price = int(
                        self._state.latest_prices.get(ticker, pos.entry_price)
                    )
                    qty = pos.remaining_qty
                    entry = pos.entry_price
                    pnl = (close_price - entry) * qty if entry > 0 else 0
                    pnl_pct = ((close_price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.strategy or "unknown"
                    prefer_best = self._vi_handler.should_use_best_limit(ticker) if hasattr(self, "_vi_handler") else False
                    result = await self._order_manager.execute_sell_force_close(
                        ticker=ticker, qty=qty, price=close_price,
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="forced_close",
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: (
                            self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})")
                            if hasattr(self, "_vi_handler") else None
                        ),
                    )
                    if result is None:
                        logger.error(f"[ORDER-TRACK] force_close 주문 실패: {ticker}")
                        continue
                    if self._paper_mode:
                        _entry_time_fc = pos.entry_time
                        self._risk_manager.settle_sell(ticker, float(close_price), qty)
                        strat_info = self._state.active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
                        logger.bind(
                            event="exit", ticker=ticker, reason="forced_close",
                            price=int(close_price), qty=qty, pnl=int(pnl),
                            pnl_pct=round(pnl_pct, 2),
                            hold_minutes=round(
                                (datetime.now() - _entry_time_fc).total_seconds() / 60, 1
                            ) if _entry_time_fc else None,
                        ).info(f"forced_close 실행: {ticker} {qty}주 @ {close_price:,} PnL={pnl:+,.0f}")
                    else:
                        self._order_tracker.submit(result["order_no"], ticker, "sell", qty)
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                            f"{ticker} sell {qty} (forced_close)"
                        )
                        strat_info = self._state.active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
            await self._candle_builder.flush()
            self._candle_builder.reset()
            await self._risk_manager.save_daily_summary()
            self._risk_manager.reset_daily()
            self._state.daily_halt_notified = False
            self._state.active_strategy = None
            self._state.active_strategies = {}
            self._state.candle_history.clear()
            if self._shadow_tracker is not None:
                self._shadow_tracker.close_all()
        finally:
            self._state.force_close_in_progress = False

    def set_vi_handler(self, vi_handler) -> None:
        self._vi_handler = vi_handler

    # ── 일일 보고서 ──

    async def daily_report(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info("15:30 일일 보고서 생성 시작")
        try:
            summary = await self._db.fetch_one(
                "SELECT * FROM daily_pnl WHERE date = ?", (today,),
            )
        except Exception as e:
            logger.warning(f"daily_pnl 조회 실패: {e}")
            summary = None
        if summary is None:
            summary = await self._risk_manager.save_daily_summary()
        if not self._config.notifications.daily_report:
            logger.info("일일 보고서 — 알림 비활성")
        elif summary:
            self._notifier.send_daily_report(
                date=summary["date"], total_trades=summary["total_trades"],
                wins=summary["wins"],
                losses=summary.get("losses", summary["total_trades"] - summary["wins"]),
                total_pnl=int(summary["total_pnl"]), win_rate=summary["win_rate"],
                strategy=summary["strategy"], max_drawdown=summary.get("max_drawdown", 0),
            )
            logger.bind(
                event="daily_summary", date=summary["date"],
                total_trades=summary["total_trades"], wins=summary["wins"],
                losses=summary.get("losses", summary["total_trades"] - summary["wins"]),
                total_pnl=int(summary["total_pnl"]),
                win_rate=round(float(summary["win_rate"]), 4),
                max_drawdown=round(float(summary.get("max_drawdown", 0)), 4),
            ).info("일일 보고서 발송 완료")
        else:
            self._notifier.send_no_trade("당일 매매 기록 없음")
            logger.info("당일 매매 없음 -- 무거래 알림 발송")

        if self._shadow_tracker is not None:
            shadow = self._shadow_tracker.get_summary()
            logger.bind(
                event="shadow_summary", total=shadow["total"],
                profit_count=shadow["profit_count"], loss_count=shadow["loss_count"],
                avg_profit_pct=round(shadow["avg_profit_pct"] * 100, 2),
                avg_loss_pct=round(shadow["avg_loss_pct"] * 100, 2),
                positions=shadow["positions"],
            ).info("섀도우 트래킹 요약")
            if self._notifier and shadow["total"] > 0:
                try:
                    self._notifier.send(self._shadow_tracker.format_report(), parse_mode="")
                except Exception as _e:
                    logger.warning(f"섀도우 트래커 알림 실패: {_e}")

    # ── 일일 리셋 ──

    async def daily_reset(self, register_fn: Callable[[list[dict]], None]) -> None:
        logger.info("[자동] 일일 리셋 시작")
        self._risk_manager.reset_daily_counters()
        self._state.daily_halt_notified = False
        if self._candle_builder is not None:
            self._candle_builder.reset()
        self._state.candle_history.clear()
        self._state.breakout_detected.clear()
        self._state.tick_signaled.clear()
        self._state.intraday_added_tickers.clear()
        self._state.intraday_add_count = 0
        self._state.ticker_sources.clear()
        if self._shadow_tracker is not None:
            self._shadow_tracker.reset()

        stocks = self._load_universe_simple()
        if not self._state.active_strategies:
            register_fn(stocks)
        else:
            for strat_info in self._state.active_strategies.values():
                strat_info["strategy"].reset()
            for gap_strat in self._state.gap_strategies.values():
                gap_strat.reset()
        await self.refresh_ohlcv(stocks)
        logger.info("[자동] 일일 리셋 완료")
        if self._notifier and self._config.notifications.daily_reset:
            try:
                self._notifier.send(
                    f"[자동] 일일 리셋 완료 — {len(self._state.active_strategies)}종목, 카운터 초기화"
                )
            except Exception as e:
                logger.warning(f"일일 리셋 알림 실패: {e}")

    def _load_universe_simple(self) -> list[dict]:
        import yaml
        from pathlib import Path
        uni_path = Path("config/universe.yaml")
        if not uni_path.exists():
            return []
        uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
        return uni.get("stocks", [])

    # ── 토큰 갱신 ──

    async def refresh_token(self) -> None:
        try:
            token = await self._token_manager.get_token()
            logger.info(f"토큰 사전 갱신 완료: {token[:10]}...")
        except Exception as e:
            logger.error(f"토큰 갱신 실패: {e}")
            if self._notifier and self._config.notifications.token_refresh_failure:
                self._notifier.send_urgent(f"토큰 갱신 실패: {e}")

    # ── OHLCV 갱신 ──

    async def refresh_ohlcv(self, stocks: list[dict] | None = None) -> None:
        """전일 OHLCV 각 strategy에 주입. startup + 08:05 cron + daily_reset 공용."""
        if stocks is None:
            stocks = self._load_universe_simple()
        if not stocks:
            return
        import time as _time_mod
        _t_start = _time_mod.monotonic()
        logger.info(f"전일 OHLCV 갱신 시작 — {len(stocks)}종목")

        semaphore = asyncio.Semaphore(5)
        _dbg_count = [0]

        async def _fetch_one(s: dict) -> tuple[int, int, int]:
            ticker = s["ticker"]
            _init = _lu_api = _lu_fb = 0
            try:
                cached = self._state.daily_ohlcv_cache.pop(ticker, None)
                if cached is not None:
                    items = cached
                else:
                    async with semaphore:
                        daily = await self._rest_client.get_daily_ohlcv(
                            ticker, base_dt=datetime.now().strftime("%Y%m%d"),
                        )
                    items = (
                        daily.get("stk_dt_pole_chart_qry")
                        or daily.get("output2")
                        or daily.get("output")
                        or []
                    )
                if items and len(items) >= 2:
                    prev = items[1]
                    prev_high = abs(float(prev.get("high_pric", 0)))
                    prev_vol = abs(int(
                        prev.get("trde_qty", prev.get("acml_vol", prev.get("acml_vlmn", 0)))
                    ))
                    if _dbg_count[0] < 3:
                        _dbg_count[0] += 1
                        logger.debug(
                            f"[OHLCV-DBG] {ticker} prev_high={prev_high} prev_vol={prev_vol} "
                            f"raw_keys={list(prev.keys())[:10]}"
                        )
                    prev_close = abs(float(prev.get("cur_prc", prev.get("stck_clpr", 0))))
                    if prev_high > 0 and ticker in self._state.active_strategies:
                        strat = self._state.active_strategies[ticker]["strategy"]
                        if hasattr(strat, "set_prev_day_data"):
                            strat.set_prev_day_data(prev_high, prev_vol, prev_close)
                            _init += 1
                        self._state.prev_high_map[ticker] = prev_high
                    # 갭 전략 전일 데이터 주입
                    gap_strat = self._state.gap_strategies.get(ticker)
                    if gap_strat is not None and prev_close > 0:
                        gap_strat.set_prev_day_data(0.0, prev_vol, prev_close)
                    if prev_close > 0:
                        self._state.prev_close[ticker] = prev_close
                        lu_val: float | None = None
                        try:
                            async with semaphore:
                                api_lu = await self._rest_client.get_limit_up_price(ticker)
                            if api_lu and api_lu > 0:
                                lu_val = float(api_lu)
                                _lu_api += 1
                        except Exception as e:
                            logger.debug(f"상한가 API 실패 ({ticker}): {e}")
                        if lu_val is None:
                            try:
                                from core.price_utils import calculate_limit_up_price
                                lu_pct = getattr(self._config.trading, "limit_up_pct", 0.30)
                                calc = calculate_limit_up_price(prev_close, lu_pct)
                                if calc > 0:
                                    lu_val = float(calc)
                                    _lu_fb += 1
                            except Exception as e:
                                logger.debug(f"상한가 계산 실패 ({ticker}): {e}")
                        if lu_val is not None:
                            self._state.limit_up_map[ticker] = lu_val
            except Exception as e:
                logger.debug(f"전일 OHLCV 실패 ({ticker}): {e}")
            return _init, _lu_api, _lu_fb

        results = await asyncio.gather(*[_fetch_one(s) for s in stocks])
        init_count = sum(r[0] for r in results)
        lu_api_count = sum(r[1] for r in results)
        lu_fallback_count = sum(r[2] for r in results)
        _elapsed = _time_mod.monotonic() - _t_start
        logger.info(
            f"전일 OHLCV 갱신 완료: {init_count}/{len(stocks)} — {_elapsed:.1f}s "
            f"(상한가 {len(self._state.limit_up_map)}종 — "
            f"API {lu_api_count} / fallback {lu_fallback_count})"
        )
        if self._state.daily_ohlcv_cache:
            self._state.daily_ohlcv_cache.clear()
        try:
            await self._seed_intraday_candles(stocks)
        except Exception as e:
            logger.warning(f"분봉 시드 실패 — 장 초반 ADX 미작동 가능: {e}")

    async def _seed_intraday_candles(self, stocks: list[dict]) -> None:
        if not stocks:
            return
        n = self._state.INTRADAY_SEED_BARS
        seeded = 0
        for s in stocks:
            ticker = s["ticker"]
            try:
                data = await self._rest_client.get_minute_ohlcv(ticker, tic_scope=1)
                items = data.get("stk_min_pole_chart_qry") or data.get("output2") or []
                if not items:
                    continue
                seed: list[dict] = []
                for item in reversed(items):
                    raw_ts = str(item.get("cntr_tm", ""))
                    if len(raw_ts) < 14:
                        continue
                    ts = (
                        f"{raw_ts[:4]}-{raw_ts[4:6]}-{raw_ts[6:8]}T"
                        f"{raw_ts[8:10]}:{raw_ts[10:12]}:{raw_ts[12:14]}"
                    )
                    try:
                        seed.append({
                            "ticker": ticker, "tf": "1m", "ts": ts,
                            "open": abs(float(item.get("open_pric") or 0)),
                            "high": abs(float(item.get("high_pric") or 0)),
                            "low": abs(float(item.get("low_pric") or 0)),
                            "close": abs(float(item.get("cur_prc") or 0)),
                            "volume": int(item.get("trde_qty") or 0),
                            "vwap": None,
                        })
                    except (ValueError, TypeError):
                        continue
                if not seed:
                    continue
                self._state.candle_history[ticker] = deque(
                    seed[-n:], maxlen=self._state.MAX_HISTORY
                )
                seeded += 1
            except Exception as e:
                logger.debug(f"분봉 시드 ({ticker}) 실패: {e}")
        logger.info(f"분봉 시드 완료: {seeded}/{len(stocks)}종 — N={n}봉")

    # ── 지수 캔들 갱신 ──

    async def refresh_index_candles(self) -> None:
        import sqlite3 as _sqlite3
        db_path = self._config.db_path
        for code in ("001", "101"):
            try:
                data = await self._rest_client.get_index_daily(code)
                items = data.get("inds_dt_pole_qry") or []
                if not items:
                    logger.warning(f"[INDEX] {code} 응답 없음")
                    continue
                conn = _sqlite3.connect(db_path)
                try:
                    for c in items:
                        conn.execute(
                            "INSERT OR REPLACE INTO index_candles "
                            "(index_code, dt, open, high, low, close, volume) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                code, c["dt"],
                                float(c["open_pric"]) / 100, float(c["high_pric"]) / 100,
                                float(c["low_pric"]) / 100, float(c["cur_prc"]) / 100,
                                int(c["trde_qty"]),
                            ),
                        )
                    conn.commit()
                finally:
                    conn.close()
                logger.info(f"[INDEX] {code} 갱신 완료: {len(items)}건")
            except Exception as exc:
                logger.error(f"[INDEX] {code} 갱신 실패: {exc}")

    # ── 분봉 수집 ──

    async def collect_daily_candles(self) -> None:
        from backtest.data_collector import DataCollector
        logger.info("[CANDLE] 일일 분봉 수집 시작")
        stocks = self._load_universe_simple()
        if not stocks:
            logger.warning("[CANDLE] 유니버스 비어 있음")
            return
        collector = DataCollector(self._rest_client, self._db)
        # 오늘 날짜를 명시해야 장 마감 직후 API가 당일 분봉을 반환한다
        today_str = datetime.now().strftime("%Y%m%d")
        success = failed = total_saved = 0
        for s in stocks:
            ticker = s["ticker"]
            try:
                saved = await collector.collect_minute_candles(
                    ticker, days=1, start_dt=today_str
                )
                total_saved += saved
                success += 1
            except Exception as e:
                logger.warning(f"[CANDLE] {ticker} 수집 실패: {e}")
                failed += 1
        logger.info(
            f"[CANDLE] 수집 완료: {success}/{len(stocks)}종목, "
            f"{total_saved:,}개 캔들, 실패 {failed}"
        )
        if self._notifier and self._config.notifications.candle_collection:
            try:
                self._notifier.send(
                    f"[CANDLE] 분봉 수집 완료\n성공: {success}/{len(stocks)}종목\n"
                    f"캔들: {total_saved:,}개\n실패: {failed}종목"
                )
            except Exception:
                pass

    # ── 시장 필터 갱신 ──

    async def refresh_market_filter(self) -> tuple[bool, bool] | None:
        """시장 필터 갱신. 성공 시 (kospi_strong, kosdaq_strong) 반환."""
        if self._market_filter is None:
            return None
        try:
            await self._market_filter.refresh()
            k = self._market_filter.kospi_strong
            q = self._market_filter.kosdaq_strong
            if self._notifier:
                try:
                    hhmm = datetime.now().strftime("%H:%M")
                    self._notifier.send(
                        f"[MARKET] {hhmm} 재갱신 — 코스피 {'강세' if k else '약세'} / "
                        f"코스닥 {'강세' if q else '약세'}"
                    )
                except Exception:
                    pass
            logger.bind(
                event="market_filter", kospi_strong=k, kosdaq_strong=q,
            ).info(
                f"[MARKET] 필터 갱신: KOSPI={'강세' if k else '약세'} "
                f"KOSDAQ={'강세' if q else '약세'}"
            )
            if self._on_market_status:
                self._on_market_status(k, q)
            return k, q
        except Exception as e:
            logger.error(f"[SCHED] 시장 필터 재갱신 실패: {e}")
            return None

    async def refresh_intraday_filter(self) -> None:
        """10분 간격 장중 필터 갱신."""
        if self._market_filter is None:
            return
        if not getattr(self._config.trading, "intraday_market_filter_enabled", False):
            return
        from datetime import time as dt_time
        now_t = datetime.now().time()
        if not (dt_time(9, 5) <= now_t <= dt_time(15, 0)):
            return
        try:
            prev_kospi = self._market_filter.is_intraday_blocked("kospi")
            prev_kosdaq = self._market_filter.is_intraday_blocked("kosdaq")
            block_thr = (
                self._market_filter._block_threshold_override
                if self._market_filter._block_threshold_override is not None
                else self._config.trading.intraday_block_threshold
            )
            resume_thr = (
                self._market_filter._resume_threshold_override
                if self._market_filter._resume_threshold_override is not None
                else self._config.trading.intraday_resume_threshold
            )
            await self._market_filter.refresh_intraday(
                block_threshold=block_thr,
                resume_threshold=resume_thr,
                cooldown_minutes=20,
            )
            now_kospi = self._market_filter.is_intraday_blocked("kospi")
            now_kosdaq = self._market_filter.is_intraday_blocked("kosdaq")
            change = self._market_filter.intraday_change
            logger.bind(
                event="intraday_market_filter",
                kospi_blocked=now_kospi, kosdaq_blocked=now_kosdaq,
                kospi_change_pct=change.get("001"), kosdaq_change_pct=change.get("101"),
            ).info(
                f"[INTRADAY] 필터 갱신: KOSPI={'차단' if now_kospi else '허용'} "
                f"KOSDAQ={'차단' if now_kosdaq else '허용'}"
            )
            if (prev_kospi != now_kospi or prev_kosdaq != now_kosdaq) and self._notifier:
                try:
                    hhmm = datetime.now().strftime("%H:%M")
                    k = "차단" if now_kospi else "허용"
                    q = "차단" if now_kosdaq else "허용"
                    self._notifier.send(
                        f"[INTRADAY] {hhmm} 장중 필터 상태 변경 — KOSPI {k} / KOSDAQ {q}"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[SCHED] 장중 필터 갱신 실패: {e}")

    # ── 업타임 체크 ──

    async def check_uptime_sanity(self) -> None:
        from datetime import timedelta as _td
        elapsed = datetime.now() - self._process_start
        hours = int(elapsed.total_seconds() / 3600)
        if elapsed < _td(hours=24):
            return
        tag = "48시간 이상" if elapsed >= _td(hours=48) else f"{hours}시간"
        msg = f"[SANITY] 프로세스 {tag} 연속 가동 중 — 재시작 권장 (시작: {self._process_start.strftime('%m/%d %H:%M')})"
        logger.warning(msg)
        if self._notifier and self._config.notifications.uptime_sanity:
            try:
                self._notifier.send(
                    f"[안내] 프로세스 {tag} 연속 가동 중\n시작: {self._process_start.strftime('%Y-%m-%dT%H:%M:%S')}\n재시작을 권장합니다"
                )
            except Exception as e:
                logger.warning(f"uptime sanity 알림 실패: {e}")

    # ── 수동 청산 (UI command) ──

    async def manual_close_one(self, ticker: str) -> None:
        if not self._risk_manager or not self._order_manager:
            return
        pos = self._risk_manager.get_open_positions().get(ticker)
        if not pos:
            logger.warning(f"[MANUAL-CLOSE] {ticker} 포지션 없음")
            return
        qty = pos.remaining_qty or pos.qty or 0
        if qty <= 0:
            return
        close_price = int(self._state.latest_prices.get(ticker, pos.entry_price))
        entry = pos.entry_price
        pnl = (close_price - entry) * qty if entry > 0 else 0
        pnl_pct = ((close_price / entry) - 1) * 100 if entry > 0 else 0
        strategy_name = pos.strategy or "unknown"
        vi = getattr(self, "_vi_handler", None)
        prefer_best = vi.should_use_best_limit(ticker) if vi else False
        result = await self._order_manager.execute_sell_force_close(
            ticker=ticker, qty=qty, price=close_price,
            strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
            exit_reason="manual_close",
            prefer_best_limit=prefer_best,
            on_rejection=lambda tk, rt: (
                vi.flag_suspected(tk, f"주문 거부 (rt_cd={rt})") if vi else None
            ),
        )
        if result is None:
            logger.error(f"[MANUAL-CLOSE] 주문 실패: {ticker}")
            return
        if self._paper_mode:
            self._risk_manager.settle_sell(ticker, float(close_price), qty)
            strat_info = self._state.active_strategies.get(ticker)
            if strat_info:
                strat_info["strategy"].on_exit()
            logger.bind(
                event="exit", ticker=ticker, reason="manual_close",
                price=int(close_price), qty=qty, pnl=int(pnl), pnl_pct=round(pnl_pct, 2),
            ).info(f"수동 청산: {ticker} {qty}주 @ {close_price:,} PnL={pnl:+,.0f}")
        else:
            self._order_tracker.submit(result["order_no"], ticker, "sell", qty)
            logger.info(
                f"[ORDER-TRACK] {result['order_no']} SUBMIT {ticker} sell {qty} (manual_close)"
            )

    # ── 기동 시퀀스 ──

    async def startup(
        self,
        screener_scheduler,
        progress_fn,
    ) -> tuple[list, str]:
        """장애 복구 점검 + 유니버스 로드 + WS 연결 + OHLCV 갱신."""
        try:
            await self.check_uptime_sanity()
            restored = await self._risk_manager.restore_from_db()
            if restored and self._notifier:
                try:
                    self._notifier.send(f"[복구] DB에서 오픈 포지션 {restored}건 복원 — API 대조 진행")
                except Exception:
                    pass
            api_balance = await self._rest_client.get_account_balance()
            holdings = [
                {"ticker": h["pdno"], "qty": int(h["hldg_qty"])}
                for h in api_balance.get("output1", [])
                if int(h.get("hldg_qty", 0)) > 0
            ]
            mismatches = await self._risk_manager.reconcile_positions(holdings)
            if mismatches:
                self._notifier.send_urgent("포지션 불일치 감지!\n" + "\n".join(mismatches))
        except Exception as e:
            logger.error(f"장애 복구 점검 실패: {e}")

        await self._risk_manager.check_consecutive_losses()

        core_stocks = screener_scheduler.load_universe()
        progress_fn("유니버스 로드", 10)

        final_stocks = core_stocks
        source = "core"
        if self._config.condition_search.enabled:
            try:
                progress_fn("조건검색 중...", 20)
                cond_top = await screener_scheduler.fetch_condition_search_top()
                if cond_top:
                    final_stocks = cond_top
                    source = "condition_search"
                    self._state.pending_cond_top = cond_top
            except Exception as e:
                logger.error(f"[COND] 시작 시 조건검색 실패: {e} — 코어 유니버스 사용")

        progress_fn(f"종목 확정 {len(final_stocks)}종목", 40)
        screener_scheduler.register_active_strategies(final_stocks)

        # 조건검색 WS 종료 후 메인 WS 연결 (동시 연결 시 서버 강제 끊김 방지)
        try:
            await self._ws_client.connect()
        except Exception as e:
            logger.error(f"WS 연결 실패: {e}")

        final_tickers = [s["ticker"] for s in final_stocks]
        if self._vi_handler is not None:
            self._vi_handler.set_universe(set(final_tickers))
        if final_tickers:
            from core.kiwoom_ws import WS_TYPE_ORDERBOOK
            await self._ws_client.subscribe(final_tickers)
            if self._config.trading.obi_filter_enabled:
                try:
                    await self._ws_client.subscribe(final_tickers, WS_TYPE_ORDERBOOK)
                    logger.info(f"WS 0D(호가) 구독: {len(final_tickers)}종목")
                except Exception as e:
                    logger.warning(f"[OBI] 0D 구독 실패 (0B 구독 유지): {e}")
            logger.info(f"WS 구독: {len(final_tickers)}종목 (source={source})")
            n_unknown = sum(1 for s in final_stocks if s.get("market") == "unknown")
            if n_unknown:
                logger.warning(f"⚠ market 미상 종목 {n_unknown}개 — scripts/update_universe_market.py 실행 권장")

        progress_fn("OHLCV 갱신 중...", 60)

        async def _market_filter_init():
            if self._market_filter is None:
                return
            try:
                await self._market_filter.refresh()
                if self._on_market_status:
                    self._on_market_status(
                        self._market_filter.kospi_strong, self._market_filter.kosdaq_strong
                    )
                if self._notifier:
                    try:
                        k = "강세" if self._market_filter.kospi_strong else "약세"
                        q = "강세" if self._market_filter.kosdaq_strong else "약세"
                        self._notifier.send(f"[MARKET] 시장 필터 갱신 — 코스피 {k} / 코스닥 {q}")
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"시장 필터 초기 갱신 실패: {e}")

        await asyncio.gather(self.refresh_ohlcv(final_stocks), _market_filter_init())
        progress_fn("준비 완료", 100)
        return final_stocks, source

    # ── 08:05 OHLCV + 지수 통합 갱신 ──

    async def refresh_ohlcv_all(self) -> None:
        """스케줄러 08:05 잡 — 지수 캔들 + OHLCV + 알림."""
        await self.refresh_index_candles()
        await self.refresh_ohlcv()
        if self._notifier and self._config.notifications.ohlcv_refresh:
            try:
                self._notifier.send(
                    f"[자동] 08:05 전일 OHLCV 갱신 완료 — {len(self._state.active_strategies)}종목"
                )
            except Exception:
                pass

    async def skip_universe_refresh(self) -> None:
        """주간 유니버스 갱신 스킵 (추세 필터 미구현)."""
        logger.warning("[UNIVERSE] 주간 자동 갱신 건너뜀 — 추세 필터 구현/검증 대기")
        if self._notifier and self._config.notifications.universe_refresh:
            try:
                self._notifier.send_urgent("[알림] 주간 유니버스 갱신 건너뜀\n사유: 추세 필터 구현/검증 대기")
            except Exception:
                pass

    # ── 동기 클린업 ──

    def cleanup_sync(self, loop) -> None:
        """QThread 종료 시 동기 클린업."""
        if not loop or loop.is_closed():
            return
        import time as _time
        deadline = _time.time() + 3.0

        def _safe_run(coro, label: str):
            remaining = deadline - _time.time()
            if remaining <= 0:
                logger.warning(f"클린업 시간 초과, {label} 스킵")
                return
            try:
                loop.run_until_complete(asyncio.wait_for(coro, timeout=min(remaining, 1.0)))
            except asyncio.TimeoutError:
                logger.warning(f"클린업 타임아웃 ({label})")
            except Exception as e:
                logger.warning(f"클린업 오류 ({label}): {e}")

        try:
            for t in asyncio.all_tasks(loop):
                t.cancel()
            loop.run_until_complete(asyncio.sleep(0.1))
        except Exception:
            pass
        if self._ws_client:
            _safe_run(self._ws_client.disconnect(), "ws")
        if self._notifier:
            if self._config and self._config.notifications.system_stop:
                mode_tag = "[PAPER] " if self._paper_mode else ""
                try:
                    self._notifier.send(f"{mode_tag}시스템 종료 (GUI)", retries=1)
                except Exception as e:
                    logger.warning(f"클린업 오류 (notify): {e}")
            try:
                self._notifier.aclose()
            except Exception as e:
                logger.warning(f"클린업 오류 (notifier_close): {e}")
        if self._rest_client:
            _safe_run(self._rest_client.aclose(), "rest")
        if self._db:
            _safe_run(self._db.close(), "db")
        logger.info("클린업 완료")

    # ── 전략 변경 (UI 요청) ──

    async def strategy_change(self, strategy_name: str) -> None:
        from strategy.momentum_strategy import MomentumStrategy
        norm = strategy_name.lower() if strategy_name else ""
        if self._config:
            object.__setattr__(self._config, "force_strategy", norm)
        if norm not in ("", "momentum", "orb", "multi"):
            logger.warning(f"전략 변경 요청 무시: {strategy_name} — momentum/orb/multi만 지원")
            return
        if norm == "orb":
            from strategy.orb_strategy import ORBStrategy
            for ticker, info in self._state.active_strategies.items():
                old_strat = info["strategy"]
                new_strat = ORBStrategy(self._config.trading)
                new_strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                if hasattr(new_strat, "set_prev_day_data"):
                    new_strat.set_prev_day_data(
                        getattr(old_strat, "_prev_day_high", 0.0),
                        getattr(old_strat, "_prev_day_volume", 0),
                        getattr(old_strat, "_prev_day_close", 0.0),
                    )
                info["strategy"] = new_strat
            logger.info("전략 수동 변경: orb")
            return
        if norm == "multi":
            from strategy.orb_strategy import ORBStrategy
            for ticker, info in self._state.active_strategies.items():
                old_strat = info["strategy"]
                # ORB 전략 (primary)
                orb_strat = ORBStrategy(self._config.trading)
                orb_strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                if hasattr(orb_strat, "set_prev_day_data"):
                    orb_strat.set_prev_day_data(
                        getattr(old_strat, "_prev_day_high", 0.0),
                        getattr(old_strat, "_prev_day_volume", 0),
                        getattr(old_strat, "_prev_day_close", 0.0),
                    )
                info["strategy"] = orb_strat
                # 모멘텀 전략 (09:30 이후 전환용)
                mom_strat = MomentumStrategy(self._config.trading)
                mom_strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                if hasattr(mom_strat, "set_prev_day_data"):
                    prev_high = getattr(old_strat, "_prev_day_high", 0.0)
                    prev_vol = getattr(old_strat, "_prev_day_volume", 0)
                    if prev_high > 0:
                        mom_strat.set_prev_day_data(prev_high, prev_vol)
                info["momentum_strategy"] = mom_strat
            logger.info("전략 수동 변경: multi (ORB+모멘텀)")
            return
        if norm == "momentum":
            for ticker, info in self._state.active_strategies.items():
                old_strat = info["strategy"]
                new_strat = MomentumStrategy(self._config.trading)
                new_strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                if hasattr(new_strat, "set_prev_day_data"):
                    prev_high = getattr(old_strat, "_prev_day_high", 0.0)
                    prev_vol = getattr(old_strat, "_prev_day_volume", 0)
                    if prev_high > 0:
                        new_strat.set_prev_day_data(prev_high, prev_vol)
                info["strategy"] = new_strat
            self._state.active_strategy = (
                list(self._state.active_strategies.values())[0]["strategy"]
                if self._state.active_strategies else MomentumStrategy(self._config.trading)
            )
            logger.info("전략 수동 변경: momentum")
        elif not strategy_name:
            logger.info("전략 Auto 모드로 전환")

    # ── 수동 일일 리셋 (OHLCV 갱신 없이 카운터만) ──

    async def quick_reset(self) -> None:
        if self._risk_manager:
            self._risk_manager.reset_daily()
        self._state.daily_halt_notified = False
        if self._candle_builder is not None:
            self._candle_builder.reset()
        self._state.candle_history.clear()
        self._state.breakout_detected.clear()
        self._state.tick_signaled.clear()
        self._state.active_strategy = None
        logger.info("일일 리셋 완료")
