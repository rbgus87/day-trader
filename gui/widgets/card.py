"""Card — 둥근 모서리 컨테이너 베이스 위젯."""

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Card(QFrame):
    """대시보드 패널 공통 카드 컨테이너.

    디자인:
        background : #313244  (surface0)
        border     : 1px solid #45475a  (surface1)
        radius     : 8px
        title      : 13px bold + 하단 구분선
    """

    BG_COLOR = "#2a2a3d"
    BORDER_COLOR = "#313244"
    BORDER_RADIUS = 8
    PADDING = (10, 8, 10, 8)

    TITLE_STYLE = (
        "font-size: 13px; font-weight: bold; color: #cdd6f4; "
        "background: transparent; border: none;"
    )

    def __init__(self, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # objectName 기반 셀렉터로 자식 QFrame에 스타일 누수 방지
        self.setObjectName("card")
        self.setStyleSheet(
            f"QFrame#card {{"
            f"  background-color: {self.BG_COLOR};"
            f"  border-radius: {self.BORDER_RADIUS}px;"
            f"  border: 1px solid {self.BORDER_COLOR};"
            f"}}"
            # 카드 내부 QFrame (구분선, 테이블 프레임 등) 은 border 없음
            f"QFrame#card QFrame {{"
            f"  border: none; border-radius: 0;"
            f"}}"
        )

        self._vbox = QVBoxLayout(self)
        self._vbox.setContentsMargins(*self.PADDING)
        self._vbox.setSpacing(4)

        self.title_label: QLabel | None = None
        if title is not None:
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet(self.TITLE_STYLE)
            self._vbox.addWidget(self.title_label)
            # 타이틀 하단 구분선
            sep = QFrame()
            sep.setObjectName("card_title_sep")
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFixedHeight(1)
            sep.setStyleSheet(
                "QFrame#card_title_sep { background: #45475a; border: none; }"
            )
            self._vbox.addWidget(sep)
            self._vbox.setSpacing(6)

    def addWidget(self, widget: QWidget, stretch: int = 0) -> None:
        self._vbox.addWidget(widget, stretch)

    def addLayout(self, layout) -> None:
        self._vbox.addLayout(layout)

    def setTitle(self, text: str) -> None:
        if self.title_label is not None:
            self.title_label.setText(text)

    def content_layout(self) -> QVBoxLayout:
        return self._vbox
