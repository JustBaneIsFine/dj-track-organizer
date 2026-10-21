"""Filename → library-track fuzzy matching for the "tracks I already own" feature.

READ-ONLY by design: this module only ever receives plain strings (file names and
"Artist - Title" read from tags). It never opens, writes, moves, or deletes any file.
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

# "(Original Mix)", "[Extended Mix]", " - Original Mix": the same song, so dropped.
_NEUTRAL_RE = re.compile(
    r"[\(\[]\s*(?:original|extended)(?:\s+(?:mix|version))?\s*[\)\]]"
    r"|\s[-–—]\s*(?:original|extended)\s+(?:mix|version)\b",
    re.I,
)

# Words that make a bracket or " - " part a version ("Bicep Remix", "VIP", "Radio Edit").
_VERSION_MARKERS = {"remix", "rmx", "rework", "edit", "bootleg", "flip", "vip", "dub", "refix"}
_VERSION_GENERIC = _VERSION_MARKERS | {"mix", "version", "re", "the", "s"}
_BRACKET_RE = re.compile(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]")


def normalize(name: str, keep_mix: bool = False) -> str:
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
    if not keep_mix:
        s = _NEUTRAL_RE.sub(" ", s)   # "(original mix)" etc.
    s = re.sub(r"[^\w\s]", " ", s)    # punctuation -> space (\w keeps letters/digits)
    s = s.replace("_", " ")           # underscores too (\w includes them)
    return re.sub(r"\s+", " ", s).strip()


def version_of(raw: str):
    """The remixer/edit words of a title or file name, or None when it names no
    version. An empty set means a version with no name ("(Remix)")."""
    s = _EXT_RE.sub("", unicodedata.normalize("NFKD", raw or "")).lower()
    s = _NEUTRAL_RE.sub(" ", s)
    # Bracketed parts count with a marker anywhere; a bare " - Bicep Remix" part only
    # when it ends in one, so a title like "Dub Something" stays a title.
    parts = [(p, False) for p in _BRACKET_RE.findall(s)]
    parts += [(p, True) for p in re.split(r"\s[-–—]\s", _BRACKET_RE.sub(" ", s))[1:]]
    found = None
    for p, bare in parts:
        words = re.sub(r"[^\w\s]", " ", p).replace("_", " ").split()
        if _VERSION_MARKERS.intersection(words[-1:] if bare else words):
            found = (found or set()) | (set(words) - _VERSION_GENERIC)
    return found


def versions_conflict(a: str, b: str) -> bool:
    """True when two names are different versions of a song: one is a remix/edit and
    the other isn't, or both name a remixer and share no word of it."""
    return _conflict(version_of(a), version_of(b))


def _conflict(va, vb) -> bool:
    if va is None or vb is None:
        return (va is None) != (vb is None)
    return bool(va and vb and not va & vb)


_VERSION_PENALTY = 25


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


def _best_for_track(t, entries, norm_index, sorted_index, floor, exclude, names):
    """Best (score, file index) for one track. ``entries`` are (normalized, file index,
    version) per matchable string. ``exclude`` is a set of filenames to skip (pairs the
    user rejected); when non-empty the lists are filtered first, so the common
    no-rejection case pays nothing. A hit that is a different version of the song
    (another remix, or remix vs original) loses _VERSION_PENALTY points."""
    if exclude:
        keep = [i for i, e in enumerate(entries) if names[e[1]] not in exclude]
        if not keep:
            return 0.0, None
        entries = [entries[i] for i in keep]
        norm_index = [norm_index[i] for i in keep]
        sorted_index = [sorted_index[i] for i in keep]

    tver = version_of(t.get("name") or "")
    best_score = 0.0
    best_idx = None

    def consider(score, i):
        nonlocal best_score, best_idx
        if _conflict(tver, entries[i][2]):
            score -= _VERSION_PENALTY
        if score >= floor and score > best_score:
            best_score, best_idx = score, entries[i][1]

    # 1) Strict match: title / "artist - title" / "alias - title" / dedup_key.
    for cand in _candidates(t):
        for _, score, i in process.extract(
            _token_sort(cand), sorted_index, scorer=fuzz.ratio, score_cutoff=floor, limit=5
        ):
            consider(score, i)

    # 2) Alias-anywhere: if a file CONTAINS the artist's alias (any position), strip
    #    the alias and subset-match the remaining title.
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
            for _, score, j in process.extract(
                title, cleaned, scorer=fuzz.token_set_ratio, score_cutoff=floor, limit=5
            ):
                consider(score, idxs[j])
    return best_score, best_idx


def _artist_in(nf: str, track: dict) -> bool:
    """True if the normalized filename names the track's artist (name, alias or
    uploader handle) as a whole word."""
    names = [track.get("artist_name") or "", *(track.get("artist_aliases") or [])]
    handle = uploader_handle(track.get("url"))
    if handle:
        names.append(handle)
    for n in names:
        a = normalize(n)
        if a and re.search(rf"\b{re.escape(a)}\b", nf):
            return True
    return False


