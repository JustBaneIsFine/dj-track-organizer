"""Central app configuration: paths, constants, default settings.

Single source of truth for filesystem locations and the seed values written to
the `settings` table on first launch. Everything that needs the DB path or a
default imports it from here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "dj-organizer"
APP_VERSION = "0.3.2"

# owner/repo on GitHub; used for the update check and About links.
GITHUB_REPO = "JustBaneIsFine/dj-track-organizer"
CONTACT_EMAIL = "djtezej@gmail.com"
# Tutorial video; empty hides the About button until there is one.
TUTORIAL_URL = "https://youtu.be/kowmLRPeob0"
# Ko-fi page; empty hides the Support button.
KOFI_URL = "https://ko-fi.com/djtezej"
# Web3Forms access key for the feedback window; empty hides it. Public by design.
FEEDBACK_KEY = "4d4c65cf-bce8-4af9-8d44-84e655016855"

# Preferred port; main.py will fall through to PORT_FALLBACK_MAX if taken.
PORT = 7331
PORT_FALLBACK_MAX = 7341
HOST = "127.0.0.1"


def app_dir() -> Path:
    """User data directory: ~/.dj-organizer/  (created on first use).
    DJ_DATA_DIR points it elsewhere, for testing against a throwaway library."""
    d = Path(os.environ["DJ_DATA_DIR"]) if os.environ.get("DJ_DATA_DIR") else Path.home() / f".{APP_NAME}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return app_dir() / "dj_organizer.db"


def browser_profile_dir() -> Path:
    """App-managed persistent browser profile, so the SoundCloud login sticks."""
    d = app_dir() / "browser-profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


def migrations_dir() -> Path:
    return _base_dir() / "db" / "migrations"


def frontend_dir() -> Path:
    return _base_dir() / "frontend"


def _base_dir() -> Path:
    """Repo root in dev, or the PyInstaller bundle dir when frozen."""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


# Default settings seeded into the `settings` table on first launch.
# Keys mirror the spec. Values are stored as strings (settings table is TEXT).
DEFAULT_SETTINGS: dict[str, str] = {
    "source_url": "",
    "scrape_delay_min_ms": "3000",
    "scrape_delay_max_ms": "7000",
    "scroll_pause_ms": "1200",
    "scroll_step_px": "400",
    "headless_mode": "true",
    "browser_profile_path": "",
    "default_sort": "priority_new_first",
    "show_deleted": "false",
    "show_reposts": "true",
    "ui_density": "comfortable",
    "ui_scale": "100",          # UI zoom percent: 100 | 110 | 125 | 150
    "track_page_size": "100",   # max tracks rendered per view: 100 | 250 | 500 | 750 | 1000
    "scrape_stop_on_known": "true",  # stop scraping an artist once we hit known tracks
    "scrape_stop_known_count": "5",  # how many consecutive already-saved tracks before stopping
    "interactive_mode": "true",
    "priority_update_threshold": "2",
    "max_tracks_per_artist": "0",
    "repost_limit_default": "30",
    "include_reposts_default": "false",
    "onboarded": "false",
    "theme": "dark",            # dark | light
    "accent_color": "#5fd35f",  # any hex; drives --accent (and "new")
    "accent_listened": "",      # listened/originals color; empty = follow --accent
    "accent_revisit": "#e0b84a",  # revisit color (amber); drives --revisit
    "owned_match_threshold": "98",  # default folder-match strictness (%)
    "owned_match_floor": "85",      # lowest score a folder check keeps
    "owned_match_floor_artist": "85",  # same, for the per-artist check
    "owned_fuzzy_from": "90",       # fuzzy matches below this are hidden in the review
    "show_owned": "false",          # owned tracks hidden by default
    "compact_layout": "",           # JSON: which pieces compact shows (layout panel)
    "narrow_hide_sidebar": "true",  # hide the sidebar when the window is narrow
    "narrow_prompt_seen": "false",  # asked once, the first time it happens
    "open_mode": "native",          # how the app window opens: native | browser
    "update_check": "true",         # check GitHub for a newer release on launch
    "update_chime": "false",        # sound when an update finds new tracks for an artist
    "saved_runs_keep": "5",         # how many past run results stay in "Recent results"
    "accepted_disclaimer": "false", # set true once the user accepts the first-run notice
    "checklist": "",                # onboarding checklist: "" never offered, active, hidden
    "animation_level": "",          # 0 off .. 3 all; empty = never changed, which means All
}
