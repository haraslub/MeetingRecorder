import sys

import pytest

from audiorecorder.slides import windows_screen
from audiorecorder.slides.windows_screen import (
    CHROMIUM_CLASS,
    WindowInfo,
    meeting_title,
    meeting_window,
)

OWN_PID = 4242


def window(handle, title, pid=100, class_name=CHROMIUM_CLASS, **flags):
    values = dict(visible=True, minimized=False, cloaked=False)
    values.update(flags)
    return WindowInfo(handle=handle, title=title, process_id=pid, class_name=class_name,
                      **values)


MEET = "Meet - abc-defg-hij - Google Chrome"


def test_a_meet_window_behind_an_editor_is_found():
    windows = [window(1, "plan.md - Visual Studio Code", class_name="Code"), window(2, MEET)]
    assert meeting_window(windows, foreground=1, own_pid=OWN_PID).handle == 2


@pytest.mark.parametrize("title", [
    "Meet – abc-defg-hij - Google Chrome",
    "Meet: Weekly sync - Brave",
    "Weekly sync | Microsoft Teams - Microsoft​ Edge",
    "Call with Radek | Microsoft Teams - Google Chrome",
])
def test_meet_and_teams_web_titles_match(title):
    assert meeting_window([window(1, title)], foreground=0, own_pid=OWN_PID).handle == 1


@pytest.mark.parametrize("title", [
    "Inbox - Gmail - Google Chrome",
    "Google Meet invitation - Gmail - Google Chrome",
    "",
])
def test_other_browser_tabs_do_not_match(title):
    assert meeting_window([window(1, title)], foreground=1, own_pid=OWN_PID) is None


def test_the_foreground_meeting_wins_else_the_front_most():
    windows = [window(1, MEET), window(2, "Sync | Microsoft Teams - Microsoft Edge")]
    assert meeting_window(windows, foreground=2, own_pid=OWN_PID).handle == 2
    assert meeting_window(windows, foreground=99, own_pid=OWN_PID).handle == 1


@pytest.mark.parametrize("skipped", [
    dict(minimized=True), dict(cloaked=True), dict(visible=False), dict(pid=OWN_PID),
])
def test_unusable_meeting_windows_are_skipped(skipped):
    pid = skipped.pop("pid", 100)
    windows = [window(1, MEET, pid=pid, **skipped)]
    assert meeting_window(windows, foreground=1, own_pid=OWN_PID) is None


def test_a_non_browser_window_with_a_meeting_like_title_does_not_match():
    windows = [window(1, "Meet - notes.txt - Notepad", class_name="Notepad")]
    assert meeting_window(windows, foreground=1, own_pid=OWN_PID) is None


def test_create_slide_source_off_windows_names_the_reason(monkeypatch):
    monkeypatch.setattr(windows_screen.sys, "platform", "linux")
    source, reason = windows_screen.create_slide_source()
    assert source is None
    assert reason == windows_screen.UNAVAILABLE


@pytest.mark.skipif(sys.platform != "win32", reason="window rendering needs Windows")
def test_render_draws_a_real_window():
    import ctypes

    # The taskbar exists on every interactive Windows desktop; tests run Qt offscreen,
    # so no window of their own has a real handle.
    taskbar = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
    if not taskbar:
        pytest.skip("no interactive desktop")
    pixels = windows_screen._Win32().render(taskbar)
    assert pixels.shape[2] == 4
    assert pixels.shape[0] > 10 and pixels.shape[1] > 100


@pytest.mark.parametrize("flags", [dict(minimized=True), dict(cloaked=True), dict(visible=False)])
def test_a_minimized_or_hidden_meeting_still_has_its_title(flags):
    windows = [window(1, "Inbox - Gmail - Google Chrome"), window(2, MEET, **flags)]
    assert meeting_title(windows, OWN_PID) == MEET


def test_no_meeting_title_without_a_meeting_tab_or_in_the_own_process():
    assert meeting_title([window(1, "Inbox - Gmail - Google Chrome")], OWN_PID) == ""
    assert meeting_title([window(1, MEET, pid=OWN_PID)], OWN_PID) == ""
    assert meeting_title([window(1, MEET, class_name="Notepad")], OWN_PID) == ""
