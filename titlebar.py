"""Dark native title bar on Windows (DWM). No-op elsewhere."""
from __future__ import annotations

import sys

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 before 20H1


def _hwnd(window) -> int:
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is not None:
        try:
            return int(handle.ToInt64())
        except Exception:
            pass
    import ctypes
    return ctypes.windll.user32.FindWindowW(None, window.title) or 0


def set_dark(window, dark: bool) -> bool:
    if sys.platform != "win32" or window is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = _hwnd(window)
        if not hwnd:
            return False
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        ok = False
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                ok = True
                break
        # The frame keeps its old colour until it repaints.
        SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP)
        return ok
    except Exception as e:
        print(f"[titlebar] {e}")
        return False


class JsApi:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self) -> None:
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def set_dark_titlebar(self, dark: bool) -> bool:
        return set_dark(self._window, bool(dark))
