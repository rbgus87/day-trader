"""gui/design_tokens.py — 공유 디자인 상수 (Catppuccin Mocha 기반 다크 테마)."""
from __future__ import annotations


class Colors:
    background = "#1e1e2e"        # base — 메인 윈도우 배경 (가장 어두움)
    surface = "#2a2a3d"           # 패널/카드 배경 (base보다 밝음)
    surface_hover = "#2f2f45"     # 테이블 짝수행 / 호버
    surface_elevated = "#45475a"  # surface1 — 선택
    surface_border = "#313244"    # 패널 테두리
    mantle = "#181825"            # 헤더/사이드바 배경
    crust = "#11111b"             # 가장 어두운 배경

    text_primary = "#cdd6f4"
    text_secondary = "#a6adc8"
    text_muted = "#7f849c"

    accent_green = "#a6e3a1"    # 수익, 상승, 활성
    accent_red = "#f38ba8"      # 손실, 하락, 오류
    accent_blue = "#89b4fa"     # 정보, 링크
    accent_yellow = "#f9e2af"   # 경고, 주의
    accent_mauve = "#cba6f7"    # 강조, 선택


class Typography:
    font_family = "Pretendard, Malgun Gothic, sans-serif"
    size_xs = 10
    size_sm = 11
    size_md = 13
    size_lg = 15
    size_xl = 20
    size_xxl = 28


class Spacing:
    gap_xs = 2
    gap_sm = 4
    gap_md = 8
    gap_lg = 12
    gap_xl = 16
    padding_card = 10
    padding_section = 16


class Border:
    radius_sm = 4
    radius_md = 6
    radius_lg = 10
    color = Colors.surface_border
    width = 1
