- [New overview video](https://youtu.be/kowmLRPeob0), also opened by the tutorial button in About.

### Added

- **Guided tour** that walks through adding an artist, running an update and reviewing tracks, using the real app. Offered on first launch.
- **Getting started checklist** in the sidebar. Items tick off from what's already done, and the card can be hidden.
- **Animations**, with a Motion setting under Settings, Appearance: Off, Low, Mid or All.
- **Feedback window** for sending suggestions, bug reports or anything else from inside the app. Open it with Feedback in the top bar.
- **Ko-fi link** in the top bar and the About panel.
- **SoundCloud downloads** are detected. Tracks where the artist turned on Download file (under More on SoundCloud) now count as Free. The first update after installing does a full pass on each artist so older tracks get checked; later updates are back to normal speed.
- **Free, Buy and Unknown pills** on track rows. Unknown marks tracks with no download option and no buy link.
- **Unknown filter** next to Free and Buy.
- **Update chime**, an optional sound when an update finds new tracks. Off by default; turn it on under Settings, Updating.
- **Filter chips** when nothing matches. Each active filter shows as a chip that can be removed on its own.

### Changed

- **New icon set.** Buttons and markers use Phosphor icons instead of text symbols.
- **Light theme** is now gray, with larger, bolder text and sharper edges. Track names use the regular font instead of monospace.
- **Disclaimer and welcome** are one two-step window on first launch.
- **Only the track name opens a track.** Clicking elsewhere on the row selects it.
- **Artist filter** dims non-matching artists instead of hiding them.
- **Dark title bar** on Windows when the dark theme is on.
- **Tooltips** follow the app theme and appear faster.
- **Bulk actions** shrink to icons when the window is narrow instead of wrapping.
- **"Copy non-DL & listened"** is now "No free DL: copy & mark heard". It works the same.
- **Track and artist lists** have faint row striping.
- **Free, Buy and Unknown tooltips** say what each one counts.
- **Update notice** lists the notes for every version since the installed one, so skipped releases aren't missed.

### Fixed

- **Restoring a backup** that was damaged or from a newer version could wipe tracks. Restores now apply fully or not at all.
- **A blank repost limit** in Settings made every update fail. Number settings are now checked before saving.
- **Esc in a dialog** cleared the filters behind it instead of closing the dialog.
- **Closing Settings without saving** kept the unsaved values on screen.
- **Artist URLs** that aren't SoundCloud profiles are rejected when adding or importing.
- **Search** treats `%` and `_` as plain characters.
- **Reset** also clears saved update results.
