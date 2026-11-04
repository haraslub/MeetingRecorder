"""Find the browser window that shows a Meet or Teams web meeting on Windows, and grab it.

Window titles of Chromium windows are read only to recognise the meeting tab, and stay on
this computer. The grab renders that one window, so other windows never appear in it.
"""

import os
import sys
from dataclasses import dataclass

import numpy as np

UNAVAILABLE = "Slides are available on Windows only for now"
# Chrome, Brave and Edge share this window class. A browser window's title is the title
# of its active tab, so a match also means the meeting tab is the one being drawn.
CHROMIUM_CLASS = "Chrome_WidgetWin_1"
MEETING_PREFIXES = ("Meet - ", "Meet – ", "Meet: ")
MEETING_MARKS = ("| Microsoft Teams",)


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    process_id: int
    class_name: str
    visible: bool
    minimized: bool
    cloaked: bool


def _is_meeting_title(title):
    return title.startswith(MEETING_PREFIXES) or any(mark in title for mark in MEETING_MARKS)


def meeting_window(windows, foreground, own_pid):
    """The browser window whose active tab is a meeting; windows are listed front to back."""
    matches = [
        window for window in windows
        if window.class_name == CHROMIUM_CLASS and window.visible and not window.minimized
        and not window.cloaked and window.process_id != own_pid
        and _is_meeting_title(window.title)
    ]
    for window in matches:
        if window.handle == foreground:
            return window
    return matches[0] if matches else None


def meeting_title(windows, own_pid):
    """The title of any browser window whose active tab is a meeting, or ''.

    Unlike meeting_window it also counts a minimized, hidden or cloaked window: the call
    still runs there, it only cannot be drawn.
    """
    for window in windows:
        if (window.class_name == CHROMIUM_CLASS and window.process_id != own_pid
                and _is_meeting_title(window.title)):
            return window.title
    return ""


class _Win32:
    """The user32, gdi32 and dwmapi calls the window walk and the grab need, bound once."""

    GW_HWNDNEXT = 2
    DWMWA_CLOAKED = 14
    PW_RENDERFULLCONTENT = 2

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32")
        self.dwmapi = ctypes.WinDLL("dwmapi")
        handle, dc, bitmap = wintypes.HWND, wintypes.HDC, wintypes.HBITMAP
        user32.GetTopWindow.argtypes = [handle]
        user32.GetTopWindow.restype = handle
        user32.GetWindow.argtypes = [handle, wintypes.UINT]
        user32.GetWindow.restype = handle
        user32.GetForegroundWindow.restype = handle
        user32.GetWindowRect.argtypes = [handle, ctypes.POINTER(wintypes.RECT)]
        user32.GetClassNameW.argtypes = [handle, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextLengthW.argtypes = [handle]
        user32.GetWindowTextW.argtypes = [handle, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [handle, ctypes.POINTER(wintypes.DWORD)]
        user32.IsWindowVisible.argtypes = [handle]
        user32.IsIconic.argtypes = [handle]
        user32.GetDC.argtypes = [handle]
        user32.GetDC.restype = dc
        user32.ReleaseDC.argtypes = [handle, dc]
        user32.PrintWindow.argtypes = [handle, dc, wintypes.UINT]
        gdi32.CreateCompatibleDC.argtypes = [dc]
        gdi32.CreateCompatibleDC.restype = dc
        gdi32.CreateCompatibleBitmap.argtypes = [dc, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = bitmap
        gdi32.SelectObject.argtypes = [dc, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.GetDIBits.argtypes = [
            dc, bitmap, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.UINT]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [dc]
        self.dwmapi.DwmGetWindowAttribute.argtypes = [
            handle, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        self.user32 = user32
        self.gdi32 = gdi32

    def windows(self):
        found = []
        current = self.user32.GetTopWindow(None)
        while current:
            found.append(self._describe(current))
            current = self.user32.GetWindow(current, self.GW_HWNDNEXT)
        return found

    def _describe(self, handle):
        ctypes, wintypes = self.ctypes, self.wintypes
        name = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(handle, name, 256)
        title = ""
        # Only browser windows are ever matched, so no other window's title is read.
        if name.value == CHROMIUM_CLASS:
            length = self.user32.GetWindowTextLengthW(handle)
            text = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(handle, text, length + 1)
            title = text.value
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        cloaked = wintypes.DWORD()
        if self.dwmapi.DwmGetWindowAttribute(
                handle, self.DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) != 0:
            cloaked.value = 0
        return WindowInfo(
            handle=handle,
            title=title,
            process_id=process_id.value,
            class_name=name.value,
            visible=bool(self.user32.IsWindowVisible(handle)),
            minimized=bool(self.user32.IsIconic(handle)),
            cloaked=bool(cloaked.value),
        )

    def foreground(self):
        return self.user32.GetForegroundWindow()

    def render(self, handle):
        """BGRA pixels of one window, drawn by the window itself even when it is covered."""
        ctypes, wintypes = self.ctypes, self.wintypes
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(handle, ctypes.byref(rect)):
            raise OSError("The meeting window is gone")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise OSError("The meeting window has no size")
        screen = self.user32.GetDC(None)
        memory = self.gdi32.CreateCompatibleDC(screen)
        bitmap = self.gdi32.CreateCompatibleBitmap(screen, width, height)
        previous = self.gdi32.SelectObject(memory, bitmap)
        try:
            if not self.user32.PrintWindow(handle, memory, self.PW_RENDERFULLCONTENT):
                raise OSError("Windows could not draw the meeting window")
            header = _bitmap_header(ctypes, wintypes, width, height)
            pixels = np.empty((height, width, 4), dtype=np.uint8)
            if not self.gdi32.GetDIBits(memory, bitmap, 0, height, pixels.ctypes.data,
                                        ctypes.byref(header), 0):
                raise OSError("Windows could not copy the meeting window")
            return pixels
        finally:
            self.gdi32.SelectObject(memory, previous)
            self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory)
            self.user32.ReleaseDC(None, screen)


def _bitmap_header(ctypes, wintypes, width, height):
    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    # A negative height asks for top-down rows, the order numpy and QImage expect.
    return BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32,
                            0, 0, 0, 0, 0, 0)


class WindowsScreenSource:
    def __init__(self):
        self.own_pid = os.getpid()
        self.win32 = _Win32()

    def find_target(self):
        """The handle of the meeting window, or None when no meeting window is open."""
        window = meeting_window(self.win32.windows(), self.win32.foreground(), self.own_pid)
        return None if window is None else window.handle

    def meeting_title(self):
        """The title of the meeting window, also when minimized, or ''."""
        return meeting_title(self.win32.windows(), self.own_pid)

    def grab(self, handle):
        """BGR pixels of the meeting window, height x width x 3."""
        return self.win32.render(handle)[..., :3]

    def close(self):
        pass


def create_slide_source():
    """The screen source for this platform and an empty reason, or None and the reason."""
    if sys.platform == "win32":
        return WindowsScreenSource(), ""
    return None, UNAVAILABLE
