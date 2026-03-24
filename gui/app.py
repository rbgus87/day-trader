"""GUI 애플리케이션 진입점."""

import sys

from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow


def run_gui():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    app.aboutToQuit.connect(window._cleanup_and_quit)

    window.show()
    sys.exit(app.exec())
