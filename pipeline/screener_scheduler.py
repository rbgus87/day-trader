"""pipeline/screener_scheduler.py — 스크리닝 + 유니버스 관리.

_run_screening / _apply_condition_search_universe / _fetch_condition_search_top /
_register_active_strategies 로직을 engine_worker에서 분리.
PyQt6 미사용.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml
from loguru import logger

from pipeline.trading_state import TradingState


def write_universe_yaml(
    top: list[dict],
    path: Path | str = "config/universe.yaml",
) -> None:
    """조건검색 결과를 universe.yaml에 atomic write."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    bak = p.with_suffix(p.suffix + ".bak")
    header = (
        "# ============================================================================\n"
        f"# universe.yaml — 조건검색 자동 갱신 "
        f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
        "# 생성: gui.workers.engine_worker._write_universe_yaml\n"
        "# ============================================================================\n\n"
    )
    body = yaml.safe_dump(
        {"stocks": [
            {"ticker": str(s["ticker"]), "name": s.get("name", ""), "market": s.get("market", "unknown")}
            for s in top
        ]},
        allow_unicode=True, sort_keys=False, default_flow_style=False,
    )
    tmp.write_text(header + body, encoding="utf-8")
    if p.exists():
        if bak.exists():
            bak.unlink()
        p.replace(bak)
    tmp.replace(p)


