// Single source of truth for all server calls. Every fetch() lives here.
window.api = (() => {
  async function j(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      throw new Error(`${res.status}: ${detail}`);
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res.text();
  }
  const body = (b) => ({
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(b),
  });

  return {
    // settings + stats + data
    meta: () => j("/api/meta"),
    getSettings: () => j("/api/settings"),
    patchSettings: (updates) => j("/api/settings", { method: "PATCH", ...body({ updates }) }),
    getStats: (artistId) => j("/api/stats" + (artistId ? "?artist_id=" + encodeURIComponent(artistId) : "")),
    exportAll: () => j("/api/export"),
    importAll: (data) => j("/api/import", { method: "POST", ...body(data) }),
    purgeDeleted: () => j("/api/purge/deleted", { method: "DELETE" }),
    resetAll: () => j("/api/reset", { method: "POST" }),

    // artists
    listArtists: (q = {}) => {
      const p = new URLSearchParams();
      if (q.include_deleted) p.set("include_deleted", "true");
      if (q.search) p.set("search", q.search);
      if (q.sort) p.set("sort", q.sort);
      if (q.artist_id) p.set("artist_id", q.artist_id);
      if (q.unscraped) p.set("unscraped", "true");
      return j("/api/artists?" + p.toString());
    },
    addArtist: (b) => j("/api/artists", { method: "POST", ...body(b) }),
    patchArtist: (id, b) => j(`/api/artists/${id}`, { method: "PATCH", ...body(b) }),
    deleteArtist: (id) => j(`/api/artists/${id}`, { method: "DELETE" }),

    // tracks
    listTracks: (q = {}) => {
      const p = new URLSearchParams();
      Object.entries(q).forEach(([k, v]) => {
        if (v === null || v === undefined || v === "") return;
        p.set(k, v);
      });
      return j("/api/tracks?" + p.toString());
    },
    patchTrack: (id, b) => j(`/api/tracks/${id}`, { method: "PATCH", ...body(b) }),
    bulkTracks: (b) => j("/api/tracks/bulk", { method: "PATCH", ...body(b) }),

    // scrape
    startImport: (b) => j("/api/scrape/import", { method: "POST", ...body(b) }),
    startUpdate: (b) => j("/api/scrape/update", { method: "POST", ...body(b) }),
    pause: (sid) => j(`/api/scrape/pause/${sid}`, { method: "POST" }),
    resume: (sid) => j(`/api/scrape/resume/${sid}`, { method: "POST" }),
    skip: (sid) => j(`/api/scrape/skip/${sid}`, { method: "POST" }),
    decide: (sid, b) => j(`/api/scrape/decide/${sid}`, { method: "POST", ...body(b) }),
    abandon: (sid) => j(`/api/scrape/abandon/${sid}`, { method: "POST" }),
    stopSave: (sid) => j(`/api/scrape/abandon/${sid}?save=true`, { method: "POST" }),
    stopBg: (sid) => j(`/api/scrape/stop-bg/${sid}`, { method: "POST" }),
    log: () => j("/api/scrape/log"),

    // owned (folder scan)
    scanOwned: (filenames, floor, artist_id, token) => j("/api/owned/scan", { method: "POST", ...body({ filenames, floor, artist_id, token }) }),
    pickFolderNative: (artist_id, floor, token) => {
      const p = new URLSearchParams();
      if (artist_id) p.set("artist_id", artist_id);
      if (floor != null) p.set("floor", floor);
      if (token) p.set("token", token);
      const qs = p.toString();
      return j("/api/owned/pick" + (qs ? "?" + qs : ""), { method: "POST" });
    },
    ownedProgress: (token) => j("/api/owned/progress?token=" + encodeURIComponent(token)),
    openOwnedFile: (filename) => j("/api/owned/open", { method: "POST", ...body({ filename }) }),

    // sessions
    resumable: () => j("/api/sessions/resumable"),
    getSession: (sid) => j(`/api/sessions/${sid}`),
    resumeSession: (sid) => j(`/api/sessions/${sid}/resume`, { method: "POST" }),

    // SSE: returns the EventSource so the caller can close it.
    progress: (sid, onEvent) => {
      const es = new EventSource(`/api/scrape/progress/${sid}`);
      es.onmessage = (e) => {
        try { onEvent(JSON.parse(e.data)); } catch (err) {}
      };
      es.onerror = () => { es.close(); };
      return es;
    },
  };
})();
