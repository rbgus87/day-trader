"""tests/test_resolve_market.py — 조건검색 추가 종목의 시장 분류 헬퍼 테스트.

EngineWorker._resolve_market은 ka10099로 받은 KOSPI/KOSDAQ 코드 set과 ticker를
대조해 'kospi' / 'kosdaq' / 'unknown'을 반환한다. 'unknown' 분류 시 market_filter가
OR fallback으로 약화되므로 정확성이 중요.
"""

from gui.workers.engine_worker import EngineWorker


def test_resolve_market_kospi():
    codes = {"kospi": {"005930", "000660"}, "kosdaq": {"035720"}}
    assert EngineWorker._resolve_market("005930", codes) == "kospi"


def test_resolve_market_kosdaq():
    codes = {"kospi": {"005930"}, "kosdaq": {"247540", "035720"}}
    assert EngineWorker._resolve_market("247540", codes) == "kosdaq"


def test_resolve_market_unknown_when_not_in_either():
    codes = {"kospi": {"005930"}, "kosdaq": {"035720"}}
    assert EngineWorker._resolve_market("999999", codes) == "unknown"


def test_resolve_market_unknown_when_cache_none():
    """ka10099 조회 실패 시 캐시는 None — 안전하게 'unknown' 반환."""
    assert EngineWorker._resolve_market("005930", None) == "unknown"


def test_resolve_market_unknown_when_keys_missing():
    """캐시 dict에 'kospi'/'kosdaq' 키가 없어도 KeyError 없이 'unknown'."""
    assert EngineWorker._resolve_market("005930", {}) == "unknown"
