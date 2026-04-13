"""Card — 둥근 모서리 컨테이너 베이스 위젯.

KPI 카드, 차트 카드, 테이블 카드 등 대시보드 전반의 카드 스타일을
한 곳에서 관리. 추상화 범위는 "배경 + 라운딩 + 패딩 + 선택적 타이틀"까지.
"""

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Card(QFrame):
    """Catppuccin Mocha surface0 배경의 둥근 컨테이너.

    기존 KPI 카드 스타일을 그대로 재사용.
        background-color: #313244
        border-radius:    6px
        padding:          10, 8, 10, 8 (layout contentsMargins)

    Args:
        title: 있으면 카드 상단에 12px bold 라벨. None 이면 라벨 없음.
    """

    # 스타일 상수 (외부에서 재사용 가능)
    BG_COLOR = "#313244"
    BORDER_RADIUS = 6
    PADDING = (10, 8, 10, 8)  # left, top, right, bottom
    TITLE_STYLE = "font-size: 12px; font-weight: bold; color: #cdd6f4;"

    def __init__(self, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {self.BG_COLOR}; "
            f"border-radius: {self.BORDER_RADIUS}px; }}"
        )

        self._vbox = QVBoxLayout(self)
        self._vbox.setContentsMargins(*self.PADDING)
        self._vbox.setSpacing(4)

        self.title_label: QLabel | None = None
        if title is not None:
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet(self.TITLE_STYLE)
            self._vbox.addWidget(self.title_label)

    def addWidget(self, widget: QWidget, stretch: int = 0) -> None:
        """카드 콘텐츠 영역에 위젯 추가."""
        self._vbox.addWidget(widget, stretch)

    def addLayout(self, layout) -> None:
        """카드 콘텐츠 영역에 레이아웃 추가."""
        self._vbox.addLayout(layout)

    def setTitle(self, text: str) -> None:
        """타이틀 라벨 텍스트 갱신 (타이틀 없이 생성된 카드는 no-op)."""
        if self.title_label is not None:
            self.title_label.setText(text)

    def content_layout(self) -> QVBoxLayout:
        """내부 QVBoxLayout 노출 (spacing/margin 조정용)."""
        return self._vbox
