"""Filename → library-track matching for the "tracks I already own" feature.

READ-ONLY by design: this module only ever receives plain strings (file names and
"Artist - Title" read from tags). It never opens, writes, moves, or deletes any file.

Two stages. A fuzzy search finds a few candidate files per track, then ``judge``
reads both names as artist, title, version and noise and decides whether the file
is the same song: exact, title (likely) or fuzzy (possible), or a different song,
which is dropped.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

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
    r"|[\|/\-]*\s*free\s*(?:download|d[\s/]*l|dl)\b",                      # bare / separator-led
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
    """Normalize a filename or track title for the fuzzy search.

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
    on every fuzzy comparison."""
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
    for alias in track.get("artist_aliases") or []:
        if alias:
            out.add(normalize(f"{alias} - {title}"))
    handle = uploader_handle(track.get("url"))
    if handle:
        out.add(normalize(f"{handle} - {title}"))
    if track.get("dedup_key"):
        out.add(track["dedup_key"])  # already normalized at insert time
    return [c for c in out if c]


# Reading a name as parts.

# The owner's own marks at the start of a title tag: "(U) ", "(LD) ", "(2) ", "(L) ! ".
_CODE_RE = re.compile(r"^\s*(?:!\s*)?\((?:[a-z]{1,2}|\d)\)\s*(?:!\s*)?", re.I)
# " - " splits artist from title. A dash with space on one side counts too ("EL JEFE -Singapur").
_SEP_RE = re.compile(r"\s+[-–—|]+\s*|[-–—|]+\s+")
_PROMO_RE = re.compile(
    r"\bbuy\s*(?:=|for|4|->)?\s*(?:free\s*)?(?:download|dl)\b"
    r"|\bdl\s+for\s+\w+(?:\s+version)?"
    r"|\*[^*]{0,40}\*"
    r"|\bout\s+now\b",
)
_ANY_BRACKET_RE = re.compile(r"[\(\[\{]+([^\(\)\[\]\{\}]*)[\)\]\}]+")
# Track numbers: "04 Title", "1.Title", "7. Title", "108_Bm Title". A single digit
# before a space stays ("7 dana", "2 The Floor").
_LEAD_RE = re.compile(r"^\s*(?:\d{2,3}_[a-g][#b]?m?\s+|\d{1,3}[._)]\s*|\d{2,3}\s+)(?=\S)")
_PHRASES = [(re.compile(r"\buk\s+garage\b"), "ukg"), (re.compile(r"\bd\s*(?:&|n|and)\s*b\b"), "dnb"),
            (re.compile(r"\bdrum\s*(?:&|n|and)\s*bass\b"), "dnb")]

_NOISE = {
    "free", "download", "downloads", "dl", "buy", "master", "mastered", "final", "wav", "mp3",
    "flac", "clean", "explicit", "preview", "soundcloud", "filtered", "unfiltered", "support",
    "complete", "pitched", "filterd",
}
# Dropped too: "(Original Mix)" and "(Extended)" are the same song.
_SOFT = {"extended", "original", "mix", "version"}
_CONNECT = {"and", "x", "vs", "v", "feat", "ft", "featuring", "w", "with", "by", "prod", "presents"}
_NOISE_TOKEN_RE = re.compile(r"v\d{1,2}|\d{2,3}bpm|bpm\d*|\d{1,3}k|(?:19|20)\d\d|[a-z]{2,5}\d{3,}")
_MARKERS = _VERSION_MARKERS | {"mashup", "blend", "blendy", "treatment", "remake", "rerub", "rework"}
_CANON = {"rmx": "remix", "two": "2", "too": "2", "to": "2", "four": "4", "for": "4", "u": "you"}
# Words that dress up an uploader name: "FunkyJ Edits", "Afro Bros Remixes", "djkickflipofficial".
_GENERIC = {"edits", "edit", "remixes", "remix", "music", "official", "records", "recordings",
            "productions", "prod", "selects", "presents", "tv", "dnb", "the"}
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|，|&|\+|/|;|\bx\b|\bfeat\b\.?|\bft\b\.?|\bvs\b\.?|\band\b|\bwith\b)\s*")


def _fold(s: str) -> str:
    """Lowercase, compatibility letters to plain ones (math bold, fraktur, fullwidth),
    accents dropped, apostrophes removed so "Boy's" equals "Boys"."""
    s = unicodedata.normalize("NFKC", s or "")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"['’`´]", "", s.lower())
    for pat, rep in _PHRASES:
        s = pat.sub(rep, s)
    return s


def _tok(s: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", s).replace("_", " ").split()


def _ctok(s: str) -> list[str]:
    return [_CANON.get(t, t) for t in _tok(s)]


def _noise(t: str) -> bool:
    return t in _NOISE or t in _SOFT or bool(_NOISE_TOKEN_RE.fullmatch(t))


def _content(tokens) -> list[str]:
    return [_CANON.get(t, t) for t in tokens if not _noise(t) and t not in _CONNECT]


class _Side:
    """One name read as parts. ``segments`` are the " - " parts outside brackets,
    ``version`` the remix/edit brackets, ``extras`` any other bracket (optional on
    the other side), ``tokens`` every word in order, for finding artist names."""

    __slots__ = ("segments", "version", "extras", "tokens", "digits")

    def __init__(self, raw: str, file_side: bool):
        s = _fold(raw)
        if file_side:
            s = _EXT_RE.sub("", s)
        self.tokens = _tok(s)
        s = _FREE_DL_RE.sub(" ", _PROMO_RE.sub(" ", s))
        groups = []

        def take(m):
            groups.append(m.group(1))
            return " - "
        while True:
            s2 = _ANY_BRACKET_RE.sub(take, s)
            if s2 == s:
                break
            s = s2
        parts = [p for p in _SEP_RE.split(s) if _tok(p)]
        segs, digits = [], set()
        for k, p in enumerate(parts):
            toks = _tok(_LEAD_RE.sub("", p))
            if not toks:
                continue
            if file_side and k < len(parts) - 1 and all(t.isdigit() and len(t) <= 3 for t in toks):
                continue  # "Arillian - 01 - Paradise Riddim"
            digits |= {t for t in toks if t.isdigit() and not _noise(t)}
            segs.append(_content(toks))
        self.segments = [x for x in segs if x]
        self.version, self.extras = [], []
        for g in groups:
            toks = _tok(g)
            words = _content(toks)
            if set(toks) & _MARKERS:
                self.version += words
                digits |= {t for t in toks if t.isdigit() and not _noise(t)}
            elif words and not all(w.isdigit() and len(w) <= 3 for w in words):
                self.extras += words
        self.digits = digits


def _strip_generic(toks: list[str]) -> list[str]:
    out = [t for t in toks if t not in _GENERIC]
    if len(out) == 1 and not toks[0].isdigit():
        for g in _GENERIC:
            if out[0].endswith(g) and len(out[0]) - len(g) >= 4:
                out = [out[0][: -len(g)]]
                break
    return out


def _name_variants(raw: str, handle: bool = False) -> list[list[str]]:
    f = _fold(raw)
    out = []
    for s in {f, _ANY_BRACKET_RE.sub(" ", f)}:
        toks = _ctok(s)
        if handle:
            toks = [t for t in toks if not t.isdigit()]
        for v in (toks, _strip_generic(toks)):
            if v and len("".join(v)) >= 3 and v not in out:
                out.append(v)
    return out


def _track_names(track: dict) -> list[list[str]]:
    names = []
    for raw in [track.get("artist_name") or "", *(track.get("artist_aliases") or [])]:
        names += [v for v in _name_variants(raw) if v not in names]
    handle = uploader_handle(track.get("url"))
    if handle:
        names += [v for v in _name_variants(handle, handle=True) if v not in names]
    return names


def _artist_parts(raw: str) -> list[list[str]]:
    out = []
    for p in _ARTIST_SPLIT_RE.split(_fold(raw)):
        toks = _ctok(p)
        if toks and len("".join(toks)) >= 3:
            out.append(toks)
    return out


def _name_in(name: list[str], seq: list[str]) -> bool:
    """True if ``name`` appears in ``seq`` as whole words, or spaced differently
    ("ToloMarvelous" in "tolo marvelous", "dj kickflip" in "djkickflip")."""
    n = len(name)
    for i in range(len(seq) - n + 1):
        if seq[i:i + n] == name:
            return True
    c = "".join(name)
    if len(c) < 4:
        return False
    if n == 1 and len(c) >= 5 and any(_typo(c, w) for w in seq):
        return True  # MITCH / MITRCH
    for i in range(len(seq)):
        joined = ""
        for j in range(i, min(i + 3, len(seq))):
            joined += seq[j]
            if joined == c:
                return True
            if len(joined) >= len(c):
                break
    return False


def _remove_names(words: list[str], names: list[list[str]]) -> list[str]:
    out = list(words)
    for name in sorted(names, key=len, reverse=True):
        n = len(name)
        i = 0
        while i <= len(out) - n:
            # A one-letter slip still counts: "The Weeknd" tagged "The Weekend".
            if all(a == b or (len(b) >= 5 and _typo(a, b)) for a, b in zip(out[i:i + n], name)):
                del out[i:i + n]
            else:
                i += 1
        c = "".join(name)
        if len(c) >= 4:
            out = [w for w in out if w != c]
    return out


def _typo(a: str, b: str) -> bool:
    return min(len(a), len(b)) >= 4 and Levenshtein.distance(a, b) <= 1


def _unmatched(a: set, b: set) -> tuple[set, set]:
    """Words left on each side after pairing one-letter typos (JEREMIH / JEREMIAH)."""
    ra, rb = set(a - b), set(b - a)
    for x in sorted(ra):
        y = next((y for y in sorted(rb) if _typo(x, y)), None)
        if y is not None:
            ra.discard(x)
            rb.discard(y)
    return ra, rb


def split_tag(tag: str) -> tuple[str, str]:
    """(artist, title) from a tag label built by folder_pick.tag_label."""
    if not tag:
        return "", ""
    if _CODE_RE.match(tag):
        return "", _CODE_RE.sub("", tag)
    parts = re.split(r"\s[-–—]\s", tag, maxsplit=1)
    if len(parts) == 2:
        return parts[0], _CODE_RE.sub("", parts[1])
    return "", tag


_RANK = {"exact": 3, "title": 2, "fuzzy": 1}


class _Track:
    __slots__ = ("raw", "names", "side", "seq")

    def __init__(self, t: dict):
        self.raw = t.get("name") or ""
        self.names = _track_names(t)
        self.side = _Side(self.raw, False)
        self.seq = _ctok(_fold(" ".join([t.get("artist_name") or "", self.raw,
                                        *(t.get("artist_aliases") or [])])))


class _File:
    __slots__ = ("variants", "seq", "tag_artists", "seg_artists")

    def __init__(self, filename: str, tag: str = ""):
        ta, tt = split_tag(tag)
        self.variants = [(filename, _Side(filename, True))]
        if tt:
            self.variants.append((tt, _Side(tt, True)))
        self.seq = _ctok(_fold(_EXT_RE.sub("", filename) + " " + (tag or "")))
        self.tag_artists = _artist_parts(ta) if ta else []
        # "Shaggy - Lost": the first part of a name names an artist when there are two.
        self.seg_artists = [v.segments[0] for _, v in self.variants if len(v.segments) > 1]


def _judge(tr: _Track, fl: _File):
    """'exact', 'title', 'fuzzy', or None when the file is a different song."""
    t = tr.side
    # Strong: the file names the uploader. Weak: the file's artist is named inside the
    # track title ("Jean Dogan - Rihanna - Rude Boy"), which may be the uploader's edit
    # of that song, so it only makes Exact when a named remix or edit matches too.
    strong = any(_name_in(n, fl.seq) for n in tr.names)
    tag_in_track = any(_name_in(a, tr.seq) for a in fl.tag_artists)
    if not strong and not tag_in_track:
        named = fl.tag_artists + fl.seg_artists
        if named and not any(_name_in(a, tr.seq) for a in named):
            return None  # the file names an artist this track never mentions

    names = tr.names + fl.tag_artists
    segs = [s for s in (_remove_names(seg, names) for seg in t.segments) if s]
    version = _remove_names(t.version, names)
    weak = tag_in_track or len(segs) > 1
    confirmed = strong or (weak and any(w not in _MARKERS for w in version))
    core = [w for s in segs for w in s] + version
    if not core:
        core = [w for s in t.segments for w in s] + t.version
    file_words = set(fl.seq)
    for _, v in fl.variants:
        file_words |= {w for s in v.segments for w in s} | set(v.version) | set(v.extras)
    for d in t.digits:
        if d not in file_words:
            return None  # Show #157 vs Show #700
    title_words = {w for w in core if w not in _MARKERS} or set(core)
    left, _ = _unmatched(title_words, file_words)
    left = {w for w in left if not _name_in([w], fl.seq)}  # TEKTEKTEK / TEK TEK TEK
    if len(left) * 2 > len(title_words):
        return None  # the titles barely share a word

    best = None
    tset, text = set(core), set(core) | set(t.extras)
    by_artist = {w for s in segs[:-1] for w in s}  # "Justin Bieber x ISHA - YUKON"
    for raw, v in fl.variants:
        fcore = _remove_names([w for s in v.segments for w in s] + v.version, names)
        if not fcore:
            fcore = [w for s in v.segments for w in s] + v.version
        # A file-only "3" or "137" is a take number or a BPM: "MASTER 3", "REMIX) 137".
        fcore = [w for w in fcore if not (w.isdigit() and w not in tset
                                          and (int(w) <= 9 or 60 <= int(w) <= 200))]
        fset, fext = set(fcore), set(fcore) | set(v.extras)
        equal = (tset == fset or text == fext or tset == fext or text == fset
                 or "".join(core) == "".join(fcore))
        if not equal and strong and len(v.segments) > 1:
            # The file adds the original artist: "Lomiiel - Hay Lupita (5HOURS Remix)".
            rest = fset - set(v.segments[0])
            equal = tset == rest or text == rest
        if equal:
            if confirmed:
                kind = "exact"
            else:
                kind = "title" if weak or len(tset) >= 2 else "fuzzy"
        else:
            lt, lf = _unmatched(tset, fset)
            lt -= set(v.extras)
            lf -= set(t.extras)
            # Left on the track side may only be a collaborator, or up to two words of
            # the version when the file adds nothing ("Gilla X NIE Afro Remix").
            lt -= by_artist
            only_version = lt <= set(version) - _MARKERS and len(lt) <= 2 and not lf
            near = (not lt or only_version) and len(lf) <= min(3, len(tset))
            kind = "title" if near and (strong or weak) else "fuzzy"
        if kind != "fuzzy" and versions_conflict(tr.raw, raw):
            kind = "fuzzy"
        if best is None or _RANK[kind] > _RANK[best]:
            best = kind
    return best


def judge(track: dict, filename: str, tag: str = ""):
    """Verdict for one track and one file: 'exact', 'title', 'fuzzy', or None for a
    different song. ``tag`` is the file's "Artist - Title" tag label."""
    return _judge(_Track(track), _File(filename, tag))


