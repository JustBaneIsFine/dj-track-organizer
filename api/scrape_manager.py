"""Background scrape-job manager + SSE event plumbing.

A "run" (import or update) is backed by:
  * an ``import_sessions`` row  -> persistent queue + status (resume across restarts)
  * a ``scrape_log`` row        -> historical record of the run
  * an in-memory ``Job``        -> live control flags + an asyncio event queue

The frontend starts a run (returns session_id), then opens an SSE stream on that
session_id to watch progress. Pause/resume/skip/abandon poke the in-memory Job.

Interactive mode uses ``Job.decision`` (an asyncio.Queue): when the engine
reaches an artist it emits ``await_decision`` and waits for the UI to push a
decision (add / skip / set priority). Batch mode auto-approves every artist.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import aiosqlite

from db import queries
from db.schema import connect
from scraper.anti_detection import TimingConfig
from scraper.engine import ScrapeEngine, SessionControl, get_platform
from scraper.platforms.soundcloud import normalize_artist_url

# Sentinel pushed onto a Job's event queue when the run is fully finished.
DONE = {"type": "__done__"}
# Sentinel pushed onto a Job's scrape queue when triage is finished - tells the
# background scrape worker there are no more artists coming.
WORKER_DONE = object()


@dataclass
class Job:
    session_id: int
    control: SessionControl = field(default_factory=SessionControl)
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    decision: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: Optional[asyncio.Task] = None
    interactive: bool = False
    # Interactive runs decouple triage (producer) from scraping (a single serial
    # background worker, consumer). These hold the shared queue + counters.
    scrape_q: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker_stop: asyncio.Event = field(default_factory=asyncio.Event)
    processed: int = 0       # artists finished (cumulative across resumes)
    skipped: int = 0         # artists skipped (cumulative)
    queued_count: int = 0    # artists enqueued for scraping but not yet done
    total_added: int = 0     # new tracks added this run
    errors: list = field(default_factory=list)

    async def emit(self, event: dict) -> None:
        await self.events.put(event)


class ScrapeManager:
    def __init__(self) -> None:
        self.jobs: dict[int, Job] = {}

    def get(self, session_id: int) -> Optional[Job]:
        return self.jobs.get(session_id)

    # Public entry points
    async def start_import(
        self,
        conn: aiosqlite.Connection,
        *,
        mode: str,
        source_url: Optional[str],
        artist_urls: Optional[list[str]],
        rescan: bool = True,
    ) -> int:
        """Begin an import. Either scrape ``source_url`` for a follow list, or
        use an explicit list of ``artist_urls``. When ``rescan`` is False and a
        ``source_url`` is given, reuse the last saved list for that source instead
        of re-scraping the follow page. Returns the session id."""
        queue: list[dict] = []
        if artist_urls:
            for u in artist_urls:
                u = u.strip()
                if not u:
                    continue
                url = normalize_artist_url(u)
                name = url.rstrip("/").split("/")[-1] or url
                queue.append(
                    {"url": url, "name": name, "status": "pending", "artist_id": None}
                )
        elif source_url and not rescan:
            # Reuse the previously discovered list - go straight into review.
            prev = await queries.latest_queue_for_source(conn, source_url)
            queue = [
                {"url": e.get("url"), "name": e.get("name"),
                 "status": "pending", "artist_id": None}
                for e in prev if e.get("url")
            ]
        session_id = await queries.create_session(
            conn, mode=mode, source_url=source_url, queue=queue
        )
        job = Job(session_id=session_id, interactive=(mode == "interactive"))
        self.jobs[session_id] = job
        # Only scrape the follow page when we don't already have a seeded queue.
        run_source = source_url if (rescan and not artist_urls) else None
        job.task = asyncio.create_task(
            self._run(session_id, run_mode="import", source_url=run_source, filter_known=True)
        )
        return session_id

    async def start_update(
        self,
        conn: aiosqlite.Connection,
        *,
        mode: str,
        artist_ids: Optional[list[int]],
        threshold: Optional[int],
        notes_query: Optional[str] = None,
    ) -> int:
        """Begin an update run. ``mode`` is full|priority|selected|single|notes."""
        if mode == "full":
            artists = await queries.list_artists(conn)
            chosen = [a for a in artists if a["is_active"]]
        elif mode == "priority":
            thr = threshold if threshold is not None else int(
                await queries.get_setting(conn, "priority_update_threshold", 2)
            )
            artists = await queries.list_artists(conn)
            chosen = [a for a in artists if a["is_active"] and a["priority"] >= thr]
        elif mode == "notes":
            # Comma-insensitive match (mirror the sidebar notes search).
            q = queries.norm_commas((notes_query or "").strip().lower())
            artists = await queries.list_artists(conn)
            chosen = [
                a for a in artists
                if a["is_active"] and q
                and q in queries.norm_commas((a.get("notes") or "").lower())
            ]
        else:  # selected | single
            chosen = []
            for aid in artist_ids or []:
                a = await queries.get_artist(conn, aid)
                if a and not a["is_deleted"]:
                    chosen.append(a)

        queue = [
            {"url": a["url"], "name": a["name"], "status": "pending", "artist_id": a["id"]}
            for a in chosen
        ]
        session_id = await queries.create_session(
            conn, mode="batch", source_url=None, queue=queue
        )
        job = Job(session_id=session_id, interactive=False)
        self.jobs[session_id] = job
        job.task = asyncio.create_task(
            self._run(session_id, run_mode=mode, source_url=None)
        )
        return session_id

    async def relaunch(self, conn: aiosqlite.Connection, session_id: int) -> int:
        """Resume a previously paused session from its persisted current_index."""
        session = await queries.get_session(conn, session_id)
        if not session:
            raise ValueError("session not found")
        await queries.update_session(conn, session_id, status="in_progress")
        job = Job(session_id=session_id, interactive=(session["mode"] == "interactive"))
        self.jobs[session_id] = job
        # source_url is None here: the list was already scraped, queue is persisted.
        job.task = asyncio.create_task(
            self._run(session_id, run_mode="import", source_url=None)
        )
        return session_id

    # Control
    def pause(self, session_id: int) -> bool:
        job = self.jobs.get(session_id)
        if not job:
            return False
        job.control.paused.set()
        return True

    def resume(self, session_id: int) -> bool:
        job = self.jobs.get(session_id)
        if not job:
            return False
        job.control.paused.clear()
        job.control.captcha_wait.set()  # also clears a captcha wait if pending
        return True

    def skip(self, session_id: int) -> bool:
        job = self.jobs.get(session_id)
        if not job:
            return False
        job.control.skip_current.set()
        # If interactive and awaiting a decision, unblock it as a skip.
        if job.interactive:
            job.decision.put_nowait({"action": "skip"})
        return True

    def decide(self, session_id: int, decision: dict) -> bool:
        job = self.jobs.get(session_id)
        if not job:
            return False
        job.decision.put_nowait(decision)
        return True

    def stop_worker(self, session_id: int) -> bool:
        """Stop the background scrape worker after the current artist. Artists still
        queued (and any un-reviewed) stay resumable. Also ends triage if it's still
        going, so the user can't enqueue into a stopped worker."""
        job = self.jobs.get(session_id)
        if not job:
            return False
        job.control.keep_resumable = True
        job.worker_stop.set()
        # Nudge the worker if it's blocked waiting for the next item.
        job.scrape_q.put_nowait(WORKER_DONE)
        # If triage is still waiting on a decision, unblock it (no-op if already done).
        if job.interactive:
            job.decision.put_nowait({"action": "__stop__"})
        return True

    def abandon(self, session_id: int, *, save: bool = False) -> bool:
        """Stop the run. ``save=True`` keeps it resumable (status=paused); otherwise
        it's discarded.

        Interactive "Stop & save" stops *triage* but deliberately leaves the
        background scrape worker running so already-chosen artists finish - so it
        must NOT set ``abandoned`` (which the worker treats as "stop now"). It just
        unblocks the producer with ``__stop__``. A discard, and any batch stop, set
        ``abandoned`` to halt the loop immediately."""
        job = self.jobs.get(session_id)
        if not job:
            return False
        job.control.paused.clear()
        if save:
            job.control.keep_resumable = True
            if job.interactive:
                # Stop triage only; the worker keeps draining the queue in the bg.
                job.decision.put_nowait({"action": "__stop__"})
            else:
                job.control.abandoned.set()  # batch has no worker - stop the loop
        else:
            job.control.keep_resumable = False
            job.control.abandoned.set()  # discard everything (producer + worker)
            if job.interactive:
                job.decision.put_nowait({"action": "__stop__"})
        return True

    # The background run
    async def _run(self, session_id: int, *, run_mode: str, source_url: Optional[str],
                   filter_known: bool = False) -> None:
        job = self.jobs[session_id]
        conn = await connect()
        log_id = await queries.start_log(conn, mode=run_mode, session_id=session_id)
        job.total_added = 0
        job.errors = []
        try:
            settings = await queries.get_settings(conn)
            cfg = TimingConfig.from_settings(settings)
            # Early-stop config: stop scraping an artist once we hit known tracks.
            stop_on_known = str(settings.get("scrape_stop_on_known", "true")).lower() == "true"
            try:
                stop_after = int(settings.get("scrape_stop_known_count", 5))
            except (TypeError, ValueError):
                stop_after = 5

            async with ScrapeEngine(settings, on_event=job.emit, control=job.control) as eng:
                # If importing from a follow page, scrape the list first.
                if run_mode == "import" and source_url:
                    await job.emit({"type": "list_start", "url": source_url})
                    platform = get_platform(source_url, cfg)

                    async def _list_progress(count):
                        await job.emit({"type": "list_progress", "count": count})

                    found = await platform.scrape_artist_list(
                        eng.page, source_url, on_progress=_list_progress
                    )
                    queue = [
                        {"url": a.url, "name": a.name, "status": "pending", "artist_id": None}
                        for a in found
                    ]
                    await queries.update_session(
                        conn, session_id,
                        artist_queue=queue, total_artists=len(queue), status="in_progress",
                    )
                    await job.emit({"type": "list_done", "count": len(queue)})

                session = await queries.get_session(conn, session_id)
                queue = session["artist_queue"]

                # Fresh import only (never on resume - would shift current_index):
                # drop artists we already have so the user reviews only new ones.
                if filter_known and run_mode == "import":
                    new_queue, skipped_known = [], 0
                    for e in queue:
                        existing = await queries.get_artist_by_url(conn, e["url"])
                        if existing:  # skip anything already in the library, incl. soft-deleted
                            skipped_known += 1
                        else:
                            new_queue.append(e)
                    if skipped_known:
                        queue = new_queue
                        await queries.update_session(
                            conn, session_id, artist_queue=queue, total_artists=len(queue)
                        )
                        await job.emit({
                            "type": "list_filtered",
                            "skipped_known": skipped_known, "remaining": len(queue),
                        })

                total = len(queue)
                await job.emit({"type": "run_start", "total": total, "mode": run_mode})

                start_index = session.get("current_index", 0)
                job.processed = session.get("processed_count", 0)
                job.skipped = session.get("skipped_count", 0)

                scrape_args = (settings, stop_on_known, stop_after)
                if job.interactive:
                    # Triage (producer) and scraping (a single serial background
                    # worker, consumer) run concurrently and share ``eng``. The
                    # worker is the SOLE user of the page; the producer only collects
                    # decisions, so they never touch the browser at the same time.
                    worker = asyncio.create_task(
                        self._scrape_worker(job, eng, session_id, queue, total, *scrape_args)
                    )
                    await self._decision_loop(
                        job, conn, session_id, queue, total, start_index, *scrape_args
                    )
                    await job.scrape_q.put(WORKER_DONE)  # no more artists coming
                    await worker
                else:
                    await self._batch_loop(
                        job, eng, conn, session_id, queue, total, start_index, *scrape_args
                    )

            # Anything still pending review or queued-but-not-scraped means there's
            # work left to resume. A plain discard (abandoned without keep_resumable)
            # is dropped outright; everything else with leftover work is resumable.
            remaining = any(
                e.get("status") in ("pending", "queued") for e in queue
            )
            if job.control.abandoned.is_set() and not job.control.keep_resumable:
                status = "abandoned"
            elif remaining:
                status = "paused"
            else:
                status = "complete"
            await queries.update_session(conn, session_id, status=status)
            await queries.finish_log(
                conn, log_id, tracks_added=job.total_added, errors=job.errors
            )
            await job.emit({
                "type": "complete", "status": status,
                "tracks_added": job.total_added, "errors": job.errors,
            })
        except Exception as e:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            print(f"[scrape_manager] run {session_id} failed:\n{tb}", flush=True)
            try:
                await queries.update_session(conn, session_id, status="paused")
                await queries.finish_log(conn, log_id, tracks_added=job.total_added,
                                         errors=job.errors + [{"artist": "*", "message": str(e)}])
            except Exception as e2:  # noqa: BLE001
                print(f"[scrape_manager] cleanup also failed: {e2}", flush=True)
            await job.emit({"type": "error", "message": str(e)})
        finally:
            await job.events.put(DONE)
            await conn.close()
            # Remove the finished job so a later resume isn't mistaken for "already
            # live" (which would skip relaunch and hang the new progress stream).
            # Guarded so we never drop a fresh job that reused this session id.
            if self.jobs.get(session_id) is job:
                self.jobs.pop(session_id, None)

    # Interactive: producer (triage) + consumer (background scrape worker)
    async def _decision_loop(self, job, conn, session_id, queue, total, start_index,
                             settings, stop_on_known, stop_after) -> None:
        """Producer: present each artist for review and enqueue 'add & scrape'
        decisions for the background worker. Never scrapes (so it never touches the
        page). Owns ``current_index`` (the triage pointer)."""
        # Resume: artists already chosen for scraping last time are re-enqueued so
        # the worker drains them (they aren't re-reviewed).
        for i, e in enumerate(queue):
            if e.get("status") == "queued":
                artist = await queries.get_artist(conn, e.get("artist_id")) if e.get("artist_id") else None
                if artist:
                    job.queued_count += 1
                    await job.scrape_q.put((i, e, artist))

        for idx in range(start_index, total):
            if job.control.abandoned.is_set():
                break
            await job.control.wait_if_paused()
            if job.control.abandoned.is_set():
                break

            entry = queue[idx]
            if entry.get("status") in ("done", "skipped", "queued"):
                continue

            # "Skip all unstarred" short-circuits remaining decisions.
            if getattr(job, "_skip_rest", False):
                entry["status"] = "skipped"
                job.skipped += 1
                await self._persist(conn, session_id, queue, idx + 1, job)
                await job.emit({"type": "artist_skipped", "index": idx, "name": entry["name"]})
                continue

            await job.emit({"type": "await_decision", "index": idx,
                            "name": entry["name"], "url": entry["url"]})
            decision = await job.decision.get()
            action = decision.get("action", "skip")
            # "Stop & save" - leave THIS artist pending (current_index points here) and
            # stop triage; the worker keeps draining whatever was already queued.
            if action == "__stop__":
                break
            priority = int(decision.get("priority", 0))
            aliases = decision.get("aliases") or []
            notes = decision.get("notes") or ""
            do_scrape = decision.get("scrape", True)
            if decision.get("skip_all_unstarred"):
                job._skip_rest = True  # type: ignore[attr-defined]
            if action == "skip":
                entry["status"] = "skipped"
                job.skipped += 1
                await self._persist(conn, session_id, queue, idx + 1, job)
                await job.emit({"type": "artist_skipped", "index": idx, "name": entry["name"]})
                continue

            # Create/resolve the artist row now, with the user's priority/aliases/notes
            # (so they're saved even before the scrape runs).
            artist = await queries.add_artist(
                conn, name=entry["name"], url=entry["url"], priority=priority,
                aliases=aliases or None,
            )
            updates = {}
            if priority:
                updates["priority"] = priority
            if aliases:
                updates["aliases"] = aliases
            if notes:
                updates["notes"] = notes
            if updates:
                await queries.update_artist(conn, artist["id"], updates)
            entry["artist_id"] = artist["id"]

            # "Add only" - in the library now, scrape never.
            if not do_scrape:
                entry["status"] = "done"
                job.processed += 1
                await self._persist(conn, session_id, queue, idx + 1, job)
                await job.emit({
                    "type": "artist_done", "index": idx, "name": entry["name"],
                    "artist_id": artist["id"], "tracks_found": 0, "reposts_found": 0,
                    "tracks_added": 0, "error": None, "added_only": True,
                })
                continue

            # Hand off to the background scrape worker and advance immediately.
            entry["status"] = "queued"
            job.queued_count += 1
            await self._persist(conn, session_id, queue, idx + 1, job)
            await job.emit({
                "type": "artist_queued", "index": idx, "name": entry["name"],
                "artist_id": artist["id"], "queued_remaining": job.queued_count,
            })
            await job.scrape_q.put((idx, entry, artist))

    async def _scrape_worker(self, job, eng, session_id, queue, total,
                             settings, stop_on_known, stop_after) -> None:
        """Consumer: drain the scrape queue one artist at a time (preserving the
        engine's anti-bot pacing). Uses its OWN DB connection so it doesn't contend
        with the producer's transactions on a single connection."""
        wconn = await connect()
        try:
            while True:
                item = await job.scrape_q.get()
                if item is WORKER_DONE:
                    break
                if job.control.abandoned.is_set() or job.worker_stop.is_set():
                    # Leave this + any remaining queued artists as "queued" (resumable).
                    break
                idx, entry, artist = item
                await job.control.wait_if_paused()
                if job.control.abandoned.is_set() or job.worker_stop.is_set():
                    break
                await self._scrape_one(job, eng, wconn, artist, entry, idx, total,
                                       settings, stop_on_known, stop_after, from_queue=True)
                # Persist the finished entry + counts (NOT current_index - that's the
                # producer's triage pointer).
                await queries.update_session(
                    wconn, session_id, artist_queue=queue,
                    processed_count=job.processed, skipped_count=job.skipped,
                )
                if not (job.control.abandoned.is_set() or job.worker_stop.is_set()):
                    await eng.pace_between_artists()
        finally:
            await wconn.close()

    async def _batch_loop(self, job, eng, conn, session_id, queue, total, start_index,
                          settings, stop_on_known, stop_after) -> None:
        """Non-interactive (batch/update) path: review-free, scrape sequentially -
        unchanged behaviour, just factored out of ``_run``."""
        for idx in range(start_index, total):
            if job.control.abandoned.is_set():
                break
            await job.control.wait_if_paused()
            if job.control.abandoned.is_set():
                break
            entry = queue[idx]
            if entry.get("status") in ("done", "skipped"):
                continue
            artist = await queries.add_artist(
                conn, name=entry["name"], url=entry["url"],
            )
            entry["artist_id"] = artist["id"]
            await self._scrape_one(job, eng, conn, artist, entry, idx, total,
                                   settings, stop_on_known, stop_after, from_queue=False)
            await self._persist(conn, session_id, queue, idx + 1, job)
            if idx + 1 < total and not job.control.abandoned.is_set():
                await eng.pace_between_artists()

    async def _scrape_one(self, job, eng, conn, artist, entry, idx, total,
                          settings, stop_on_known, stop_after, *, from_queue) -> None:
        """Scrape a single resolved artist, emit its events, mark it done, and
        update the shared counters. Shared by the worker and the batch loop."""
        await job.emit({
            "type": "artist_start", "index": idx, "total": total,
            "name": entry["name"], "url": entry["url"],
        })
        inc_reposts, repost_lim = await self._repost_opts(conn, artist, settings)

        async def upsert(name, url, is_repost, purchase_url=None, _aid=artist["id"], _an=artist["name"]):
            return await queries.upsert_track(
                conn, artist_id=_aid, name=name, url=url,
                is_repost=is_repost, purchase_url=purchase_url, artist_name=_an,
            )

        async def on_progress(phase, count, _idx=idx, _name=entry["name"]):
            await job.emit({
                "type": "artist_progress", "index": _idx, "name": _name,
                "phase": phase, "count": count,
            })

        # Early-stop only kicks in when we already have tracks for this artist
        # (a re-scrape); first-ever scrapes pull everything.
        known_urls = (
            await queries.track_urls_for_artist(conn, artist["id"])
            if stop_on_known else None
        )
        res = await eng.scrape_artist(
            artist, upsert_track=upsert,
            include_reposts=inc_reposts, repost_limit=repost_lim,
            known_urls=known_urls, stop_after=(stop_after if stop_on_known else 0),
            on_progress=on_progress,
        )
        await conn.commit()
        await queries.refresh_artist_cache(conn, artist["id"])

        if res.error:
            job.errors.append({"artist": entry["name"], "message": res.error})
        job.total_added += res.tracks_added
        entry["status"] = "done"
        job.processed += 1
        if from_queue:
            job.queued_count = max(0, job.queued_count - 1)
        await job.emit({
            "type": "artist_done", "index": idx, "name": entry["name"],
            "artist_id": artist["id"],
            "tracks_found": res.tracks_found, "reposts_found": res.reposts_found,
            "tracks_added": res.tracks_added, "error": res.error,
            "queued_remaining": job.queued_count,
        })

    async def _persist(self, conn, session_id, queue, next_index, job) -> None:
        await queries.update_session(
            conn, session_id,
            artist_queue=queue, current_index=next_index,
            processed_count=job.processed, skipped_count=job.skipped,
        )

    async def _repost_opts(self, conn, artist, settings) -> tuple[bool, int]:
        """Resolve effective (include_reposts, repost_limit) for an artist,
        honouring the -1 'inherit global' sentinel on both fields."""
        global_inc = str(settings.get("include_reposts_default", "false")).lower() == "true"
        global_lim = int(settings.get("repost_limit_default", 30))

        ir = artist["include_reposts"]
        inc = global_inc if ir == -1 else bool(ir)

        lim = artist["repost_limit"]
        if lim == -1:
            lim = global_lim
        return inc, lim


# A single process-wide manager instance.
manager = ScrapeManager()
