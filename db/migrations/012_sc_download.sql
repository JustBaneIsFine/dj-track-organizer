-- SoundCloud's own "Download file" option (under More), read from the api-v2 track data.
--   tracks.sc_download    1 when the artist has downloads switched on for the track.
--   artists.dl_checked    1 once the artist has had a full pass since this landed; until then
--                         an update ignores early-stop for them so older tracks get flagged.
ALTER TABLE tracks ADD COLUMN sc_download INTEGER NOT NULL DEFAULT 0;
ALTER TABLE artists ADD COLUMN dl_checked INTEGER NOT NULL DEFAULT 0;
