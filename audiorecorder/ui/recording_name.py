"""Inline name editor. Only Enter commits; losing focus leaves the files unchanged."""

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QLineEdit, QStackedLayout, QVBoxLayout, QWidget

from audiorecorder.recordings.naming import recording_name
from audiorecorder.ui.widgets import ElidedLabel


class RecordingName(QWidget):
    rename_requested = pyqtSignal(str)

    def __init__(self, name):
        super().__init__()
        self._name = name
        self._editing = False
        self._submitting = False
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._stack = QStackedLayout()
        self.label = ElidedLabel(name)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setObjectName("name")
        self.label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.label.setAccessibleName(f"Rename recording: {name}")
        self.label.setAccessibleDescription("Click or press Enter to rename.")
        self.label.installEventFilter(self)
        self.editor = QLineEdit()
        self.editor.setAccessibleName("Recording name")
        self.editor.setToolTip("Enter to save · Escape or click elsewhere to cancel")
        self.editor.installEventFilter(self)
        self.editor.returnPressed.connect(self._submit)
        self._stack.addWidget(self.label)
        self._stack.addWidget(self.editor)
        layout.addLayout(self._stack)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setObjectName("renameError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.set_busy(False)

    def eventFilter(self, watched, event):
        if watched is self.label:
            clicked = (event.type() == QEvent.Type.MouseButtonRelease
                       and event.button() == Qt.MouseButton.LeftButton)
            pressed = (event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space, Qt.Key.Key_F2))
            if clicked or pressed:
                self.begin()
                return True
        elif watched is self.editor:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.cancel()
                self.label.setFocus()
                return True
            if (event.type() == QEvent.Type.FocusOut and not self._submitting
                    and event.reason() != Qt.FocusReason.ActiveWindowFocusReason):
                self.cancel()
        return super().eventFilter(watched, event)

    def begin(self):
        if self._busy or self._editing:
            return
        self._editing = True
        self._submitting = False
        self.error.hide()
        self.error.clear()
        self.editor.setAccessibleDescription("Enter to save; Escape to cancel.")
        self.editor.setText(self._name)
        self._stack.setCurrentWidget(self.editor)
        self.editor.setFocus()
        self.editor.selectAll()

    def set_name(self, name):
        """Update the displayed name without discarding an edit already in progress."""
        self._name = name
        self.label.setText(name)
        self.label.setAccessibleName(f"Rename recording: {name}")

    def cancel(self):
        self._editing = False
        self._submitting = False
        self._stack.setCurrentWidget(self.label)
        self.error.hide()

    def _submit(self):
        if not self._editing or self._submitting or self._busy:
            return
        try:
            name = recording_name(self.editor.text())
        except ValueError as error:
            self.show_error(str(error))
            return
        if name == self._name:
            self.cancel()
            self.label.setFocus()
            return
        # The receiver may rebuild this row synchronously. Do not touch it after emit.
        self._submitting = True
        self.rename_requested.emit(name)

    def show_error(self, message):
        self._submitting = False
        self.error.setText(message)
        self.error.show()
        self.editor.setAccessibleDescription(message)
        self.editor.setFocus()

    def set_busy(self, busy):
        self._busy = busy
        if busy:
            self.cancel()
        self.label.setEnabled(not busy)
        self.label.setCursor(Qt.CursorShape.ArrowCursor if busy
                             else Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Wait for processing to finish before renaming." if busy
                        else "Click the name to rename · Enter to save · Escape to cancel")
