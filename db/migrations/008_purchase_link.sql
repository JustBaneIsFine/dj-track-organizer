-- Per-track buy / free-download link captured during scraping.
--   purchase_url      external link found in the track's buy/purchase button (NULL = none found).
--   purchase_checked  1 once a scrape has evaluated this track for a buy link — distinguishes
--                     "scraped, genuinely no link" from "not re-scraped since this feature landed".
ALTER TABLE tracks ADD COLUMN purchase_url TEXT;
ALTER TABLE tracks ADD COLUMN purchase_checked INTEGER NOT NULL DEFAULT 0;