def match_filenames(tracks: list[dict], filenames: list[str], floor: int = 90,
                    progress_cb=None, rejected: dict = None,
                    tags: list = None, paths: list = None) -> list[dict]:
    """Return tracks that match some file in the folder.

    A fuzzy search (score >= floor) collects up to 8 candidate files per track, and
    ``judge`` picks the best of them. Returns one row per matched track:
    {track_id, track_name, artist_name, url, filename, path, score, kind, tags}.

    ``filenames`` may repeat (same name in two folders). ``tags`` and ``paths`` are
    optional lists in the same order: "Artist - Title" read from each file's tags and
    its full path.

    ``rejected`` (optional) maps track_id -> set of filenames the user said are NOT
    the same song; those exact (track, file) pairs are never returned, though the
    track can still match a different file.

    ``progress_cb(done, total)`` (optional) is called periodically. This is CPU-bound;
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

    norm_index = [e[0] for e in entries]
    sorted_index = [_token_sort(nf) for nf in norm_index]
    # Only files sharing a word with the query are scored. Words in more than a tenth
    # of the files ("remix", "edit") don't narrow anything, so they are skipped.
    postings = defaultdict(list)
    for ei, s in enumerate(sorted_index):
        for w in set(s.split()):
            postings[w].append(ei)
    cap = max(500, len(entries) // 10)

    def pool(query):
        words = [w for w in set(query.split()) if w in postings]
        lists = [postings[w] for w in words if len(postings[w]) <= cap] or [postings[w] for w in words]
        return sorted(set().union(*lists)) if lists else []

    files: dict[int, _File] = {}
    total = len(tracks)
    matches = []
    for ti, t in enumerate(tracks):
        exclude = rejected.get(t["id"]) if rejected else None
        tver = version_of(t.get("name") or "")
        hits: dict[int, float] = {}

        def consider(score, ei):
            if exclude and filenames[entries[ei][1]] in exclude:
                return
            if _conflict(tver, entries[ei][2]):
                score -= _VERSION_PENALTY
            fi = entries[ei][1]
            if score >= floor and score > hits.get(fi, 0):
                hits[fi] = score

        for cand in _candidates(t):
            q = _token_sort(cand)
            ids = pool(q)
            if not ids:
                continue
            for _, score, j in process.extract(
                q, [sorted_index[i] for i in ids], scorer=fuzz.ratio, score_cutoff=floor, limit=8
            ):
                consider(score, ids[j])

        # An alias anywhere in a file name: strip it and subset-match the title.
        title = normalize(t.get("name") or "")
        for alias in (normalize(a) for a in (t.get("artist_aliases") or []) if a):
            if not alias or not title:
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

        if hits:
            tr = _Track(t)
            best = None
            for fi, score in sorted(hits.items(), key=lambda kv: -kv[1])[:8]:
                fl = files.get(fi)
                if fl is None:
                    fl = files[fi] = _File(filenames[fi], tags[fi])
                kind = _judge(tr, fl)
                if kind and (best is None or (_RANK[kind], score) > (_RANK[best[0]], best[1])):
                    best = (kind, score, fi)
            if best:
                kind, score, i = best
                matches.append({
                    "track_id": t["id"],
                    "track_name": t.get("name"),
                    "artist_name": t.get("artist_name"),
                    "url": t.get("url"),  # so the review modal can open the track
                    "filename": filenames[i],
                    "path": paths[i],
                    # 100 is reserved for exact; everything else tops out at 99.
                    "score": 100 if kind == "exact" else min(round(score), 99),
                    "kind": kind,
                    "tags": tags[i] or None,
                })
        if progress_cb is not None and (ti & 63) == 0:
            progress_cb(ti + 1, total)
    if progress_cb is not None:
        progress_cb(total, total)
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches
