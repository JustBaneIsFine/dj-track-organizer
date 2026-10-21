-- "Artist - Title" from the matched file's tags, when the native scan could read them.
ALTER TABLE owned_check_items ADD COLUMN tags TEXT;