def _exact_key(name: str) -> str:
    """Words of a name in sorted order. Only case, punctuation, the extension, a leading
    track number and free download tags are dropped; "(Original Mix)" stays."""
    return _token_sort(normalize(name, keep_mix=True))


def _exact_keys(track: dict) -> set[str]:
    """Exact keys of every "artist - title" form of a track (name, aliases, handle)."""
    title = track.get("name") or ""
    artists = [track.get("artist_name") or "", *(track.get("artist_aliases") or []),
               uploader_handle(track.get("url"))]
    forms = [title] + [f"{a} - {title}" for a in artists if a]
    return {k for f in forms if (k := _exact_key(f)) and _artist_in(normalize(f), track)}


def is_identical(filename: str, track: dict, tag: str = "") -> bool:
    """True only when the file name or its tags hold exactly the words of the artist and
    title: every word on both sides, nothing extra, in any order."""
    keys = _exact_keys(track)
    return any(s and _exact_key(s) in keys for s in (filename, tag))


def match_kind(score: int, filename: str, track: dict, exact_at: int = 98,
               tag: str = "") -> str:
    """'exact' (identical, see is_identical), 'title' (high score, not identical) or
    'fuzzy'. A one-word title with no artist is fuzzy whatever the score: "Intro" or
    "Home" matches too many files to trust."""
    if is_identical(filename, track, tag):
        return "exact"
    if score < exact_at:
        return "fuzzy"
    if not _artist_in(normalize(filename), track) and not (tag and _artist_in(normalize(tag), track)) \
            and len(normalize(track.get("name") or "").split()) <= 1:
        return "fuzzy"
    return "title"


def match_filenames(tracks: list[dict], filenames: list[str], floor: int = 90,
                    progress_cb=None, rejected: dict = None, exact_at: int = 98,
                    tags: list = None, paths: list = None) -> list[dict]:
    """Return tracks that fuzzily match some file in the folder, at score >= floor.

    For each track we take the best score across its candidate strings vs every
    (normalized) filename, keeping the single best file. Returns one row per matched
    track: {track_id, track_name, artist_name, url, filename, path, score, kind, tags}.

    ``filenames`` may repeat (same name in two folders). ``tags`` and ``paths`` are
    optional lists in the same order: "Artist - Title" read from each file's tags (a
    second string it can match on) and its full path.

    ``rejected`` (optional) maps track_id -> set of filenames the user said are NOT
    the same song; those exact (track, file) pairs are never returned, though the
    track can still match a different file.

    ``progress_cb(done, total)`` (optional) is called periodically with the number
    of tracks processed so the caller can show a progress bar. This is CPU-bound;
    callers should run it off the event loop (``asyncio.to_thread``).
    """
    tags = tags or [""] * len(filenames)
    paths = paths or [None] * len(filenames)
    entries = []
    for i, f in enumerate(filenames):
        if not is_audio(f):
            continue
        nf = normalize(f)
        if nf:
            entries.append((nf, i, version_of(f)))
        tf = normalize(tags[i])
        if tf and tf != nf:
            entries.append((tf, i, version_of(tags[i])))
    if not entries:
        return []

    by_name = {}
    for i, f in enumerate(filenames):
        if is_audio(f):
            for k in {_exact_key(f), _exact_key(tags[i])} - {""}:
                by_name.setdefault(k, []).append(i)

    norm_index = [e[0] for e in entries]
    # Pre-sort every file's tokens ONCE; comparing pre-sorted strings with plain
    # ratio is equivalent to token_sort_ratio but avoids re-sorting F files per query.
    sorted_index = [_token_sort(nf) for nf in norm_index]
    total = len(tracks)
    matches = []
    for ti, t in enumerate(tracks):
        exclude = rejected.get(t["id"]) if rejected else None
        # An identical file always wins, even if a fuzzy hit elsewhere also scores 100.
        i = next((j for k in _exact_keys(t) for j in by_name.get(k, ())
                  if not (exclude and filenames[j] in exclude)), None)
        if i is not None:
            best_score = 100
        else:
            best_score, i = _best_for_track(
                t, entries, norm_index, sorted_index, floor, exclude, filenames
            )
        if i is not None:
            kind = match_kind(round(best_score), filenames[i], t, exact_at, tags[i])
            # 100 is reserved for identical names; subset and reordered hits top out at 99.
            score = 100 if kind == "exact" else min(round(best_score), 99)
            matches.append({
                "track_id": t["id"],
                "track_name": t.get("name"),
                "artist_name": t.get("artist_name"),
                "url": t.get("url"),  # so the review modal can open the track
                "filename": filenames[i],
                "path": paths[i],
                "score": score,
                "kind": kind,
                "tags": tags[i] or None,
            })
        if progress_cb is not None and (ti & 63) == 0:  # ~every 64 tracks
            progress_cb(ti + 1, total)
    if progress_cb is not None:
        progress_cb(total, total)
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches
