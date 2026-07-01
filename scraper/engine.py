"""Scraping orchestration.

The engine owns the Playwright browser lifecycle and drives platforms
sequentially, never concurrently. It handles inter-artist delays, network-error
retries with backoff, rate-limit (429) cool-downs, and CAPTCHA detection that
pauses for the user.

Progress is reported through an async on_event callback so both the SSE layer
and the CLI observe the same stream of events.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from scraper.anti_detection import (
    TimingConfig,
    inter_artist_delay,
    random_user_agent,
    reading_pause,
    VIEWPORT,
)
from scraper.platforms.base import Platform
from scraper.platforms.soundcloud import SoundCloud

EventCb = Callable[[dict], Awaitable[None]]

# Strong signals that SoundCloud has thrown up a block/verification wall.
# Kept narrow on purpose: only act on these, and only when a scrape returned
# nothing, so a normal successful page never trips a false alarm.
CAPTCHA_URL_HINTS = ("/challenge", "captcha", "recaptcha")
CAPTCHA_DOM_HINTS = ("iframe[src*='captcha']", "iframe[src*='recaptcha']", "#challenge")


@dataclass
class SessionControl:
    """Shared mutable control flags for pause/skip/abandon during a run."""

    paused: asyncio.Event = field(default_factory=asyncio.Event)
    skip_current: asyncio.Event = field(default_factory=asyncio.Event)
    abandoned: asyncio.Event = field(default_factory=asyncio.Event)
    captcha_wait: asyncio.Event = field(default_factory=asyncio.Event)
    # When stopped via "Stop & save", keep the session resumable (status=paused)
    # instead of marking it abandoned.
    keep_resumable: bool = False

    async def wait_if_paused(self) -> None:
        while self.paused.is_set() and not self.abandoned.is_set():
            await asyncio.sleep(0.3)


def get_platform(url: str, cfg: TimingConfig) -> Platform:
    """Pick a platform adapter for a URL. New platforms slot in here."""
    sc = SoundCloud(cfg)
    if sc.matches(url):
        return sc
    raise ValueError(f"No platform handles URL: {url}")


@dataclass
class ArtistScrapeResult:
    tracks_found: int = 0
    reposts_found: int = 0
    tracks_added: int = 0
    error: Optional[str] = None
    skipped: bool = False


class ScrapeEngine:
    def __init__(
        self,
        settings: dict,
        *,
        on_event: Optional[EventCb] = None,
        control: Optional[SessionControl] = None,
    ):
        self.settings = settings
        self.cfg = TimingConfig.from_settings(settings)
        self.on_event = on_event
        self.control = control or SessionControl()
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page = None

    async def _emit(self, event: dict) -> None:
        if self.on_event:
            await self.on_event(event)

    @property
    def _headless(self) -> bool:
        return str(self.settings.get("headless_mode", "false")).lower() == "true"

    @property
    def _profile_path(self) -> str:
        return (self.settings.get("browser_profile_path") or "").strip()

    async def __aenter__(self) -> "ScrapeEngine":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        ua = random_user_agent()
        # Always use a persistent profile so the SoundCloud login is remembered
        # across runs. Default: an app-managed profile dir; if the user set a
        # browser_profile_path (e.g. their real Chrome profile), use that instead.
        import config
        profile = self._profile_path or str(config.browser_profile_dir())
        self._ctx = await self._pw.chromium.launch_persistent_context(
            profile,
            headless=self._headless,
            viewport=VIEWPORT,
            user_agent=ua,
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

        self.page.on("response", self._on_response)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    def _on_response(self, response) -> None:
        if response.status == 429:
            # Flagged here; the per-artist loop checks and cools down.
            self.control.captcha_wait.clear()
            self._rate_limited = True  # type: ignore[attr-defined]

    async def _check_captcha(self) -> bool:
        url = (self.page.url or "").lower()
        if any(h in url for h in CAPTCHA_URL_HINTS):
            return True
        for sel in CAPTCHA_DOM_HINTS:
            try:
                if await self.page.query_selector(sel):
                    return True
            except Exception:
                pass
        return False

    async def _handle_rate_limit(self) -> None:
        cooldown = random.uniform(60, 120)
        await self._emit({"type": "rate_limited", "cooldown_s": round(cooldown)})
        await asyncio.sleep(cooldown)

    async def _handle_captcha(self) -> None:
        await self._emit({
            "type": "captcha",
            "message": "SoundCloud is asking you to sign in or verify. In the "
                       "browser window that's open, log into SoundCloud (or "
                       "complete the check), then click Resume. Your login is "
                       "saved for next time.",
        })
        # Block until the user clears the captcha wait (set by API resume).
        self.control.captcha_wait.clear()
        while not self.control.captcha_wait.is_set() and not self.control.abandoned.is_set():
            await asyncio.sleep(0.5)

    async def scrape_artist(
        self,
        artist: dict,
        *,
        upsert_track,
        include_reposts: bool,
        repost_limit: int,
        on_progress=None,
        known_urls=None,
        stop_after: int = 0,
    ) -> ArtistScrapeResult:
        """Scrape one artist. ``upsert_track`` is an async fn(name,url,is_repost,purchase_url)->bool.

        ``on_progress(phase, count)`` (optional, async) is called with a running
        count while scrolling, so the UI can show live progress within an artist.
        ``known_urls`` + ``stop_after`` enable early-stop: stop scrolling once that
        many consecutive already-saved tracks are seen (0/empty = scrape fully).
        """
        result = ArtistScrapeResult()
        platform = get_platform(artist["url"], self.cfg)

        def _phase_progress(phase: str):
            if on_progress is None:
                return None
            async def cb(count: int) -> None:
                await on_progress(phase, count)
            return cb

        try:
            tracks = await self._with_retries(
                lambda: platform.scrape_tracks(
                    self.page, artist["url"], on_progress=_phase_progress("tracks"),
                    known=known_urls, stop_after=stop_after,
                )
            )
            # Only suspect a block/login wall when we got nothing AND a real
            # block signal is present - a successful scrape never trips this.
            if not tracks and await self._check_captcha():
                await self._handle_captcha()
                tracks = await platform.scrape_tracks(self.page, artist["url"])

            result.tracks_found = len(tracks)
            for t in tracks:
                if await upsert_track(t.name, t.url, 0, t.purchase_url):
                    result.tracks_added += 1

            if include_reposts:
                await asyncio.sleep(random.uniform(1.0, 2.5))  # inter-page delay
                reposts = await self._with_retries(
                    lambda: platform.scrape_reposts(
                        self.page, artist["url"], repost_limit,
                        on_progress=_phase_progress("reposts"),
                        known=known_urls, stop_after=stop_after,
                    )
                )
                result.reposts_found = len(reposts)
                for t in reposts:
                    if await upsert_track(t.name, t.url, 1, t.purchase_url):
                        result.tracks_added += 1

            if known_urls and result.tracks_found == 0 and not result.error:
                result.error = (
                    "No tracks found for an artist that previously had some. "
                    "SoundCloud's page layout may have changed, or the request was blocked."
                )

        except Exception as e:  # noqa: BLE001 - surfaced, never swallowed
            result.error = str(e)

        return result

    async def _with_retries(self, coro_factory, *, attempts: int = 3):
        backoffs = [5, 15, 45]
        last_exc = None
        for i in range(attempts):
            try:
                if getattr(self, "_rate_limited", False):
                    self._rate_limited = False
                    await self._handle_rate_limit()
                return await coro_factory()
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if i < attempts - 1:
                    await self._emit({
                        "type": "retry",
                        "attempt": i + 1,
                        "wait_s": backoffs[i],
                        "error": str(e),
                    })
                    await asyncio.sleep(backoffs[i])
        raise last_exc  # type: ignore[misc]

    async def pace_between_artists(self) -> None:
        await reading_pause()
        await inter_artist_delay(self.cfg)
