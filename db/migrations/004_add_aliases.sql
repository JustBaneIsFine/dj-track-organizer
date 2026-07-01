-- Migration 004: artist alternate names (aliases).
-- The scraped SoundCloud handle often differs from the name an artist uses in
-- track titles/filenames. Aliases are extra names used to improve owned-folder
-- matching and artist search. Stored as a JSON array of strings (NULL = none).

ALTER TABLE artists ADD COLUMN aliases TEXT;
