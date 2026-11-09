"""Names of recording folders: the start time and, when it is known, the meeting title."""

import re

MAX_TITLE = 60
_BROWSER = re.compile(r"\s+[-–]\s+(Google Chrome|Microsoft​?\s?Edge|Brave|Chromium)$")
_TEAMS = re.compile(r"\s*\|\s*Microsoft Teams\b.*$")
# Teams sections that are not a meeting: "Chat | Microsoft Teams", "(3) Activity | ...".
_TEAMS_SECTION = re.compile(
    r"^(activity|apps|calendar|calls|chat|copilot|files|onedrive|teams)$", re.I)
_UNREAD = re.compile(r"^\(\d+\)\s*")
_MEET = re.compile(r"^Meet\s*[-–:]\s*")
# A Meet tab shows the meeting code, not its subject; a code is no name.
_MEET_CODE = re.compile(r"^[a-z]{3,4}-[a-z]{4}-[a-z]{3,4}$")
_GENERIC = {
    "active microsoft teams call", "scheduled meeting", "google meet", "microsoft teams",
    "meet", "meeting", "call",
}
_NOT_IN_NAMES = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def display_name(value):
    """A single-line UI title; punctuation has no filesystem meaning."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter a recording name.")
    name = value.strip()
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("This name contains invalid text. Choose another name.") from None
    if len(name) > 200:
        raise ValueError("This name is too long. Use a shorter name.")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ValueError("Use a single-line name without control characters.")
    return name


def meeting_title(raw):
    """The meeting subject inside a window or tab title, cleaned for a folder name, or ''."""
    title = _BROWSER.sub("", (raw or "").strip())
    teams = _TEAMS.search(title) is not None
    title = _UNREAD.sub("", _TEAMS.sub("", title)).strip()
    if teams and _TEAMS_SECTION.match(title):
        return ""
    title = _MEET.sub("", title)
    if _MEET_CODE.match(title) or title.lower() in _GENERIC:
        return ""
    title = re.sub(r"\s+", " ", _NOT_IN_NAMES.sub(" ", title)).strip()
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE].rsplit(" ", 1)[0] or title[:MAX_TITLE]
    # Windows drops trailing dots and spaces from names, which would break the pairing
    # of the folder with the files inside it.
    return title.rstrip(". ")


def folder_name(started, title=""):
    stamp = started.strftime("%Y-%m-%d %H-%M-%S")
    return f"{stamp} - {title}" if title else stamp
