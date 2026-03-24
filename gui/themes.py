"""GUI 테마 시스템 — Catppuccin Mocha 다크 테마.

QSS를 파일이 아닌 함수로 반환하여 PyInstaller 번들 시 경로 문제를 방지.
"""

COLORS = {
    # Base layers
    "base": "#1e1e2e",
    "mantle": "#181825",
    "crust": "#11111b",
    # Surfaces
    "surface0": "#313244",
    "surface1": "#45475a",
    "surface2": "#585b70",
    # Overlays
    "overlay0": "#6c7086",
    "overlay1": "#7f849c",
    # Text
    "text": "#cdd6f4",
    "subtext0": "#a6adc8",
    "subtext1": "#bac2de",
    # Accent colors
    "green": "#a6e3a1",
    "red": "#f38ba8",
    "yellow": "#f9e2af",
    "blue": "#89b4fa",
    "mauve": "#cba6f7",
    "peach": "#fab387",
    "teal": "#94e2d5",
    "sky": "#89dceb",
    "lavender": "#b4befe",
}


def dark_theme() -> str:
    """Catppuccin Mocha 다크 테마 QSS 문자열을 반환."""
    c = COLORS
    return f"""
/* ─── Base ─────────────────────────────────────────────────────────────── */
QMainWindow,
QWidget {{
    background-color: {c['base']};
    color: {c['text']};
    font-family: "Segoe UI", "맑은 고딕", sans-serif;
    font-size: 13px;
}}

/* ─── Sidebar ───────────────────────────────────────────────────────────── */
QFrame#sidebar {{
    background-color: {c['mantle']};
    border-right: 1px solid {c['surface0']};
}}

/* ─── Tabs ──────────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {c['surface0']};
    background-color: {c['base']};
}}

QTabBar::tab {{
    background-color: {c['mantle']};
    color: {c['subtext0']};
    padding: 6px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}}

QTabBar::tab:selected {{
    color: {c['mauve']};
    border-bottom: 2px solid {c['mauve']};
    background-color: {c['base']};
}}

QTabBar::tab:hover:!selected {{
    color: {c['text']};
    background-color: {c['surface0']};
}}

/* ─── Buttons ───────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {c['surface0']};
    color: {c['text']};
    border: 1px solid {c['surface1']};
    border-radius: 4px;
    padding: 5px 12px;
}}

QPushButton:hover {{
    background-color: {c['surface1']};
    border-color: {c['overlay0']};
}}

QPushButton:pressed {{
    background-color: {c['surface2']};
}}

QPushButton:disabled {{
    color: {c['overlay0']};
    border-color: {c['surface0']};
}}

QPushButton#startBtn {{
    background-color: {c['green']};
    color: {c['base']};
    border-color: {c['green']};
    font-weight: bold;
}}

QPushButton#startBtn:hover {{
    background-color: #b9f0b4;
    border-color: #b9f0b4;
}}

QPushButton#startBtn:pressed {{
    background-color: #93d08f;
}}

QPushButton#startBtn:disabled {{
    background-color: {c['surface1']};
    color: {c['overlay0']};
    border-color: {c['surface1']};
}}

QPushButton#stopBtn {{
    background-color: {c['red']};
    color: {c['base']};
    border-color: {c['red']};
    font-weight: bold;
}}

QPushButton#stopBtn:hover {{
    background-color: #f59db5;
    border-color: #f59db5;
}}

QPushButton#stopBtn:pressed {{
    background-color: #e07891;
}}

QPushButton#stopBtn:disabled {{
    background-color: {c['surface1']};
    color: {c['overlay0']};
    border-color: {c['surface1']};
}}

QPushButton#haltBtn {{
    background-color: {c['peach']};
    color: {c['base']};
    border-color: {c['peach']};
    font-weight: bold;
}}

QPushButton#haltBtn:hover {{
    background-color: #fbbe97;
    border-color: #fbbe97;
}}

QPushButton#haltBtn:pressed {{
    background-color: #e89f74;
}}

QPushButton#haltBtn:disabled {{
    background-color: {c['surface1']};
    color: {c['overlay0']};
    border-color: {c['surface1']};
}}

/* ─── Tables ────────────────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {c['surface0']};
    color: {c['text']};
    gridline-color: {c['surface1']};
    border: 1px solid {c['surface0']};
    alternate-background-color: {c['mantle']};
    selection-background-color: {c['surface2']};
    selection-color: {c['text']};
}}

QTableWidget::item {{
    padding: 4px 8px;
    border: none;
}}

QTableWidget::item:selected {{
    background-color: {c['surface2']};
}}

QHeaderView {{
    background-color: {c['surface0']};
}}

QHeaderView::section {{
    background-color: {c['surface0']};
    color: {c['subtext1']};
    padding: 5px 8px;
    border: none;
    border-right: 1px solid {c['surface1']};
    border-bottom: 1px solid {c['surface1']};
    font-weight: bold;
}}

QHeaderView::section:last {{
    border-right: none;
}}

/* ─── ComboBox ──────────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {c['surface0']};
    color: {c['text']};
    border: 1px solid {c['surface1']};
    border-radius: 4px;
    padding: 4px 8px;
    min-width: 80px;
}}

QComboBox:hover {{
    border-color: {c['overlay0']};
}}

QComboBox:focus {{
    border-color: {c['mauve']};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {c['subtext0']};
    width: 0;
    height: 0;
}}

QComboBox QAbstractItemView {{
    background-color: {c['surface0']};
    color: {c['text']};
    border: 1px solid {c['surface1']};
    selection-background-color: {c['surface2']};
    selection-color: {c['text']};
    outline: none;
}}

/* ─── Inputs ────────────────────────────────────────────────────────────── */
QLineEdit,
QSpinBox,
QDoubleSpinBox,
QDateEdit {{
    background-color: {c['surface0']};
    color: {c['text']};
    border: 1px solid {c['surface1']};
    border-radius: 4px;
    padding: 4px 8px;
}}

QLineEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QDateEdit:focus {{
    border-color: {c['mauve']};
}}

QLineEdit:hover,
QSpinBox:hover,
QDoubleSpinBox:hover,
QDateEdit:hover {{
    border-color: {c['overlay0']};
}}

QSpinBox::up-button,
QSpinBox::down-button,
QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button,
QDateEdit::up-button,
QDateEdit::down-button {{
    background-color: {c['surface1']};
    border: none;
    width: 16px;
}}

QSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover,
QDateEdit::up-button:hover,
QDateEdit::down-button:hover {{
    background-color: {c['surface2']};
}}

/* ─── ScrollBar ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background-color: {c['base']};
    width: 8px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {c['surface1']};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {c['surface2']};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}

QScrollBar:horizontal {{
    background-color: {c['base']};
    height: 8px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {c['surface1']};
    border-radius: 4px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {c['surface2']};
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

/* ─── StatusBar ─────────────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {c['mantle']};
    color: {c['subtext0']};
    border-top: 1px solid {c['surface0']};
}}

QStatusBar::item {{
    border: none;
}}

/* ─── ProgressBar ───────────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {c['surface0']};
    color: {c['text']};
    border: 1px solid {c['surface1']};
    border-radius: 4px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {c['mauve']};
    border-radius: 3px;
}}

/* ─── PlainTextEdit ─────────────────────────────────────────────────────── */
QPlainTextEdit {{
    background-color: {c['mantle']};
    color: {c['text']};
    border: 1px solid {c['surface0']};
    border-radius: 4px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
}}

QPlainTextEdit:focus {{
    border-color: {c['surface1']};
}}

/* ─── CheckBox ──────────────────────────────────────────────────────────── */
QCheckBox {{
    color: {c['text']};
    spacing: 6px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c['surface2']};
    border-radius: 3px;
    background-color: {c['surface0']};
}}

QCheckBox::indicator:hover {{
    border-color: {c['overlay0']};
}}

QCheckBox::indicator:checked {{
    background-color: {c['mauve']};
    border-color: {c['mauve']};
}}

QCheckBox::indicator:checked:hover {{
    background-color: {c['lavender']};
    border-color: {c['lavender']};
}}

/* ─── GroupBox ──────────────────────────────────────────────────────────── */
QGroupBox {{
    color: {c['subtext1']};
    border: 1px solid {c['surface1']};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: bold;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: {c['mauve']};
    background-color: {c['base']};
}}

/* ─── Splitter ──────────────────────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {c['surface0']};
    height: 2px;
}}

QSplitter::handle:horizontal {{
    width: 2px;
    height: auto;
}}

QSplitter::handle:vertical {{
    width: auto;
    height: 2px;
}}

QSplitter::handle:hover {{
    background-color: {c['surface1']};
}}
"""
