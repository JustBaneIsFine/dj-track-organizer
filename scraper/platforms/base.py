"""Abstract platform interface.

Every platform (SoundCloud today, others later) implements this. The engine only
talks to this interface, so adding a platform = adding one file under
``scraper/platforms/`` that subclasses ``Platform``. No DOM knowledge leaks out
of the platform module.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional

from scraper.anti_detection import TimingConfig


@dataclass
class ScrapedTrack:
    name: str
    url: Optional[str]
    is_repost: bool = False
    purchase_url: Optional[str] = None  # buy / free-download link found in the track row, if any


@dataclass
class ScrapedArtist:
    name: str
    url: str


@dataclass
class ArtistResult:
    tracks: list[ScrapedTrack] = field(default_factory=list)
    reposts: list[ScrapedTrack] = field(default_factory=list)


class Platform(abc.ABC):
    name: str = "base"

    def __init__(self, cfg: TimingConfig):
        self.cfg = cfg

    @abc.abstractmethod
    def matches(self, url: str) -> bool:
        """True if this platform handles the given URL."""

    @abc.abstractmethod
    async def scrape_artist_list(self, page, list_url: str, *, on_progress=None) -> list[ScrapedArtist]:
        """Extract artist name+profile-url from a following/likes/list page.

        ``on_progress(count)`` (optional, async) reports the running count while
        scrolling - useful for long follow lists."""

    @abc.abstractmethod
    async def scrape_tracks(self, page, artist_url: str, *, on_progress=None,
                            known=None, stop_after: int = 0) -> list[ScrapedTrack]:
        """Extract original tracks from the artist's /tracks page.

        ``on_progress(count)`` (optional, async) is called with the running count
        while scrolling so callers can report live progress. ``known`` (a set of
        comparison keys) + ``stop_after`` enable early-stop once that many
        consecutive already-saved tracks are seen."""

    @abc.abstractmethod
    async def scrape_reposts(
        self, page, artist_url: str, limit: int, *, on_progress=None,
        known=None, stop_after: int = 0
    ) -> list[ScrapedTrack]:
        """Extract reposts from the artist's /reposts page (capped at limit)."""
