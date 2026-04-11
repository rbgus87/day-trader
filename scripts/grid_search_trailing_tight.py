"""트레일링 스톱 타이트 그리드 서치 — 0.5~1.2% 범위 세밀 탐색."""
import subprocess
import sys
from pathlib import Path

GRID_VALUES = [0.005, 0.007, 0.010, 0.015, 0.020]
START_DATE = "2025-05-07"
END_DATE = "2026-04-10"
CONFIG_PATH = Path("config.yaml")
RESULT_DIR = Path("backtest_results")


def update_config(trailing_value: float) -> None:
    """공통 + momentum trailing_stop_pct 모두 업데이트."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    lines = text.split("\n")

    in_momentum = False
    common_updated = False
    momentum_updated = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 섹션 추적
        if stripped.startswith("momentum:"):
            in_momentum = True
            continue
        if in_momentum and stripped and not line.startswith(" ") and not line.startswith("\t"):
            in_momentum = False

        if "trailing_stop_pct" not in line or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        new_line = " " * indent + f"trailing_stop_pct: {trailing_value}"

        # 공통 trailing_stop_pct (momentum 밖)
        if not in_momentum and not common_updated:
            lines[i] = new_line
            common_updated = True
        # momentum trailing_stop_pct
        elif in_momentum and not momentum_updated:
            lines[i] = new_line
            momentum_updated = True

    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"  config: common={common_updated}, momentum={momentum_updated}")


def parse_result(output: str) -> dict:
    """compare_strategies 출력에서 Momentum 순위 행 추출."""
    result = {"trades": 0, "pf": 0.0, "pnl": 0, "pf_above_1": 0}

    for line in output.split("\n"):
        if "Momentum" in line and "|" in line and "Single" not in line:
            parts = [p.strip() for p in line.split("|")]
            for part in parts:
                tokens = part.split()
                if len(tokens) >= 1 and tokens[0] == "Momentum":
                    continue
                if len(tokens) >= 1:
                    try:
                        val = int(tokens[0].replace(",", "").replace("+", ""))
                        if result["trades"] == 0:
                            result["trades"] = val
                        elif result["pnl"] == 0 and ('+' in tokens[0] or '-' in tokens[0] or val > 100):
                            result["pnl"] = int(tokens[0].replace(",", "").replace("+", ""))
                            if tokens[0].startswith("-"):
                                result["pnl"] = -abs(result["pnl"])
                        else:
                            result["pf_above_1"] = val
                    except (ValueError, IndexError):
                        try:
                            result["pf"] = float(tokens[0])
                        except ValueError:
                            pass
            break

    return result


def run_backtest() -> str:
    """compare_strategies 실행 + 출력 캡처."""
    cmd = [
        sys.executable, "-m", "backtest.compare_strategies",
        "--strategy", "momentum",
        "--start", START_DATE,
        "--end", END_DATE,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (proc.stdout or "") + (proc.stderr or "")


def main():
    RESULT_DIR.mkdir(exist_ok=True)
    results = []

    original_text = CONFIG_PATH.read_text(encoding="utf-8")

    print("=" * 70)
    print("Trailing Stop Tight Grid Search")
    print(f"Period: {START_DATE} ~ {END_DATE}")
    print(f"Values: {[f'-{v*100:.1f}%' for v in GRID_VALUES]}")
    print("=" * 70)

    try:
        for i, trail in enumerate(GRID_VALUES, 1):
            print(f"\n[{i}/{len(GRID_VALUES)}] trailing -{trail*100:.1f}% ...")

            update_config(trail)
            # 적용 확인
            verify = subprocess.run(
                [sys.executable, "-c",
                 "from config.settings import AppConfig; "
                 "c = AppConfig.from_yaml().trading; "
                 "print(f'common={c.trailing_stop_pct}, momentum={c.momentum_trailing_stop_pct}')"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            print(f"  verify: {(verify.stdout or '').strip()}")
            output = run_backtest()

            result_file = RESULT_DIR / f"trailing_tight_{trail*100:.1f}.txt"
            result_file.write_text(output, encoding="utf-8")

            r = parse_result(output)
            r["trailing"] = trail
            results.append(r)

            print(f"  trades={r['trades']}, PF={r['pf']:.2f}, PnL={r['pnl']:+,}, PF>1={r['pf_above_1']}")

    finally:
        CONFIG_PATH.write_text(original_text, encoding="utf-8")
        print("\n(config.yaml restored)")

    print("\n" + "=" * 70)
    print("Trailing Stop Tight Grid Search Results")
    print("=" * 70)
    print(f"{'Trailing':<10} {'Trades':>6} {'PF':>6} {'Total PnL':>12} {'PF>1.0':>10}")
    print("-" * 50)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r["pf"] < 100 else "INF"
        print(f"-{r['trailing']*100:>4.1f}%    {r['trades']:>6} {pf_str:>6} {r['pnl']:>+12,} {r['pf_above_1']:>10}")

    best = max(results, key=lambda x: x["pnl"])
    print()
    print(f"Best: trailing -{best['trailing']*100:.1f}% (PF {best['pf']:.2f}, PnL {best['pnl']:+,})")


if __name__ == "__main__":
    main()
