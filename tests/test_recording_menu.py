# The window fixtures come from test_rts_toggle; pytest passes them in by parameter name.
# ruff: noqa: F401, F811
from datetime import datetime

from PyQt6.QtGui import QGuiApplication
from test_rts_toggle import app, window

from audiorecorder.recordings.manager import SLIDES, Recording
from audiorecorder.ui.recording_list import RecordingItemWidget


def row(folder="Q:/out/2026-11-04 10-00-00 - Weekly sync", outputs=()):
    name = "2026-11-04 10-00-00 - Weekly sync"
    path = f"{folder}/{name}.m4a" if folder else f"Q:/out/{name}.m4a"
    return RecordingItemWidget(Recording(
        path=path, name=name, date=datetime(2026, 11, 4, 10), duration_seconds=60,
        is_transcribed=False, transcript_path="", outputs=outputs, folder=folder))


def actions(widget):
    widget._fill_menu()
    return {action.text(): action for action in widget._menu.actions() if action.text()}


def test_the_menu_opens_the_folder_and_copies_its_name(app):
    widget = row()
    opened, copied = [], []
    widget.open_folder_clicked.connect(opened.append)
    widget.copy_name_clicked.connect(copied.append)

    menu = actions(widget)
    assert list(menu)[:3] == ["Clean up with AI…", "Open folder", "Copy recording name"]
    menu["Open folder"].trigger()
    menu["Copy recording name"].trigger()

    assert opened == ["Q:/out/2026-11-04 10-00-00 - Weekly sync"]
    assert copied == [widget.recording.name]


def test_a_recording_without_a_folder_can_copy_its_display_name(app):
    widget = row(folder="")
    opened = []
    widget.open_folder_clicked.connect(opened.append)

    menu = actions(widget)
    menu["Open folder"].trigger()

    assert opened == ["Q:/out"]
    assert menu["Copy recording name"].isEnabled()


def test_the_slides_badge_opens_the_recording_folder(app):
    widget = row(outputs=(SLIDES,))
    opened = []
    widget.open_folder_clicked.connect(opened.append)

    (badge, _glyph), = widget._badges
    badge.click()

    assert opened == ["Q:/out/2026-11-04 10-00-00 - Weekly sync"]


def test_copy_recording_name_puts_the_display_name_on_the_clipboard(window):
    w = window(rts_translate=False)
    w._copy_recording_name("Q4: budget / plan")
    assert QGuiApplication.clipboard().text() == "Q4: budget / plan"
