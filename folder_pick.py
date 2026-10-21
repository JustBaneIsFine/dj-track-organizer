"""Server-side folder picking + name listing (fallback for non-Chromium browsers).

READ-ONLY: opens a native folder dialog, lists file NAMES via os.walk and reads the
artist/title tags. It never writes, moves, or deletes any of the user's files.
"""
from __future__ import annotations

import os
from typing import Optional

import match


def walk_audio_names(root: str) -> list[str]:
    """Recursively list audio file *basenames* under ``root``. Read-only."""
    return walk_audio_paths(root)[0]


def walk_audio_paths(root: str) -> tuple[list[str], list[str]]:
    """Like walk_audio_names, plus each file's full path in the same order, so a
    match can be opened in the user's default player. Names may repeat across
    folders. Nothing is opened or read here."""
    names: list[str] = []
    paths: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if match.is_audio(fn):
                names.append(fn)
                paths.append(os.path.join(dirpath, fn))
    return names, paths


_ARTIST_KEYS = ("artist", "TPE1", "Author", "\xa9ART")
_TITLE_KEYS = ("title", "TIT2", "Title", "\xa9nam")
_ALBUM_ARTIST_KEYS = ("albumartist", "TPE2", "WM/AlbumArtist", "aART")
_NOT_AN_ARTIST = {"various artists", "various", "va", "unknown artist", "unknown"}


def _first(tags, keys) -> str:
    for k in keys:
        try:
            v = tags.get(k)
        except Exception:  # noqa: BLE001 - some tag types reject unknown keys
            continue
        if v is None:
            continue
        v = getattr(v, "text", v)  # ID3 frame
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        s = str(v).strip()
        if s:
            return s
    return ""


def read_tags(path: str) -> tuple[str, str, str]:
    """(artist, title, album artist) from the file's tags, '' where missing.
    mutagen only reads the tag header, never the audio."""
    import mutagen

    found = ["", "", ""]
    for easy in (True, False):
        try:
            f = mutagen.File(path, easy=easy)
        except Exception:  # noqa: BLE001 - corrupt or unsupported file
            break
        tags = getattr(f, "tags", None)
        if not tags:
            continue
        for i, keys in enumerate((_ARTIST_KEYS, _TITLE_KEYS, _ALBUM_ARTIST_KEYS)):
            found[i] = found[i] or _first(tags, keys)
        if all(found):
            break
    return found[0], found[1], found[2]


def tag_label(name: str, artist: str, title: str, album_artist: str = "") -> str:
    """The string a file's tags add for matching. Uses what is there: "Artist - Title",
    the tag artist with the file name when the title tag is empty, or the title tag
    alone. '' when the tags add nothing the file name doesn't already say."""
    if not artist and album_artist.lower() not in _NOT_AN_ARTIST:
        artist = album_artist
    if artist and title:
        return f"{artist} - {title}"
    stem = os.path.splitext(name)[0]
    if artist:
        a = match.normalize(artist)
        return "" if f" {a} " in f" {match.normalize(stem)} " else f"{artist} - {stem}"
    return title


def read_tag_labels(names: list[str], paths: list[str], cache: dict,
                    progress_cb=None) -> tuple[list[str], list[tuple]]:
    """Tag label per file, in order. ``cache`` maps path -> (mtime_ns, size, artist,
    title, album_artist) from earlier scans; only new or changed files are opened,
    several at a time since the wait is disk, not CPU. Returns the labels and the
    freshly read rows to store. Read-only."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(paths)
    raw: list = [None] * total
    stamps: list = [None] * total
    todo = []
    for i, p in enumerate(paths):
        try:
            st = os.stat(p)
        except OSError:
            raw[i] = ("", "", "")
            continue
        stamps[i] = (st.st_mtime_ns, st.st_size)
        hit = cache.get(p)
        if hit and tuple(hit[:2]) == stamps[i]:
            raw[i] = tuple(hit[2:])
        else:
            todo.append(i)

    done = total - len(todo)
    if progress_cb is not None:
        progress_cb(done, total)
    fresh = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(read_tags, paths[i]): i for i in todo}
        for fut in as_completed(futs):
            i = futs[fut]
            raw[i] = fut.result()
            fresh.append((paths[i], *stamps[i], *raw[i]))
            done += 1
            if progress_cb is not None and (done & 31) == 0:
                progress_cb(done, total)
    return [tag_label(n, *r) for n, r in zip(names, raw)], fresh


# Set by main.py when running in the native pywebview window. Its folder dialog is
# marshalled to the GUI thread by pywebview, which is reliable; tkinter in a worker
# thread is not (it can deadlock on Windows), so prefer this when available.
_WEBVIEW_WINDOW = None


def set_webview_window(window) -> None:
    global _WEBVIEW_WINDOW
    _WEBVIEW_WINDOW = window


def has_native_window() -> bool:
    return _WEBVIEW_WINDOW is not None


def ask_save_path(filename: str) -> Optional[str]:
    """Native 'Save As' dialog for a JSON file; the path, or None if cancelled.
    Native window only: WebView2 drops browser downloads unless pywebview allows them."""
    import webview

    result = _WEBVIEW_WINDOW.create_file_dialog(
        webview.SAVE_DIALOG, save_filename=filename, file_types=("JSON files (*.json)",)
    )
    if not result:
        return None
    path = result[0] if isinstance(result, (list, tuple)) else result
    return path if path.lower().endswith(".json") else path + ".json"


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
