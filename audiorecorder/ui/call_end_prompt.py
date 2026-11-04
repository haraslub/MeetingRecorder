"""Non-modal prompt offered when the meeting of a running recording has ended."""

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from audiorecorder.ui import theme
from audiorecorder.ui.meeting_prompt import record_button
from audiorecorder.ui.widgets import IconLabel


class CallEndPrompt(QDialog):
    """Accepted means stop and save; rejected means keep recording."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Call ended?")
        if sys.platform == "darwin":
            # A parentless macOS Tool can overlay another application's full-screen Space.
            self.setWindowFlag(Qt.WindowType.Tool, True)
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(False)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(12)
        tile = QFrame()
        tile.setFixedSize(36, 36)
        tile.setStyleSheet(f"background-color: {theme.token('rec.soft')}; border-radius: 8px;")
        tile_layout = QHBoxLayout(tile)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        tile_layout.addWidget(IconLabel("phone-off", "rec", 18), 0, Qt.AlignmentFlag.AlignCenter)
        top.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        title = QLabel("The call seems to have ended")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        text.addWidget(title)
        message = QLabel(
            "The Meet or Teams window of this recording is gone. Stop and save the recording?")
        message.setWordWrap(True)
        message.setStyleSheet(f"color: {theme.token('text.secondary')};")
        text.addWidget(message)
        top.addLayout(text, 1)
        layout.addLayout(top)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        keep = QPushButton("Keep recording")
        stop = record_button("Stop and save", "square")
        keep.setAutoDefault(False)
        stop.setDefault(True)
        keep.clicked.connect(self.reject)
        stop.clicked.connect(self.accept)
        buttons.addWidget(keep)
        buttons.addWidget(stop)
        layout.addLayout(buttons)
