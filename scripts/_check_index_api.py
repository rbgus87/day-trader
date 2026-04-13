"""임시 진단 스크립트 — ka20006 응답 구조 확인."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AppConfig
from core.auth import TokenManager
from core.rate_limiter import AsyncRateLimiter
from core.kiwoom_rest import KiwoomRestClient
from core.market_filter import MarketFilter


async def main():
    cfg = AppConfig.from_yaml()
    tm = TokenManager(
        cfg.kiwoom.app_key, cfg.kiwoom.secret_key, cfg.kiwoom.rest_base_url
    )
    rl = AsyncRateLimiter(
        max_calls=cfg.kiwoom.rate_limit_calls, period=cfg.kiwoom.rate_limit_period
    )
    rest = KiwoomRestClient(cfg.kiwoom, tm, rl)
    try:
        for code, label in [("001", "KOSPI"), ("101", "KOSDAQ")]:
            print(f"\n===== {label} ({code}) =====")
            raw = await rest.get_index_daily(code)
            print("top-level keys:", list(raw.keys()))
            for k, v in raw.items():
                if isinstance(v, list) and v:
                    print(f"[list key] '{k}' len={len(v)}")
                    print("first item fields:", list(v[0].keys()))
                    print("first 3 items:")
                    print(json.dumps(v[:3], ensure_ascii=False, indent=2))
                    break
            else:
                print("NO LIST CONTAINER FOUND. full response:")
                print(json.dumps(raw, ensure_ascii=False, indent=2)[:2000])

        print("\n===== MarketFilter.refresh() =====")
        mf = MarketFilter(rest)
        await mf.refresh()
        print(f"kospi_strong  = {mf.kospi_strong}")
        print(f"kosdaq_strong = {mf.kosdaq_strong}")
    finally:
        await rest.aclose()


if __name__ == "__main__":
    asyncio.run(main())
