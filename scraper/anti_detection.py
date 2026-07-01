"""Human-like browser behaviour and timing.

All knobs that affect how "careful" we are when scraping live here or are read
from settings. The philosophy: behave like a slow, slightly unpredictable human
visitor. Speed is never a goal; not getting blocked is.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

# A small pool of realistic, recent desktop Chrome user agents. Rotate per run.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

VIEWPORT = {"width": 1280, "height": 900}


@dataclass
class TimingConfig:
    """Timing knobs, normally hydrated from the settings table."""

    delay_min_ms: int = 3000
    delay_max_ms: int = 7000
    scroll_pause_ms: int = 1200
    scroll_step_px: int = 400

    @classmethod
    def from_settings(cls, s: dict) -> "TimingConfig":
        def i(key: str, default: int) -> int:
            try:
                return int(s.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            delay_min_ms=i("scrape_delay_min_ms", 3000),
            delay_max_ms=i("scrape_delay_max_ms", 7000),
            scroll_pause_ms=i("scroll_pause_ms", 1200),
            scroll_step_px=i("scroll_step_px", 400),
        )


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


async def _sleep_ms(ms: float) -> None:
    await asyncio.sleep(ms / 1000.0)


def _jitter(value: int, frac: float = 0.2) -> float:
    """Return value +/- frac (default 20%)."""
    delta = value * frac
    return value + random.uniform(-delta, delta)


async def inter_artist_delay(cfg: TimingConfig) -> None:
    """Random pause between processing two artists (the big anti-bot lever)."""
    await _sleep_ms(random.uniform(cfg.delay_min_ms, cfg.delay_max_ms))


async def reading_pause() -> None:
    """Extra short 'reading' pause after finishing an artist."""
    await _sleep_ms(random.uniform(1000, 3000))


async def post_nav_pause() -> None:
    """After navigation + network idle, wait a touch before interacting."""
    await _sleep_ms(random.uniform(800, 1500))


async def move_mouse_randomly(page) -> None:
    """Nudge the mouse to a random spot before scrolling."""
    try:
        x = random.randint(50, VIEWPORT["width"] - 50)
        y = random.randint(50, VIEWPORT["height"] - 50)
        await page.mouse.move(x, y, steps=random.randint(3, 8))
    except Exception:
        pass  # mouse moves are best-effort; never fail a scrape over them


async def human_scroll_to_bottom(page, cfg: TimingConfig, count_fn, *, on_progress=None, stop_check=None, max_steps: int = 400):
    """Scroll down in jittered steps until item count stops growing.

    ``count_fn`` is an async callable returning the current number of collected
    items. We stop after two consecutive scrolls that add nothing, or at
    ``max_steps`` as a hard safety cap. ``on_progress(count)`` (optional, async)
    is reported whenever the running count grows, for live UI progress.
    ``stop_check`` (optional, async → bool) lets the caller end scrolling early -
    e.g. once the freshly-loaded tracks are all ones we already have.
    """
    async def _should_stop() -> bool:
        if stop_check is None:
            return False
        try:
            return bool(await stop_check())
        except Exception:
            return False  # early-stop is an optimization; never fail a scrape over it
    async def _report(n: int) -> None:
        if on_progress is not None:
            try:
                await on_progress(n)
            except Exception:
                pass  # progress is best-effort; never fail a scrape over it

    stagnant = 0
    last_count = await count_fn()
    await _report(last_count)
    if await _should_stop():
        return last_count  # already deep into known territory on first paint
    for _ in range(max_steps):
        await move_mouse_randomly(page)
        # A human-ish wheel nudge...
        await page.mouse.wheel(0, int(_jitter(cfg.scroll_step_px)))
        # ...then make sure we actually reach the very bottom so SoundCloud's
        # lazy loader fires the next batch (a short wheel step can stop just
        # above the trigger point and falsely look "done" at a batch boundary).
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await _sleep_ms(_jitter(cfg.scroll_pause_ms))

        current = await count_fn()
        if current <= last_count:
            stagnant += 1
            if stagnant >= 3:
                # Confirmation pass: give the network a real chance to deliver a
                # pending batch before concluding the list is exhausted.
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                await _sleep_ms(1500)
                final = await count_fn()
                if final <= last_count:
                    break  # genuinely done
                last_count = final
                await _report(final)
                stagnant = 0
                continue
        else:
            stagnant = 0
        if current > last_count:
            await _report(current)
        last_count = current
        if await _should_stop():
            break  # newest batch is all tracks we already have - no need to go deeper
    return last_count
