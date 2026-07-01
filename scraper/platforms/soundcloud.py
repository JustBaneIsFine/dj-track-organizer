"""SoundCloud platform adapter.

This is the ONLY file that knows about SoundCloud's DOM. If SoundCloud changes
their markup, edit the SELECTORS block below and (if needed) the small JS
extraction snippets. Nothing else in the app references SoundCloud internals.

Pages used:
  <artist>/tracks    -> originals only (finite)
  <artist>/reposts   -> reposts only  (dedicated page; capped by repost_limit)
  <user>/following   -> followed artists (for import)
  <user>/likes       -> liked tracks (import variant)
"""
from __future__ import annotations

import asyncio
import sys
from urllib.parse import urlsplit, urlunsplit

from scraper.anti_detection import (
    TimingConfig,
    human_scroll_to_bottom,
    post_nav_pause,
)
from scraper.platforms.base import Platform, ScrapedArtist, ScrapedTrack

# SELECTORS - the one place SoundCloud's DOM is described.
# Each entry is a list of candidates tried in order (newest markup first).
SELECTORS = {
    # Track rows on /tracks and /reposts share the same title anchor.
    "track_title": [
        "a.soundTitle__title",
        "li.soundList__item a.soundTitle__title",
        ".sound__body a.soundTitle__title",
    ],
    # Followed-user cards on /following.
    "user_link": [
        "a.userBadgeListItem__heading",
        "ul.userBadgeList__list a.userBadgeListItem__heading",
        ".userBadgeListItem a.userBadgeListItem__heading",
    ],
    # A repost on the /reposts page is marked with a reposted caption; we still
    # take everything on /reposts as a repost, this is only used as a hint.
    "repost_caption": [".soundContext__line", ".sound__header"],
    # Buy / free-download link inside a single track row, if present. Tried in order.
    # SC markup has shifted over time; keep several candidates. The href is usually the
    # real external destination (bandcamp / hypeddit / pumpyoursound), which we classify.
    "track_buy": [
        ".purchaseLink__container a",
        "a.sc-buylink",
        "a.soundActions__purchaseLink",
        ".soundActions a[href*='buy']",
        "a[href].sc-buylink",
    ],
}

BASE = "https://soundcloud.com"


# URL helpers
def normalize_artist_url(url: str) -> str:
    """Return the canonical artist base URL: https://soundcloud.com/<user>.

    Accepts full URLs, scheme-less domains ("soundcloud.com/x"), and bare
    usernames ("x").
    """
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        full = url
    elif url.startswith("soundcloud.com") or url.startswith("www.soundcloud.com"):
        full = "https://" + url
    else:  # bare username or path fragment
        full = BASE + "/" + url.lstrip("/")
    parts = urlsplit(full)
    segs = [s for s in parts.path.split("/") if s]
    user = segs[0] if segs else ""
    return f"{BASE}/{user}"


