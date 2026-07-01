"""Filename → library-track fuzzy matching for the "tracks I already own" feature.

READ-ONLY by design: this module only ever receives plain strings (file *names*
the browser collected). It never opens, reads, writes, moves, or deletes any file.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".aac", ".ogg", ".wma", ".alac",
}

_EXT_RE = re.compile(r"\.[a-z0-9]{1,5}$", re.I)
_LEADING_NUM_RE = re.compile(r"^\s*\d{1,3}\s*[-_.)]?\s+")  # "01 ", "01. ", "01 - ", "01)"

# Promo tags SoundCloud titles carry that the downloaded file never has, e.g.
# "(free download)", "[FREE DL]", "| free download", "free d/l". Stripped before
# comparison so "Artist - Title (Free Download)" matches "Artist - Title.mp3".
# Narrow on purpose: requires "free" immediately followed by download/dl/d-l, so
# legit words ("Free", "Freedom", "(Remix)") are untouched.
_FREE_DL_RE = re.compile(
    r"[\(\[\{][^)\]\}]*free\s*(?:download|d[\s/]*l|dl)[^)\]\}]*[\)\]\}]"  # bracketed
    r"|[\|/\---]*\s*free\s*(?:download|d[\s/]*l|dl)\b",                   # bare / separator-led
    re.I,
)


def normalize(name: str) -> str:
    """Normalize a filename or track title for comparison.

    Strips a trailing audio extension and a leading track number, lowercases,
    unicode-normalizes, drops "(free download)"-style promo tags, and reduces all
    punctuation (``-_()[]`` etc.) to spaces so tokens compare cleanly regardless
    of separators/word order.
    """
    s = unicodedata.normalize("NFKD", name or "")
    s = _EXT_RE.sub("", s)            # drop ".mp3" etc.
    s = _LEADING_NUM_RE.sub("", s)    # drop a leading track number
    s = s.lower()
    s = _FREE_DL_RE.sub(" ", s)       # drop promo tags absent from the real file
    s = re.sub(r"[^\w\s]", " ", s)    # punctuation -> space (\w keeps letters/digits)
    s = s.replace("_", " ")           # underscores too (\w includes them)
    return re.sub(r"\s+", " ", s).strip()


def _token_sort(s: str) -> str:
    """Tokens sorted alphabetically, so plain ``ratio`` on two sorted strings equals
    ``token_sort_ratio`` on the originals - but the sort happens ONCE here instead of
    on every fuzzy comparison (the old hot path re-sorted every file per query)."""
    return " ".join(sorted(s.split()))


def _strip_alias(nf: str, alias: str) -> str:
    """Remove whole-word occurrences of ``alias`` from an already-normalized string."""
    cleaned = re.sub(rf"\b{re.escape(alias)}\b", " ", nf)
    return re.sub(r"\s+", " ", cleaned).strip()


def is_audio(filename: str) -> bool:
    m = _EXT_RE.search(filename or "")
    return bool(m) and m.group(0).lower() in AUDIO_EXTS


_URL_HANDLE_RE = re.compile(r"soundcloud\.com/([^/?#]+)", re.I)
# Path segments that aren't an uploader handle.
_NON_HANDLE = {"you", "stream", "discover", "search", "tags", "charts", "popular"}


def uploader_handle(url) -> str:
    """The uploading user's handle from a track URL (soundcloud.com/<handle>/<title>).

    For tracks on a *label's* page the real artist is the URL owner, not the
    library 'artist' (the label), so this recovers it for matching."""
    if not url:
        return ""
    m = _URL_HANDLE_RE.search(url)
    if not m:
        return ""
    h = m.group(1).lower()
    return "" if h in _NON_HANDLE else h


def _candidates(track: dict) -> list[str]:
    """Normalized strings a filename might equal for this track."""
    title = track.get("name") or ""
    artist = track.get("artist_name") or ""
    out = {normalize(title)}
    if artist:
        out.add(normalize(f"{artist} - {title}"))
    # Alternate artist names (aliases): files are often "Alias - Title".
    for alias in track.get("artist_aliases") or []:
        if alias:
            out.add(normalize(f"{alias} - {title}"))
    # The uploader handle from the track URL - the real artist for label tracks
    # (and a useful extra for reposts/normal artists).
    handle = uploader_handle(track.get("url"))
    if handle:
        out.add(normalize(f"{handle} - {title}"))
    if track.get("dedup_key"):
        out.add(track["dedup_key"])  # already normalized at insert time
    return [c for c in out if c]


def match_filenames(tracks: list[dict], filenames: list[str], floor: int = 90,
                    progress_cb=None) -> list[dict]:
    """Return tracks that fuzzily match some file in the folder, at score >= floor.

    For each track we take the best score across its candidate strings vs every
    (normalized) filename, keeping the single best filename. Returns one row per
    matched track: {track_id, track_name, artist_name, filename, score}.

    ``progress_cb(done, total)`` (optional) is called periodically with the number
    of tracks processed so the caller can show a progress bar. This is CPU-bound;
    callers should run it off the event loop (``asyncio.to_thread``).
    """
    norm_files = []
    for f in filenames:
        if not is_audio(f):
            continue
        nf = normalize(f)
        if nf:
            norm_files.append((nf, f))
    if not norm_files:
        return []

    norm_index = [nf for nf, _ in norm_files]
    # Pre-sort every file's tokens ONCE; comparing pre-sorted strings with plain
    # ratio is equivalent to token_sort_ratio but avoids re-sorting F files per query.
    sorted_index = [_token_sort(nf) for nf in norm_index]
    total = len(tracks)
    matches = []
    for ti, t in enumerate(tracks):
        best_score = 0.0
        best_file = None
        # 1) Strict match: title / "artist - title" / "alias - title" / dedup_key.
        #    Pre-sorted candidate vs pre-sorted files with ratio == token_sort_ratio.
        for cand in _candidates(t):
            hit = process.extractOne(
                _token_sort(cand), sorted_index, scorer=fuzz.ratio, score_cutoff=floor
            )
            if hit and hit[1] > best_score:
                best_score = hit[1]
                best_file = norm_files[hit[2]][1]  # original filename

        # 2) Alias-anywhere: if a file CONTAINS the artist's alias (any position),
        #    strip the alias and subset-match the remaining title. Gating on the
        #    alias being present keeps token_set_ratio's looseness in check.
        aliases = [normalize(a) for a in (t.get("artist_aliases") or []) if a]
        title = normalize(t.get("name") or "")
        if aliases and title:
            for alias in aliases:
                if not alias:
                    continue
                pat = re.compile(rf"\b{re.escape(alias)}\b")
                idxs = [i for i, nf in enumerate(norm_index) if pat.search(nf)]
                if not idxs:
                    continue
                cleaned = [_strip_alias(norm_index[i], alias) for i in idxs]
                hit = process.extractOne(
                    title, cleaned, scorer=fuzz.token_set_ratio, score_cutoff=floor
                )
                if hit and hit[1] > best_score:
                    best_score = hit[1]
                    best_file = norm_files[idxs[hit[2]]][1]

        if best_file is not None:
            matches.append({
                "track_id": t["id"],
                "track_name": t.get("name"),
                "artist_name": t.get("artist_name"),
                "url": t.get("url"),  # so the review modal can open the track
                "filename": best_file,
                "score": round(best_score),
            })
        if progress_cb is not None and (ti & 63) == 0:  # ~every 64 tracks
            progress_cb(ti + 1, total)
    if progress_cb is not None:
        progress_cb(total, total)
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches
