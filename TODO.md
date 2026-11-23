# Tarseer TODO

Ideas and known issues for after v1. Keep this file current: move items to
**Done** (with the commit) when finished, and add new ideas or issues
found while working.

## Next up
- [ ] **Real app icon** to replace the placeholder in `data/icons/`.
- [ ] **Homepage/repo URL** in the metainfo (`url type="homepage"`) and About
      dialog, once the project is published on GitHub.

## Features
- [ ] **Background update checks + notifications.** Check periodically (or at
      login) and notify when updates are available. Needs a background portal
      or autostart entry; mind the GitHub rate limit.
- [ ] **Optional GitHub token** (Preferences) to raise the API limit from 60 to
      5000 requests/hour. Store it in the keyring (libsecret), not in apps.json.
- [ ] **"Update All" button** in the header bar when several updates are available.
- [ ] **Cancel a running download** (core already supports a cancel event in
      `github.download`).
- [ ] **Multiple app grid entries per app**, e.g. Calibre's E-book Viewer and
      Editor alongside the main program.
- [ ] **`StartupWMClass` detection/setting**, so running apps get the right icon
      in the dash instead of a generic or duplicate one (especially Java apps
      like Sparrow).
- [ ] **Edit the release file pattern** in the details dialog (currently
      read-only), e.g. by picking a file again from the latest release.
- [ ] **Pre-release channel** per app (use `/releases` instead of `/releases/latest`).
- [ ] **Adopt an existing install** (point at a folder + tell Tarseer its version)
      instead of reinstalling.
- [ ] **Translations:** strings already use `_()`; set up `po/` and Meson i18n.
- [ ] **Flathub submission** (needs: real icon, screenshots in metainfo, homepage URL,
      review of `--filesystem` permissions).
- [ ] **Custom app Icon picking** Let the user pick a custom icon for the .desktop file.
      Put an option to pick a custom icon near or in the already existing dropdown where
      you choose between different icons in the app folder.

## Known issues / polish
- [ ] Icon dropdown shows long paths truncated (`resources/images/calib…`);
      consider showing the file name with the folder as subtitle.
- [ ] "Check for updates" errors are still toasts (one alert per app would
      pile up), so long ones get cut off; the full text is only in the row's ⚠
      tooltip. Consider an "error details" action.
- [ ] The "Open Folder" button has not been tested by Claude (it would open a
      window on the user's desktop); verified manually: ☐
- [ ] Changing the repository keeps the old release file pattern; if the new
      repo names its files differently, Update fails with "no file matches".
      Re-pick the file on repo change (ties in with editing the pattern).

## Done
- [x] v1: scaffold, core logic, main window, Add App dialog, details dialog
      (`f471394` … `ac25af6`).
- [x] Menu entries named `<id>.desktop` with conflict detection and migration of
      `tarseer-<id>.desktop` (`a088463`).
- [x] Code review of v1 (`/code-review src/`, 2026-11-23): asset name
      tokenizing, version wildcard in patterns, repo change keeping the old
      release, half-registered installs, rollback launcher paths, rename
      recovery in install/rollback, disk work off the main loop, error alerts,
      `%%` in Exec (`8eb7670` … `40aa0af`).
