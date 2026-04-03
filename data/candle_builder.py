"""data/candle_builder.py — 실시간 분봉 생성 + VWAP."""

import asyncio
from collections import defaultdict
from datetime import datetime

from loguru import logger


class CandleBuilder:
    """틱 데이터 → 1분/5분 캔들 생성, VWAP 계산."""

    def __init__(
        self,
        candle_queue: asyncio.Queue,
        timeframes: list[str] | None = None,
    ):
        self._candle_queue = candle_queue
        self._timeframes = timeframes or ["1m"]
        self._building: dict[str, dict] = {}
        self._min1_buffer: dict[str, list[dict]] = defaultdict(list)
        self._vwap_accum: dict[str, dict] = defaultdict(lambda: {"pv_sum": 0.0, "vol_sum": 0})
        self._date_str: str | None = None  # 백테스트 모드에서 날짜 주입용

    def set_date(self, date_str: str) -> None:
        """백테스트 모드에서 날짜를 외부에서 주입."""
        self._date_str = date_str

    async def on_tick(self, tick: dict) -> None:
        ticker = tick["ticker"]
        price = tick["price"]
        volume = tick["volume"]
        time_str = tick["time"]
        minute_key = time_str[:4]

        self._vwap_accum[ticker]["pv_sum"] += price * volume
        self._vwap_accum[ticker]["vol_sum"] += volume

        current = self._building.get(ticker)
        if current is None or current["_minute_key"] != minute_key:
            if current is not None:
                await self._emit_candle(current)
            date_part = self._date_str or datetime.now().strftime("%Y-%m-%d")
            self._building[ticker] = {
                "ticker": ticker,
                "tf": "1m",
                "_minute_key": minute_key,
                "ts": f"{date_part}T{time_str[:2]}:{time_str[2:4]}:00",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "vwap": None,
            }
        else:
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
            current["volume"] += volume

    async def _emit_candle(self, candle: dict) -> None:
        ticker = candle["ticker"]
        accum = self._vwap_accum[ticker]
        if accum["vol_sum"] > 0:
            candle["vwap"] = accum["pv_sum"] / accum["vol_sum"]

        out = {k: v for k, v in candle.items() if not k.startswith("_")}
        await self._candle_queue.put(out)
        logger.trace(f"1분봉 완성: {ticker} {candle['ts']} C={candle['close']}")

        if "5m" in self._timeframes:
            self._min1_buffer[ticker].append(out)
            if len(self._min1_buffer[ticker]) >= 5:
                await self._emit_5m_candle(ticker)

    async def _emit_5m_candle(self, ticker: str) -> None:
        buf = self._min1_buffer[ticker][:5]
        self._min1_buffer[ticker] = self._min1_buffer[ticker][5:]

        candle_5m = {
            "ticker": ticker,
            "tf": "5m",
            "ts": buf[0]["ts"],
            "open": buf[0]["open"],
            "high": max(c["high"] for c in buf),
            "low": min(c["low"] for c in buf),
            "close": buf[-1]["close"],
            "volume": sum(c["volume"] for c in buf),
            "vwap": buf[-1].get("vwap"),
        }
        await self._candle_queue.put(candle_5m)
        logger.debug(f"5분봉 완성: {ticker} {candle_5m['ts']}")

    async def flush(self) -> None:
        for ticker, candle in list(self._building.items()):
            await self._emit_candle(candle)
        self._building.clear()

    def reset(self) -> None:
        self._building.clear()
        self._min1_buffer.clear()
        self._vwap_accum.clear()
