"""트레일링 스톱 그리드 서치 — config.yaml 자동 수정 + 백테스트 반복."""
import subprocess
import sys
from pathlib import Path

GRID_VALUES = [0.010, 0.015, 0.020, 0.025, 0.030]
START_DATE = "2025-05-07"
END_DATE = "2026-04-10"
CONFIG_PATH = Path("config.yaml")
RESULT_DIR = Path("backtest_results")


def update_config(trailing_value: float) -> None:
    """config.yaml의 momentum trailing_stop_pct만 업데이트."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_momentum = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("momentum:"):
            in_momentum = True
            continue
        if in_momentum and stripped and not line.startswith(" ") and not line.startswith("\t"):
            in_momentum = False
        if in_momentum and "trailing_stop_pct" in line:
            # 주석 보존
            comment_idx = line.find("#")
            indent = len(line) - len(line.lstrip())
            new_line = " " * indent + f"trailing_stop_pct: {trailing_value}"
            if comment_idx > 0:
                new_line += "  " + line[comment_idx:]
            lines[i] = new_line
            break

    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def parse_result(output: str) -> dict:
    """compare_strategies 출력에서 Momentum 순위 행 추출."""
    result = {"trades": 0, "pf": 0.0, "pnl": 0, "pf_above_1": 0}

    for line in output.split("\n"):
        if "Momentum" in line and "|" in line and "Single" not in line:
            parts = [p.strip() for p in line.split("|")]
            # parts: ['', 'Momentum', '616', '1.05', '+32,193', '28', '']
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
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return proc.stdout + proc.stderr


def main():
    RESULT_DIR.mkdir(exist_ok=True)
    results = []

    # 원래 값 백업
    original_text = CONFIG_PATH.read_text(encoding="utf-8")

    print("=" * 70)
    print("Trailing Stop Grid Search")
    print(f"Period: {START_DATE} ~ {END_DATE}")
    print(f"Values: {[f'-{v*100:.1f}%' for v in GRID_VALUES]}")
    print("=" * 70)

    try:
        for i, trail in enumerate(GRID_VALUES, 1):
            print(f"\n[{i}/{len(GRID_VALUES)}] trailing -{trail*100:.1f}% ...")

            update_config(trail)
            output = run_backtest()

            # 결과 파일 저장
            result_file = RESULT_DIR / f"trailing_{trail*100:.1f}.txt"
            result_file.write_text(output, encoding="utf-8")

            # 파싱
            r = parse_result(output)
            r["trailing"] = trail
            results.append(r)

            print(f"  trades={r['trades']}, PF={r['pf']:.2f}, PnL={r['pnl']:+,}, PF>1={r['pf_above_1']}")

    finally:
        # 원래 config 복원
        CONFIG_PATH.write_text(original_text, encoding="utf-8")
        print("\n(config.yaml restored)")

    # 최종 표 출력
    print("\n" + "=" * 70)
    print("Trailing Stop Grid Search Results")
    print("=" * 70)
    print(f"{'Trailing':<10} {'Trades':>6} {'PF':>6} {'Total PnL':>12} {'PF>1.0':>10}")
    print("-" * 50)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r["pf"] < 100 else "INF"
        print(f"-{r['trailing']*100:>4.1f}%    {r['trades']:>6} {pf_str:>6} {r['pnl']:>+12,} {r['pf_above_1']:>10}")

    # 최적값
    best = max(results, key=lambda x: x["pnl"])
    print()
    print(f"Best: trailing -{best['trailing']*100:.1f}% (PF {best['pf']:.2f}, PnL {best['pnl']:+,})")


if __name__ == "__main__":
    main()
