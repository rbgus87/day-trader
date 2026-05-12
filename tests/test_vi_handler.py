"""tests/test_vi_handler.py — VIHandler 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def test_import_vi_handler():
    """모듈/클래스/enum이 import 가능."""
    from core.vi_handler import VIHandler, VIState
    assert VIState.NORMAL.value == "normal"
    assert VIState.STATIC_VI.value == "static_vi"
    assert VIState.SUSPECTED.value == "suspected"
