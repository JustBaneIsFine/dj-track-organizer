# DJ Track Organizer

![DJ Track Organizer](docs/mainpic.png)

A local desktop app for working through the tracks you haven't heard yet. It collects
**names and links** from SoundCloud artist pages so you can prioritise artists, mark what
you've heard or want to come back to, and flag what you already own. No audio is
downloaded or touched, and nothing leaves your computer.

> **Read this first.** This reads **public** SoundCloud pages through your own browser,
> collecting names and links. Use it responsibly and respect SoundCloud's Terms of
> Service. Provided **as is, with no warranty** — how you use it is on you. **Not
> affiliated with or endorsed by SoundCloud.** See [Disclaimer](#disclaimer).

## Features

- Originals and reposts, with the same repost merged across artists
- Priority stars and per-track status: new, listened, revisit, owned
- **Check folder** compares a music folder against your library by filename. You review
  every match, play it if you're unsure, and untick the wrong ones. They stay unticked.
- Buy and free-download links captured per track, so you can triage by where they point
- Scraping is slow, sequential and **logged out by default**
- No account, no telemetry

## Install

### Windows
1. Download `DJOrganizer_windows.zip` from the [latest release](https://github.com/JustBaneIsFine/dj-track-organizer/releases/latest).
2. Unzip it anywhere and run `DJOrganizer.exe`.
3. The app is not code-signed yet, so Windows SmartScreen may say "Windows protected
   your PC". Click **More info -> Run anyway**. (You can verify the source by building
   it yourself from this repo.)

### macOS (Apple Silicon)
1. Download `DJOrganizer_mac_apple-silicon.zip` from the latest release and unzip it.
2. The app is not notarized yet, so on first launch **right-click the app -> Open ->
   Open**. After that it launches normally.
3. Intel Macs: run from source (below).

### Run from source (any OS)
```bash
python -m venv .venv
# Windows:
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python main.py
# macOS / Linux:
# .venv/bin/python -m pip install -r requirements.txt
# .venv/bin/python -m playwright install chromium
# .venv/bin/python main.py
```
The app runs a local server and opens in a native window (WebView2 on Windows). If
the native window doesn't appear on Windows, install the
[WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/); the app
otherwise falls back to your default browser. Data lives at
`~/.dj-organizer/dj_organizer.db`.

## Updating

Download the new zip, delete the old `DJOrganizer` folder, extract the new one in its
place. Your data lives elsewhere and carries over untouched. The app tells you when a
release is out.

## Uninstall

Delete the `DJOrganizer` folder (or the `.app` on macOS). To also remove your data,
delete `~/.dj-organizer/` (on Windows, `%USERPROFILE%\.dj-organizer`); it holds the
database, logs, and saved browser login. If you want a backup first, use Export in Settings.

## Privacy

100% local. No accounts, no analytics, no telemetry. The only outbound network calls
are to SoundCloud while scraping, and a once-per-launch check to GitHub for a newer
release (you can turn that off in Settings). Your database, logs, and browser profile
stay in `~/.dj-organizer/` and are never uploaded.

## Scraping notes

Defaults are deliberately slow: random delays between artists, one request at a time,
no browser window. Scraping runs logged out, so no account is attached to it. If
SoundCloud asks for a check, a visible browser opens for you to complete it. You can
point the app at a logged-in Chrome profile in Settings instead — use a throwaway
account if you do, not your main one.

## Feedback

- Bugs and feature requests: [open an issue](https://github.com/JustBaneIsFine/dj-track-organizer/issues/new)
- Contact: djtezej@gmail.com

## Building a release

```bash
python build.py   # Windows onedir bundle in dist/DJOrganizer
```
See [RELEASE.md](RELEASE.md) for the tag-and-publish flow.

## Project layout

| Area | Where |
|---|---|
| Entry point | `main.py` |
| Paths + defaults | `config.py` |
| DB schema | `db/migrations/*.sql` (numbered) |
| All SQL | `db/queries.py` |
| Scraping engine | `scraper/engine.py` |
| SoundCloud DOM | `scraper/platforms/soundcloud.py` |
| API | `api/server.py`, `api/routes/*` |
| Background jobs | `api/scrape_manager.py` |
| Frontend | `frontend/` (Alpine.js, no build step) |

## Contributing

Issues and pull requests are welcome. Keep changes focused, match the existing style,
and don't add telemetry or anything that sends user data off the machine.

## License

[MIT](LICENSE). Use at your own risk.

## Disclaimer

This software is provided "as is", without warranty of any kind. The author is not
liable for any claim, damages, or other liability arising from its use. It reads
publicly accessible pages through your own browser and stores only names and links;
it does not download audio. You are solely responsible for ensuring your use complies
with SoundCloud's Terms of Service and applicable law. This project is not affiliated
with, authorized, or endorsed by SoundCloud.

## Contact

- **Email:** djtezej@gmail.com
- **Bug reports & feature requests:** [open an issue](https://github.com/JustBaneIsFine/dj-track-organizer/issues/new)