class ScreenerScheduler:
    """스크리닝 실행 + 조건검색 유니버스 갱신 + 전략 등록."""

    def __init__(
        self,
        rest_client,
        token_manager,
        ws_client,
        config,
        notifier,
        db,
        candidate_collector,
        pre_market_screener,
        state: TradingState,
    ):
        self._rest_client = rest_client
        self._token_manager = token_manager
        self._ws_client = ws_client
        self._config = config
        self._notifier = notifier
        self._db = db
        self._candidate_collector = candidate_collector
        self._pre_market_screener = pre_market_screener
        self._state = state

    # ── 유니버스 로드 ──

    def load_universe(self) -> list[dict]:
        """universe.yaml 로드 + _ticker_markets / _ticker_names 갱신."""
        uni_path = Path("config/universe.yaml")
        if not uni_path.exists():
            logger.error(f"universe.yaml 없음: {uni_path}")
            return []
        uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
        stocks = uni.get("stocks", [])
        self._state.ticker_markets = {s["ticker"]: s.get("market", "unknown") for s in stocks}
        self._state.ticker_names = {s["ticker"]: s.get("name", s["ticker"]) for s in stocks}
        return stocks

    # ── 전략 등록 ──

    def register_active_strategies(self, stocks: list[dict]) -> None:
        """유니버스 종목에 MomentumStrategy 인스턴스 등록.

        gap_pullback_enabled=true 시 GapPullbackStrategy도 함께 등록.
        """
        from strategy.momentum_strategy import MomentumStrategy

        prev_data: dict[str, tuple[float, int, float]] = {}
        for ticker, info in (self._state.active_strategies or {}).items():
            old = info.get("strategy") if isinstance(info, dict) else None
            high = getattr(old, "_prev_day_high", 0.0)
            vol = getattr(old, "_prev_day_volume", 0)
            close = getattr(old, "_prev_day_close", 0.0)
            if high > 0:
                prev_data[ticker] = (float(high), int(vol), float(close))

        gap_enabled = getattr(self._config.trading, "gap_pullback_enabled", False)
        if gap_enabled:
            from strategy.gap_pullback_strategy import GapPullbackStrategy

        self._state.active_strategies = {}
        self._state.gap_strategies = {}
        self._state.intraday_added_tickers.clear()
        self._state.intraday_add_count = 0
        self._state.ticker_sources = {}
        for s in stocks:
            ticker = s["ticker"]
            strat = MomentumStrategy(self._config.trading)
            strat.configure_multi_trade(
                max_trades=self._config.trading.max_trades_per_day,
                cooldown_minutes=self._config.trading.cooldown_minutes,
            )
            if hasattr(strat, "set_ticker"):
                strat.set_ticker(ticker)
            if ticker in prev_data and hasattr(strat, "set_prev_day_data"):
                ph, pv, pc = prev_data[ticker]
                strat.set_prev_day_data(ph, pv, pc)
            self._state.active_strategies[ticker] = {
                "strategy": strat, "name": s.get("name", ticker), "score": 0,
            }
            if "market" in s:
                self._state.ticker_markets[ticker] = s["market"]
            self._state.ticker_names[ticker] = s.get("name", ticker)
            self._state.ticker_sources[ticker] = "day_momentum"

            # 갭 전략 등록 (enabled 시)
            if gap_enabled:
                gap_strat = GapPullbackStrategy(self._config.trading)
                gap_strat.set_ticker(ticker)
                if ticker in prev_data:
                    _, pv, pc = prev_data[ticker]
                    gap_strat.set_prev_day_data(0.0, pv, pc)
                self._state.gap_strategies[ticker] = gap_strat

        self._state.active_strategy = (
            list(self._state.active_strategies.values())[0]["strategy"]
            if self._state.active_strategies else None
        )
        logger.info(
            f"유니버스 전체 전략 등록: {len(self._state.active_strategies)}종목"
            + (f" + gap_pullback {len(self._state.gap_strategies)}종목" if gap_enabled else "")
        )

    # ── 조건검색 ──

    async def ensure_market_codes_cache(self) -> dict[str, set[str]] | None:
        if self._state.market_codes_cache is not None:
            return self._state.market_codes_cache
        try:
            kospi = await self._rest_client.get_stock_list_by_market("0")
            kosdaq = await self._rest_client.get_stock_list_by_market("10")
        except Exception as e:
            logger.error(f"[MARKET-CODES] ka10099 조회 실패: {e}")
            return None

        def _codes(rows: list[dict]) -> set[str]:
            return {
                (s.get("code") or s.get("stk_cd") or s.get("shcode") or "").strip()
                for s in rows
            } - {""}

        kospi_codes = _codes(kospi)
        kosdaq_codes = _codes(kosdaq)
        if not kospi_codes or not kosdaq_codes:
            logger.warning(
                f"[MARKET-CODES] 응답 비어있음: KOSPI {len(kospi_codes)}, "
                f"KOSDAQ {len(kosdaq_codes)} — 캐시 미저장"
            )
            return None
        self._state.market_codes_cache = {"kospi": kospi_codes, "kosdaq": kosdaq_codes}
        logger.info(
            f"[MARKET-CODES] 캐시 구축: KOSPI {len(kospi_codes)}, KOSDAQ {len(kosdaq_codes)}"
        )
        return self._state.market_codes_cache

    @staticmethod
    def resolve_market(ticker: str, market_codes: dict[str, set[str]] | None) -> str:
        if not market_codes:
            return "unknown"
        if ticker in market_codes.get("kospi", set()):
            return "kospi"
        if ticker in market_codes.get("kosdaq", set()):
            return "kosdaq"
        return "unknown"

    async def fetch_condition_search_top(self) -> list[dict] | None:
        """조건검색 실행 → 거래대금 정렬 → top N 반환."""
        cs_cfg = self._config.condition_search
        if not cs_cfg.enabled:
            return None
        try:
            from core.condition_search import run_condition_search
            token = await self._token_manager.get_token()
            cs_results = await run_condition_search(
                ws_url=self._config.kiwoom.ws_url,
                access_token=token,
                condition_name=cs_cfg.condition_name,
            )
        except Exception as e:
            logger.error(f"[COND] 조건검색 실행 실패: {e}")
            return None

        if not cs_results:
            logger.warning("[COND] 조건검색 결과 비어있음")
            return None

        from core.indicators import calculate_atr, calculate_atr_pct
        import pandas as pd

        base_dt = datetime.now().strftime("%Y%m%d")
        semaphore = asyncio.Semaphore(5)
        total_cs = len(cs_results)
        completed = 0

        async def enrich_one(stock: dict) -> dict | None:
            nonlocal completed
            ticker = stock.get("code", "").strip()
            if not ticker:
                completed += 1
                return None
            async with semaphore:
                try:
                    daily = await self._rest_client.get_daily_ohlcv(ticker, base_dt=base_dt)
                    items = (
                        daily.get("stk_dt_pole_chart_qry")
                        or daily.get("output2")
                        or daily.get("output")
                        or []
                    )
                    if len(items) < 2:
                        return None
                    self._state.daily_ohlcv_cache[ticker] = items
                    prev = items[1]
                    prev_close = abs(float(prev.get("cur_prc", prev.get("stck_clpr", 0))))
                    prev_volume = abs(int(
                        prev.get("trde_qty", prev.get("acml_vol", prev.get("acml_vlmn", 0)))
                    ))
                    amount = prev_close * prev_volume
                    if len(items) >= 15:
                        try:
                            rows = []
                            for it in items[:30]:
                                h = abs(float(it.get("high_pric", it.get("stck_hgpr", 0)) or 0))
                                l = abs(float(it.get("low_pric", it.get("stck_lwpr", 0)) or 0))
                                c = abs(float(it.get("cur_prc", it.get("stck_clpr", 0)) or 0))
                                if h > 0 and l > 0 and c > 0:
                                    rows.append((h, l, c))
                            if len(rows) >= 15:
                                rows.reverse()
                                df = pd.DataFrame(rows, columns=["high", "low", "close"])
                                atr = calculate_atr(df, length=14)
                                atr_pct_series = calculate_atr_pct(atr, df["close"])
                                latest = atr_pct_series.dropna()
                                if len(latest) > 0:
                                    self._state.ticker_atr_pct[ticker] = float(latest.iloc[-1])
                        except Exception as e:
                            logger.debug(f"[COND] {ticker} ATR 계산 실패: {e}")
                    if amount > 0:
                        return {"ticker": ticker, "name": stock.get("name", ticker), "_amount": amount}
                    return None
                except Exception as e:
                    logger.debug(f"[COND] {ticker} 일봉 조회 실패: {e}")
                    return None
                finally:
                    completed += 1
                    if completed % 50 == 0 or completed == total_cs:
                        logger.info(f"[COND] enrichment 진행: {completed}/{total_cs}")

        gather_results = await asyncio.gather(*[enrich_one(s) for s in cs_results])
        enriched = [r for r in gather_results if r is not None]
        enriched.sort(key=lambda x: x["_amount"], reverse=True)
        top = enriched[: cs_cfg.max_watch_stocks]
        logger.info(f"[COND] 조건검색 결과: {len(cs_results)}종목, 필터 후 {len(top)}종목")
        if not top:
            return None

        market_codes = await self.ensure_market_codes_cache()
        result = [
            {
                "ticker": s["ticker"], "name": s["name"],
                "market": self.resolve_market(s["ticker"], market_codes),
            }
            for s in top
        ]
        try:
            write_universe_yaml(result)
            logger.info(f"[UNIVERSE] 유니버스 갱신: {len(result)}종목 저장")
        except Exception as e:
            logger.warning(f"[UNIVERSE] 저장 실패: {e}")
        return result

    async def apply_condition_search_universe(self) -> None:
        """조건검색 결과로 _active_strategies + WS 구독 동기화."""
        if self._state.pending_cond_top is not None:
            top = self._state.pending_cond_top
            self._state.pending_cond_top = None
            logger.info(f"[COND] startup 캐시 재사용: {len(top)}종목")
        else:
            top = await self.fetch_condition_search_top()
        if top is None:
            logger.warning("[COND] 조건검색 결과 없음 — 기존 감시 종목 유지")
            return

        old_tickers = set(self._state.active_strategies.keys())
        new_tickers = {s["ticker"] for s in top}
        added = new_tickers - old_tickers
        removed = old_tickers - new_tickers
        logger.info(
            f"[COND] 감시 종목 갱신: 기존 {len(old_tickers)} → 신규 {len(new_tickers)}"
        )
        self.register_active_strategies(top)
        try:
            if removed:
                await self._ws_client.unsubscribe(list(removed))
            if added:
                await self._ws_client.subscribe(list(added))
        except Exception as e:
            logger.warning(f"[COND] WS 구독 갱신 실패: {e} — 다음 재연결 시 자동 복원")
        return added  # caller가 OHLCV refresh 여부 판단

    async def run_screening(self, refresh_ohlcv_fn: Callable | None = None) -> None:
        """08:30 장 전 스크리닝 — score 업데이트."""
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"스크리닝 시작 ({today})")
        try:
            added = await self.apply_condition_search_universe()
            if added and refresh_ohlcv_fn:
                new_stock_dicts = [
                    s for s in (self._state.active_strategies.keys())
                    if s in added
                ]
                # refresh_ohlcv_fn은 SessionManager.refresh_ohlcv에 바인딩됨
                try:
                    await refresh_ohlcv_fn([
                        {"ticker": t} for t in added
                    ])
                except Exception as e:
                    logger.error(f"[COND] 신규 종목 OHLCV 갱신 실패: {e}")
        except Exception as e:
            logger.error(f"[COND] 조건검색 통합 실패: {e} — 코어 유니버스 유지")

        try:
            candidates = await self._candidate_collector.collect()
            if not candidates:
                logger.warning("candidates 없음")
                self._notifier.send("스크리닝: candidates 없음")
                return
            screened = await self._pre_market_screener.screen(candidates)
            if not screened:
                logger.warning("스크리닝 통과 종목 없음")
                self._notifier.send("스크리닝: 통과 종목 없음")
                return
            self._state.screener_results = screened
            await self._pre_market_screener.save_results(today, screened)
            for s in screened:
                ticker = s["ticker"]
                if ticker in self._state.active_strategies:
                    self._state.active_strategies[ticker]["score"] = s.get("score", 0)
            top_n = self._config.trading.screening_top_n
            selected = screened[:top_n]
            for s in selected:
                tk = s["ticker"]
                try:
                    price_data = await self._rest_client.get_current_price(tk)
                    output = price_data.get("output1", {})
                    cur_price = abs(int(output.get("cur_pric", 0)))
                    if cur_price > 0:
                        self._state.latest_prices[tk] = cur_price
                except Exception as e:
                    logger.warning(f"현재가 초기화 실패 ({tk}): {e}")
            force = getattr(self._config, "force_strategy", "") or "auto"
            logger.info(
                f"스크리닝 완료: {len(screened)}종목 통과, "
                f"감시: {len(self._state.active_strategies)}종목 유지"
            )
            self._notifier.send(
                f"스크리닝 완료 — {force}\n"
                f"필터 통과: {len(screened)}종목\n"
                f"전체 감시: {len(self._state.active_strategies)}종목\n"
                f"상위:\n"
                + "\n".join(
                    f"  {s.get('name','')} ({s['ticker']}) 점수:{s.get('score',0):.1f}"
                    for s in selected
                )
            )
        except Exception as exc:
            import traceback
            logger.error(f"스크리닝 실패: {exc}\n{traceback.format_exc()}")
            try:
                self._notifier.send_urgent(f"스크리닝 오류: {exc}")
            except Exception:
                pass

    # ── 장중 조건검색 ──

    async def run_intraday_search(self, refresh_ohlcv_fn=None) -> list[dict]:
        """장중 intraday_leader 조건검색 → 신규 종목 동적 추가.

        조건검색 WS와 메인 WS 순차 실행 (동시 연결 금지).
        enrichment 실패 종목은 스킵 — 전체 중단 없음.
        Returns: 추가된 종목 list[dict]
        """
        is_cfg = getattr(self._config, "intraday_search", None)
        if not is_cfg or not is_cfg.enabled:
            return []

        if self._state.intraday_add_count >= is_cfg.max_total_added:
            logger.info(
                f"[INTRADAY] 장중 추가 한도 도달 ({is_cfg.max_total_added}) — 검색 스킵"
            )
            return []

        logger.info(f"[INTRADAY] 조건검색 시작: {is_cfg.condition_name}")
        try:
            from core.condition_search import run_condition_search
            token = await self._token_manager.get_token()
            # 키움 서버는 동일 토큰으로 2개 WS를 허용하지 않음 — 조건검색 전 메인 WS 종료
            logger.info("[INTRADAY] 메인 WS 일시 종료 (조건검색 WS 연결 전)")
            await self._ws_client.disconnect()
            try:
                cs_results = await run_condition_search(
                    ws_url=self._config.kiwoom.ws_url,
                    access_token=token,
                    condition_name=is_cfg.condition_name,
                )
            finally:
                logger.info("[INTRADAY] 메인 WS 재연결 (조건검색 완료)")
                await self._ws_client.connect()
        except Exception as e:
            logger.error(f"[INTRADAY] 조건검색 실패: {e}")
            return []

        if not cs_results:
            logger.info("[INTRADAY] 조건검색 결과 없음")
            return []

        # 기존 감시 종목 제외
        existing = set(self._state.active_strategies.keys())
        new_codes = [s for s in cs_results if s.get("code", "").strip() not in existing]
        if not new_codes:
            logger.info("[INTRADAY] 신규 종목 없음 — 모두 감시 중")
            return []

        remaining = is_cfg.max_total_added - self._state.intraday_add_count
        limit = min(is_cfg.max_add_per_search, remaining)

        market_codes = await self.ensure_market_codes_cache()
        added = await self._enrich_intraday_additions(new_codes, limit, market_codes)
        if not added:
            logger.info("[INTRADAY] enrichment 후 추가 가능한 종목 없음")
            return []

        # 전략 등록
        from strategy.momentum_strategy import MomentumStrategy
        for s in added:
            ticker = s["ticker"]
            strat = MomentumStrategy(self._config.trading)
            strat.configure_multi_trade(
                max_trades=self._config.trading.max_trades_per_day,
                cooldown_minutes=self._config.trading.cooldown_minutes,
            )
            if hasattr(strat, "set_ticker"):
                strat.set_ticker(ticker)
            self._state.active_strategies[ticker] = {
                "strategy": strat, "name": s.get("name", ticker), "score": 0,
            }
            self._state.ticker_markets[ticker] = s.get("market", "unknown")
            self._state.ticker_names[ticker] = s.get("name", ticker)
            self._state.intraday_added_tickers.add(ticker)
            self._state.ticker_sources[ticker] = "intraday_leader"

        self._state.intraday_add_count += len(added)

        # OHLCV + 분봉 시드 주입 (daily_ohlcv_cache에 사전 적재 → 재조회 없음)
        if refresh_ohlcv_fn:
            try:
                await refresh_ohlcv_fn([{"ticker": s["ticker"]} for s in added])
            except Exception as e:
                logger.warning(f"[INTRADAY] 신규 종목 OHLCV 주입 실패: {e}")

        # WS 구독 추가
        new_tickers = [s["ticker"] for s in added]
        await self._manage_ws_subscriptions(new_tickers)

        logger.bind(
            event="intraday_search",
            added=len(added),
            total_watched=len(self._state.active_strategies),
            total_intraday=self._state.intraday_add_count,
        ).info(
            f"[INTRADAY] 장중 종목 추가: +{len(added)}"
            f" → 감시 총 {len(self._state.active_strategies)}종목"
            f" (누적 {self._state.intraday_add_count}/{is_cfg.max_total_added})"
        )
        try:
            names = ", ".join(s.get("name", s["ticker"]) for s in added)
            self._notifier.send(
                f"[INTRADAY] 장중 종목 추가 +{len(added)}\n{names}"
            )
        except Exception:
            pass

        return added

    async def _enrich_intraday_additions(
        self,
        candidates: list[dict],
        limit: int,
        market_codes: dict | None,
    ) -> list[dict]:
        """신규 intraday 종목 enrichment — daily_ohlcv_cache에 적재."""
        base_dt = datetime.now().strftime("%Y%m%d")
        semaphore = asyncio.Semaphore(5)

        async def enrich_one(stock: dict) -> dict | None:
            ticker = stock.get("code", "").strip()
            if not ticker:
                return None
            try:
                async with semaphore:
                    daily = await self._rest_client.get_daily_ohlcv(ticker, base_dt=base_dt)
                items = (
                    daily.get("stk_dt_pole_chart_qry")
                    or daily.get("output2")
                    or daily.get("output")
                    or []
                )
                if len(items) < 2:
                    return None
                self._state.daily_ohlcv_cache[ticker] = items

                if len(items) >= 15:
                    try:
                        from core.indicators import calculate_atr, calculate_atr_pct
                        import pandas as pd
                        rows = []
                        for it in items[:30]:
                            h = abs(float(it.get("high_pric", it.get("stck_hgpr", 0)) or 0))
                            l = abs(float(it.get("low_pric", it.get("stck_lwpr", 0)) or 0))
                            c = abs(float(it.get("cur_prc", it.get("stck_clpr", 0)) or 0))
                            if h > 0 and l > 0 and c > 0:
                                rows.append((h, l, c))
                        if len(rows) >= 15:
                            rows.reverse()
                            df = pd.DataFrame(rows, columns=["high", "low", "close"])
                            atr = calculate_atr(df, length=14)
                            atr_pct_series = calculate_atr_pct(atr, df["close"])
                            latest = atr_pct_series.dropna()
                            if len(latest) > 0:
                                self._state.ticker_atr_pct[ticker] = float(latest.iloc[-1])
                    except Exception:
                        pass

                return {
                    "ticker": ticker,
                    "name": stock.get("name", ticker),
                    "market": self.resolve_market(ticker, market_codes),
                }
            except Exception as e:
                logger.debug(f"[INTRADAY] {ticker} enrichment 실패: {e}")
                return None

        results = await asyncio.gather(*[enrich_one(s) for s in candidates])
        added: list[dict] = []
        for r in results:
            if r is not None and len(added) < limit:
                added.append(r)
        return added

    async def _manage_ws_subscriptions(self, new_tickers: list[str]) -> None:
        """WS 구독 추가. 한도(100) 초과 시 기존 intraday 종목 교체."""
        WS_LIMIT = 100
        WS_TYPE = "0B"
        current_subs = set(self._ws_client._subscriptions.get(WS_TYPE, []))
        available = WS_LIMIT - len(current_subs)

        if len(new_tickers) <= available:
            try:
                await self._ws_client.subscribe(new_tickers, WS_TYPE)
            except Exception as e:
                logger.warning(f"[INTRADAY] WS 구독 실패: {e}")
            return

        # 한도 초과 — intraday 추가 종목 중 교체 후보 선정
        needed = len(new_tickers) - available
        to_remove = [
            t for t in list(self._state.intraday_added_tickers)
            if t not in new_tickers
        ][:needed]

        if to_remove:
            try:
                await self._ws_client.unsubscribe(to_remove, WS_TYPE)
            except Exception as e:
                logger.warning(f"[INTRADAY] WS 구독 해제 실패: {e}")
            for t in to_remove:
                self._state.active_strategies.pop(t, None)
                self._state.ticker_markets.pop(t, None)
                self._state.ticker_names.pop(t, None)
                self._state.intraday_added_tickers.discard(t)
                self._state.ticker_sources.pop(t, None)

        try:
            await self._ws_client.subscribe(new_tickers, WS_TYPE)
        except Exception as e:
            logger.warning(f"[INTRADAY] WS 구독 실패: {e}")

        logger.info(
            f"[INTRADAY] WS 구독 갱신: +{len(new_tickers)} 제거 {len(to_remove)}"
            f" / 현재 구독 {len(self._ws_client._subscriptions.get(WS_TYPE, []))}종목"
        )
