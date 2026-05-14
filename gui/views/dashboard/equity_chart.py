"""실시간 Equity Curve — pyqtgraph 기반 장중 PnL 추이 차트."""
from __future__ import annotations

import math
from datetime import datetime, date as _date

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from gui.design_tokens import Colors
from gui.widgets.card import Card

pg.setConfigOptions(antialias=True, useOpenGL=False)

_MARKET_OPEN_H = 9
_MARKET_OPEN_M = 0
_MARKET_CLOSE_H = 15
_MARKET_CLOSE_M = 30
_TICK_INTERVAL_MIN = 60  # X축 주 눈금 (1시간)


def _min_from_open(ts: float) -> float:
    """Unix timestamp → 09:00 기준 경과 분."""
    dt = datetime.fromtimestamp(ts)
    return (dt.hour - _MARKET_OPEN_H) * 60 + dt.minute + dt.second / 60.0


def _min_to_hhmm(val: float) -> str:
    h = int(val // 60) + _MARKET_OPEN_H
    m = int(val % 60)
    return f"{h:02d}:{m:02d}"


_X_MIN = 0.0          # 09:00
_X_MAX = 6 * 60.0     # 15:00 (표시 범위)


class _TimeAxisItem(pg.AxisItem):
    """분 값을 HH:MM 문자열로 포맷하는 X축."""

    def __init__(self):
        super().__init__(orientation="bottom")
        self.setStyle(tickFont=QFont("Malgun Gothic", 8))

    def tickStrings(self, values, scale, spacing):
        return [_min_to_hhmm(v) for v in values]


class EquityChart(QWidget):
    """장중 실시간 PnL Equity Curve (pyqtgraph)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._xs: list[float] = []   # minutes from open
        self._ys: list[float] = []   # PnL in 원
        self._buy_xs: list[float] = []
        self._buy_ys: list[float] = []
        self._sell_win_xs: list[float] = []
        self._sell_win_ys: list[float] = []
        self._sell_loss_xs: list[float] = []
        self._sell_loss_ys: list[float] = []
        self._build_ui()

    # ── UI 구성 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        card = Card(title="Equity Curve (일중 PnL)")

        axis_bottom = _TimeAxisItem()
        axis_left = pg.AxisItem(orientation="left")
        axis_left.setStyle(tickFont=QFont("Malgun Gothic", 8))

        self._pw = pg.PlotWidget(
            axisItems={"bottom": axis_bottom, "left": axis_left}
        )
        self._pw.setBackground("#313244")
        self._pw.showGrid(x=False, y=True, alpha=0.15)
        self._pw.getPlotItem().hideAxis("top")
        self._pw.getPlotItem().hideAxis("right")

        for ax_name in ("bottom", "left"):
            ax = self._pw.getAxis(ax_name)
            ax.setPen(pg.mkPen("#585b70", width=1))
            ax.setTextPen(pg.mkPen("#6c7086"))

        self._pw.setXRange(_X_MIN, _X_MAX, padding=0)
        self._pw.setYRange(-50_000, 50_000, padding=0.1)

        # X 눈금: 1시간 단위
        self._pw.getAxis("bottom").setTickSpacing(_TICK_INTERVAL_MIN, 30)

        # 0원 기준선
        self._zero_line = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen("#585b70", width=1,
                         style=Qt.PenStyle.DashLine)
        )
        self._pw.addItem(self._zero_line)

        # 메인 라인
        self._line = self._pw.plot(
            [], [], pen=pg.mkPen(Colors.accent_blue, width=2)
        )

        # 상승 fill (PnL ≥ 0)
        self._fill_pos = pg.FillBetweenItem(
            pg.PlotDataItem([0], [0]),
            pg.PlotDataItem([0], [0]),
            brush=pg.mkBrush(166, 227, 161, 30),
        )
        self._pw.addItem(self._fill_pos)

        # 하락 fill (PnL < 0)
        self._fill_neg = pg.FillBetweenItem(
            pg.PlotDataItem([0], [0]),
            pg.PlotDataItem([0], [0]),
            brush=pg.mkBrush(243, 139, 168, 30),
        )
        self._pw.addItem(self._fill_neg)

        # 매수 마커 (파란 원)
        self._buy_scatter = pg.ScatterPlotItem(
            size=9, symbol="o",
            brush=pg.mkBrush(Colors.accent_blue),
            pen=pg.mkPen(None),
        )
        self._pw.addItem(self._buy_scatter)

        # 매도 수익 마커 (초록 원)
        self._sell_win_scatter = pg.ScatterPlotItem(
            size=9, symbol="o",
            brush=pg.mkBrush(Colors.accent_green),
            pen=pg.mkPen(None),
        )
        self._pw.addItem(self._sell_win_scatter)

        # 매도 손실 마커 (빨간 원)
        self._sell_loss_scatter = pg.ScatterPlotItem(
            size=9, symbol="o",
            brush=pg.mkBrush(Colors.accent_red),
            pen=pg.mkPen(None),
        )
        self._pw.addItem(self._sell_loss_scatter)

        # 빈 상태 안내
        self._empty_text = pg.TextItem(
            text="엔진 시작 후 PnL 차트 표시",
            color="#6c7086",
            anchor=(0.5, 0.5),
        )
        self._empty_text.setFont(QFont("Malgun Gothic", 10))
        self._pw.addItem(self._empty_text)
        self._empty_text.setPos((_X_MAX - _X_MIN) / 2, 0)

        card.addWidget(self._pw, stretch=1)
        layout.addWidget(card)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def add_pnl(self, timestamp: float, value: float):
        """1초마다 PnL 추가 (pnl_updated 시그널에서 호출)."""
        x = _min_from_open(timestamp)
        self._xs.append(x)
        self._ys.append(value)
        self._redraw_line()

    def add_trade_marker(self, trade: dict):
        """체결 시 마커 추가 (trade_executed 시그널에서 호출)."""
        side = (trade.get("side", "") or "").lower()
        pnl = trade.get("pnl") or 0

        ts_str = trade.get("time", "")
        if ts_str:
            try:
                now = datetime.now()
                h, m, s = (int(p) for p in ts_str.split(":"))
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                x = _min_from_open(dt.timestamp())
            except Exception:
                x = _min_from_open(datetime.now().timestamp())
        else:
            x = _min_from_open(datetime.now().timestamp())

        y = self._ys[-1] if self._ys else 0.0

        if side == "buy":
            self._buy_xs.append(x)
            self._buy_ys.append(y)
            self._buy_scatter.setData(
                x=np.array(self._buy_xs, dtype=float),
                y=np.array(self._buy_ys, dtype=float),
            )
        elif side == "sell":
            if (pnl or 0) >= 0:
                self._sell_win_xs.append(x)
                self._sell_win_ys.append(y)
                self._sell_win_scatter.setData(
                    x=np.array(self._sell_win_xs, dtype=float),
                    y=np.array(self._sell_win_ys, dtype=float),
                )
            else:
                self._sell_loss_xs.append(x)
                self._sell_loss_ys.append(y)
                self._sell_loss_scatter.setData(
                    x=np.array(self._sell_loss_xs, dtype=float),
                    y=np.array(self._sell_loss_ys, dtype=float),
                )

    def reset(self):
        """일일 리셋 시 호출."""
        self._xs.clear(); self._ys.clear()
        self._buy_xs.clear(); self._buy_ys.clear()
        self._sell_win_xs.clear(); self._sell_win_ys.clear()
        self._sell_loss_xs.clear(); self._sell_loss_ys.clear()
        self._line.setData([], [])
        self._buy_scatter.setData(x=[], y=[])
        self._sell_win_scatter.setData(x=[], y=[])
        self._sell_loss_scatter.setData(x=[], y=[])
        self._empty_text.setVisible(True)

    # ── 내부 렌더링 ──────────────────────────────────────────────────────────

    def _redraw_line(self):
        if not self._xs:
            return

        self._empty_text.setVisible(False)
        xs = np.array(self._xs, dtype=float)
        ys = np.array(self._ys, dtype=float)
        self._line.setData(xs, ys)

        # fill (0 기준선 상/하)
        zero_arr = np.zeros_like(ys)
        line_item = pg.PlotDataItem(xs, ys)
        zero_item = pg.PlotDataItem(xs, zero_arr)
        self._fill_pos.setCurves(line_item, zero_item)
        self._fill_neg.setCurves(zero_item, line_item)

        # Y 범위 자동 조정
        if len(ys) > 0:
            margin = max(abs(ys).max() * 0.15, 20_000)
            self._pw.setYRange(ys.min() - margin, ys.max() + margin, padding=0)
