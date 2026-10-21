### Added

- **Sort direction can be flipped.** An arrow next to the sort menu switches between ascending and descending, for tracks and for artists.
- **Most owned** is a new artist sort.
- **The artist list loads as you scroll**, in batches set by "Artists shown at a time" in Settings. Tracks load the same way.

### Changed

- **Large libraries are much faster.** At 100,000 tracks the artist list loads over 100x faster, and track and artist rows draw about 3x faster.
- **Updating is about 2x faster** per artist page.
- **Track sorting is down to Priority, Artist and Date added.**
- **Check folder matching has been refined.** It also reads artist and title tags now, so files named only by title are found.
- **Backups include check decisions** and the results of past updates.

### Fixed

- **High CPU use while the window was open and idle**, most noticeable on weaker systems.
- **Continue last import did nothing** after Stop & save while added artists were still updating in the background.
- **The progress count restarted at 0** when continuing an import.
- **Export in the app window** now opens a Save dialog.
