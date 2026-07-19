"""Server-side folder picking + name listing (fallback for non-Chromium browsers).

READ-ONLY: opens a native folder dialog and lists file NAMES via os.walk. It never
opens, reads, writes, moves, or deletes any of the user's files.
"""
from __future__ import annotations

import os
from typing import Optional

import match


def walk_audio_names(root: str) -> list[str]:
    """Recursively list audio file *basenames* under ``root``. Read-only."""
    return walk_audio_paths(root)[0]


def walk_audio_paths(root: str) -> tuple[list[str], dict[str, str]]:
    """Like walk_audio_names, but also return {basename: abspath} so a match can be
    opened in the user's default player. First path wins on duplicate basenames.
    Still read-only: nothing is opened or read here."""
    names: list[str] = []
    paths: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if match.is_audio(fn):
                names.append(fn)
                paths.setdefault(fn, os.path.join(dirpath, fn))
    return names, paths


# Set by main.py when running in the native pywebview window. Its folder dialog is
# marshalled to the GUI thread by pywebview, which is reliable; tkinter in a worker
# thread is not (it can deadlock on Windows), so prefer this when available.
_WEBVIEW_WINDOW = None


def set_webview_window(window) -> None:
    global _WEBVIEW_WINDOW
    _WEBVIEW_WINDOW = window


def ask_directory() -> Optional[str]:
    """Show a native 'Select Folder' dialog; return the path or None if cancelled.

    Blocking (GUI) - call from a thread/executor. Uses the pywebview window's dialog
    in native mode, and falls back to a hidden Tk root otherwise (browser mode).
    """
    win = _WEBVIEW_WINDOW
    if win is not None:
        import webview

        result = win.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        path = filedialog.askdirectory(title="Choose your music folder")
    finally:
        root.destroy()
    return path or None