def _strip(url: str) -> str:
    """Drop query + fragment; reposts often carry ?in= params."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def _username(url: str) -> str:
    """First path segment of an artist/track URL (the owning user)."""
    segs = [s for s in urlsplit(url).path.split("/") if s]
    return segs[0].lower() if segs else ""


def _cmp_key(url: str) -> str:
    """Scheme/query-insensitive key for comparing a track URL to a saved one."""
    p = urlsplit(url or "")
    net = p.netloc.lower()
    if net.startswith("www."):
        net = net[4:]
    return (net + p.path.rstrip("/")).lower()


# JS that collects {name, url} for the first matching selector with results.
# preferText: take textContent over the title attribute. Followed-artist cards
# set title="Visit <name>'s profile" (an accessibility tooltip, NOT the display
# name); the link text is the real name -- so user lists pass preferText. clean()
# also unwraps that "Visit ... profile" pattern defensively for any selector.
_EXTRACT_JS = """
([selectors, preferText]) => {
  const clean = (s) => {
    s = (s || '').trim();
    const m = s.match(/^Visit\\s+(.+?)(?:['\\u2019]s)?\\s+profile$/i);
    return m ? m[1].trim() : s;
  };
  for (const sel of selectors) {
    const els = Array.from(document.querySelectorAll(sel));
    if (els.length === 0) continue;
    const out = [];
    const seen = new Set();
    for (const el of els) {
      const href = el.href || el.getAttribute('href');
      const title = el.getAttribute('title');
      const text = el.textContent;
      const name = clean(preferText ? (text || title) : (title || text));
      if (!href || !name) continue;
      if (seen.has(href)) continue;
      seen.add(href);
      out.push({ name, url: href });
    }
    if (out.length) return out;
  }
  return [];
}
"""


# Per-track {name, url, purchase_url}: like _EXTRACT_JS but it also looks inside each
# track row for a buy / free-download link, so the title and its purchase link stay
# paired. Scopes the buy search to the row containing the title anchor.
_TRACK_EXTRACT_JS = """
([titleSelectors, buySelectors]) => {
  const clean = (s) => {
    s = (s || '').trim();
    const m = s.match(/^Visit\\s+(.+?)(?:['\\u2019]s)?\\s+profile$/i);
    return m ? m[1].trim() : s;
  };
  for (const tsel of titleSelectors) {
    const titles = Array.from(document.querySelectorAll(tsel));
    if (titles.length === 0) continue;
    const out = [];
    const seen = new Set();
    for (const a of titles) {
      const href = a.href || a.getAttribute('href');
      const name = clean(a.getAttribute('title') || a.textContent);
      if (!href || !name) continue;
      if (seen.has(href)) continue;
      seen.add(href);
      const item = a.closest('li.soundList__item, .soundList__item, .sound, .soundBadge, .audibleTile') || a.parentElement;
      let purchase = null;
      if (item) {
        for (const bsel of buySelectors) {
          const b = item.querySelector(bsel);
          if (b) { const h = b.href || b.getAttribute('href'); if (h) { purchase = h; break; } }
        }
      }
      out.push({ name, url: href, purchase_url: purchase });
    }
    if (out.length) return out;
  }
  return [];
}
"""


class SoundCloud(Platform):
    name = "soundcloud"

    def matches(self, url: str) -> bool:
        return "soundcloud.com" in url

    async def _goto(self, page, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass  # networkidle can be flaky on infinite-scroll pages
        await post_nav_pause()

    async def _extract(self, page, selector_key: str, prefer_text: bool = False) -> list[dict]:
        return await page.evaluate(_EXTRACT_JS, [SELECTORS[selector_key], prefer_text])

    async def _extract_tracks(self, page) -> list[dict]:
        """Per-track {name, url, purchase_url}: pairs each title with the buy link in
        the same row (if any). Used for the final /tracks + /reposts collection."""
        return await page.evaluate(
            _TRACK_EXTRACT_JS, [SELECTORS["track_title"], SELECTORS["track_buy"]]
        )

    async def _scroll_and_collect(
        self, page, selector_key: str, *, limit: int = 0, on_progress=None,
        known: Optional[set] = None, stop_after: int = 0, prefer_text: bool = False,
        with_purchase: bool = False,
    ) -> list[dict]:
        async def count_fn() -> int:
            items = await self._extract(page, selector_key)
            return len(items)

        stop_check = None
        if known and stop_after and stop_after > 0:
            # /tracks and /reposts are newest-first, so new uploads load at the TOP and
            # the artist's already-known tracks form a contiguous block right after them.
            # Stop once we've seen `stop_after` consecutive known tracks anywhere in the
            # loaded list - we've then passed the new ones. (We scan for a run rather than
            # the tail, because /tracks later appends unrelated "Related tracks" by other
            # users that would otherwise keep the tail unknown forever.) Guarded so artists
            # with fewer than `stop_after` known tracks just scroll to the end.
            async def stop_check() -> bool:
                items = await self._extract(page, selector_key)
                if len(items) < stop_after:
                    return False
                run = 0
                for it in items:
                    url = it.get("url")
                    if url and _cmp_key(_strip(url)) in known:
                        run += 1
                        if run >= stop_after:
                            return True
                    else:
                        run = 0
                return False

        await human_scroll_to_bottom(
            page, self.cfg, count_fn, on_progress=on_progress, stop_check=stop_check
        )
        if with_purchase:
            items = await self._extract_tracks(page)
        else:
            items = await self._extract(page, selector_key, prefer_text=prefer_text)
        if limit and limit > 0:
            items = items[:limit]
        return items

    async def scrape_tracks(self, page, artist_url: str, *, on_progress=None,
                            known=None, stop_after: int = 0) -> list[ScrapedTrack]:
        base = normalize_artist_url(artist_url)
        owner = _username(base)
        await self._goto(page, f"{base}/tracks")
        items = await self._scroll_and_collect(
            page, "track_title", on_progress=on_progress, known=known,
            stop_after=stop_after, with_purchase=True,
        )
        # /tracks appends a "Related tracks" section on scroll; keep only the
        # artist's own uploads (URL owned by the same username).
        out = []
        for i in items:
            url = _strip(i["url"])
            if _username(url) != owner:
                continue
            out.append(ScrapedTrack(name=i["name"], url=url, is_repost=False,
                                    purchase_url=i.get("purchase_url")))
        return out

    async def scrape_reposts(
        self, page, artist_url: str, limit: int, *, on_progress=None,
        known=None, stop_after: int = 0
    ) -> list[ScrapedTrack]:
        base = normalize_artist_url(artist_url)
        await self._goto(page, f"{base}/reposts")
        cap = 0 if limit in (0,) else limit
        items = await self._scroll_and_collect(
            page, "track_title", limit=cap, on_progress=on_progress,
            known=known, stop_after=stop_after, with_purchase=True,
        )
        return [
            ScrapedTrack(name=i["name"], url=_strip(i["url"]), is_repost=True,
                         purchase_url=i.get("purchase_url"))
            for i in items
        ]

    async def scrape_artist_list(self, page, list_url: str, *, on_progress=None) -> list[ScrapedArtist]:
        await self._goto(page, list_url)
        items = await self._scroll_and_collect(
            page, "user_link", on_progress=on_progress, prefer_text=True
        )
        out = []
        for i in items:
            out.append(ScrapedArtist(name=i["name"], url=normalize_artist_url(i["url"])))
        return out


# CLI smoke test:  python -m scraper.platforms.soundcloud <artist_url> [--reposts]
async def _smoke(url: str, do_reposts: bool) -> None:
    import os

    from playwright.async_api import async_playwright

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # print ↻ on Windows consoles
    except Exception:
        pass
    headless = os.environ.get("SCRAPER_HEADLESS", "false").lower() == "true"
    cfg = TimingConfig()
    sc = SoundCloud(cfg)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        print(f"\n=== TRACKS for {url} ===")
        tracks = await sc.scrape_tracks(page, url)
        for t in tracks:
            print(f"  {t.name}  ->  {t.url}")
        print(f"  ({len(tracks)} tracks)")

        if do_reposts:
            print(f"\n=== REPOSTS for {url} (limit 30) ===")
            reposts = await sc.scrape_reposts(page, url, limit=30)
            for t in reposts:
                print(f"  ↻ {t.name}  ->  {t.url}")
            print(f"  ({len(reposts)} reposts)")

        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scraper.platforms.soundcloud <artist_url> [--reposts]")
        raise SystemExit(1)
    asyncio.run(_smoke(sys.argv[1], "--reposts" in sys.argv))
