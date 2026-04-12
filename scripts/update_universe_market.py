"""scripts/update_universe_market.py — universe.yaml에 market 필드 추가.

키움 ka10099 (시장별 종목 리스트)로 코스피/코스닥 종목코드 집합을 받아
universe.yaml의 각 종목에 market: "kospi" | "kosdaq" | "unknown" 추가.

주의: yaml.safe_dump 사용 시 기존 주석이 제거된다. 원본은 git 히스토리에 보존.
"""

import asyncio
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.rate_limiter import AsyncRateLimiter


async def main() -> int:
    cfg = AppConfig.from_yaml()
    tm = TokenManager(
        cfg.kiwoom.app_key,
        cfg.kiwoom.secret_key,
        cfg.kiwoom.rest_base_url,
    )
    rl = AsyncRateLimiter(
        max_calls=cfg.kiwoom.rate_limit_calls,
        period=cfg.kiwoom.rate_limit_period,
    )
    rest = KiwoomRestClient(cfg.kiwoom, tm, rl)

    try:
        print("코스피 종목 조회 중 (ka10099, mrkt_tp=0) ...")
        kospi = await rest.get_stock_list_by_market("0")
        kospi_codes = {
            s.get("code") or s.get("stk_cd") or s.get("shcode") or ""
            for s in kospi
        }
        kospi_codes.discard("")
        print(f"  → {len(kospi_codes)}종목")

        print("코스닥 종목 조회 중 (ka10099, mrkt_tp=10) ...")
        kosdaq = await rest.get_stock_list_by_market("10")
        kosdaq_codes = {
            s.get("code") or s.get("stk_cd") or s.get("shcode") or ""
            for s in kosdaq
        }
        kosdaq_codes.discard("")
        print(f"  → {len(kosdaq_codes)}종목")

        if not kospi_codes or not kosdaq_codes:
            print("[!] 시장 종목 조회 결과가 비어있음. API 응답 키 확인 필요.")
            return 1

        uni_path = Path("config/universe.yaml")
        uni = yaml.safe_load(uni_path.read_text(encoding="utf-8"))

        updated = 0
        unknown = []
        for stock in uni["stocks"]:
            ticker = stock["ticker"]
            if ticker in kospi_codes:
                stock["market"] = "kospi"
                updated += 1
            elif ticker in kosdaq_codes:
                stock["market"] = "kosdaq"
                updated += 1
            else:
                stock["market"] = "unknown"
                unknown.append((ticker, stock.get("name", "")))

        # 주석이 제거되므로 경고 문구를 최상단에 삽입
        header = (
            "# ============================================================================\n"
            "# universe.yaml — 단타 유니버스 (market 필드 자동 추가됨)\n"
            "# ============================================================================\n"
            "# 주의: 이 파일은 scripts/update_universe_market.py에 의해 재생성될 때\n"
            "#       원본 주석이 유실됩니다. 원본 주석은 git 히스토리를 참조하세요.\n"
            "# ============================================================================\n\n"
        )
        body = yaml.safe_dump(uni, allow_unicode=True, sort_keys=False)
        uni_path.write_text(header + body, encoding="utf-8")

        print(f"\n[OK] 업데이트 완료: {updated}종목")
        if unknown:
            print(f"[!] 시장 불명 {len(unknown)}종목:")
            for tk, nm in unknown:
                print(f"    - {tk} {nm}")
        return 0
    finally:
        await rest.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
