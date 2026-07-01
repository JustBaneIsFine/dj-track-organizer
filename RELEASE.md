# Releasing

1. Bump `APP_VERSION` in `config.py`.
2. Update `RELEASE_NOTES.md` with the changes for this version.
3. Commit, then tag and push:
   ```bash
   git tag v0.1.0
   git push origin main --tags
   ```
4. GitHub Actions builds the Windows and macOS (Apple Silicon) zips and publishes a
   Release with them attached. The in-app update check picks it up on the next launch.

The asset names must stay `DJOrganizer_windows.zip` and `DJOrganizer_mac_apple-silicon.zip`;
the README download links and the in-app update banner point at them.
