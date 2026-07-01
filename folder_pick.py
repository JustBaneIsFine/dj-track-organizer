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
    names: list[str] = []
    for _dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if match.is_audio(fn):
                names.append(fn)
    return names


def ask_directory() -> Optional[str]:
    """Show a native 'Select Folder' dialog; return the path or None if cancelled.

    Blocking (GUI) - call from a thread/executor. Creates and tears down its own
    hidden Tk root so it doesn't interfere with anything else.
    """
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
