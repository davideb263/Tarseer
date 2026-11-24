# Tarseer TODO

Ideas and known issues for after v1. Keep this file current: move items to
**Done** (with the commit) when finished, and add new ideas or issues
found while working.

## Next up
v0.2 "Update all, safely", in this order:
1. Cancel a running download (also: cancel the rest of an "Update All").
2. Optional GitHub token in the keyring.

Planned for v0.3: background checks + notifications (needs the token);
then translations + Flathub as a "v1.0 readiness" release.

## Features
- [ ] **Background update checks + notifications.** Check periodically (or at
      login) and notify when updates are available. Needs a background portal
      or autostart entry; mind the GitHub rate limit.
- [ ] **Optional GitHub token** (Preferences) to raise the API limit from 60 to
      5000 requests/hour. Store it in the keyring (libsecret), not in apps.json.
- [ ] **Cancel a running download** (core already supports a cancel event in
      `github.download`).
- [ ] **Multiple app grid entries per app**, e.g. Calibre's E-book Viewer and
      Editor alongside the main program.
- [ ] **`StartupWMClass` detection/setting**, so running apps get the right icon
      in the dash instead of a generic or duplicate one (especially Java apps
      like Sparrow).
- [ ] **"Reinstall now" after changing the release file.** A new file is only
      used from the next update; if the installed tag is already the latest,
      offer to reinstall it (e.g. switching from the gnu to the musl build).
- [ ] **Pre-release channel** per app (use `/releases` instead of `/releases/latest`).
- [ ] **Adopt an existing install** (point at a folder + tell Tarseer its version)
      instead of reinstalling.
- [ ] **Translations:** strings already use `_()`; set up `po/` and Meson i18n.
- [ ] **Flathub submission** (needs: screenshots in metainfo, review of `--filesystem` permissions).
- [ ] **Custom app Icon picking** Let the user pick a custom icon for the .desktop file.
      Put an option to pick a custom icon near or in the already existing dropdown where
      you choose between different icons in the app folder.

## Known issues / polish
- [ ] Icon dropdown shows long paths truncated (`resources/images/calib…`);
      consider showing the file name with the folder as subtitle.
- [ ] "Check for updates" errors are still toasts (one alert per app would
      pile up), so long ones get cut off; the full text is only in the row's ⚠
      tooltip. Consider an "error details" action.
- [ ] `riscv64gc` (ripgrep's name for riscv64) isn't recognized as a foreign
      arch, so it's offered on x86_64. Match arch tokens by prefix or add aliases.

## Done
- [x] "Update All" in the header bar (2+ updates): one app at a time, queued rows
      marked, failures collected into one alert at the end (`a047284`).
- [x] Re-pick the release file from the details dialog ("Change…"), and pick it
      again when a new repository names its files differently (`daff575`).
      Cancel and confirm paths on repository change verified manually.
- [x] Homepage, bug tracker and source URLs in the metainfo and About dialog (`d83cfa4`).
- [x] Real app icon in `data/icons/` (`a401572` first draft; vector SVG in `4df7f2e`).
- [x] v1: scaffold, core logic, main window, Add App dialog, details dialog
      (`f471394` … `ac25af6`).
- [x] Menu entries named `<id>.desktop` with conflict detection and migration of
      `tarseer-<id>.desktop` (`a088463`).
- [x] Code review of v1 (`/code-review src/`, 2026-11-23): asset name
      tokenizing, version wildcard in patterns, repo change keeping the old
      release, half-registered installs, rollback launcher paths, rename
      recovery in install/rollback, disk work off the main loop, error alerts,
      `%%` in Exec (`8eb7670` … `40aa0af`).
- [x] The "Open Folder" button has not been tested by Claude (it would open a
      window on the user's desktop); verified manually: Test OK
