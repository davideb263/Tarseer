# Tarseer TODO

Ideas and known issues for after v1. Keep this file current: move items to
**Done** (with the commit) when finished, and add new ideas or issues
found while working.

## Next up
v0.3 "Updates come to you": background checks + notifications, plus the
`riscv64gc` and "Reinstall now" items. Then translations + Flathub as a
"v1.0 readiness" release.

v0.3 plan (agreed 2026-11-24):
1. [x] **Check logic in core.** `AppManager.check_updates()` returns a release or
       error per app (4 lookups at a time), stops early on rate limit / rejected
       token, and caches the results; rows and the window read from the cache (`850bcb0`).
2. [x] **Use the rate limit carefully.** ETag + `If-None-Match` per repo, last
       replies cached in `$XDG_CACHE_HOME/tarseer/releases.json` (pruned to managed
       repos at startup); `AppManager.has_quota_for()` (every app counted as a
       request + 10 spare) for background runs to call first. Without a token a 304
       still costs a request (verified live); with a token it's free (verified
       manually with a real token: the quota in Preferences stays the same) (`d6654c6`).
3. [x] **Background service** (`ui/background.py`). `tarseer --background`: no window,
       first check 2 min after login (`--check-now`: right away); a second instance
       hands off and exits. `check-interval-hours` 0 = at login only (check, retry
       up to 3× every 5 min if offline, exit), otherwise resident: `hold()` and a
       15-min tick that checks when due by wall clock (`core/schedule.py`, so
       suspend counts). A normal launch also starts the resident service if
       enabled; closing the window keeps it running, Ctrl+Q quits. Skips if the
       quota is low or the window is busy; waits up to 30 s for the keyring, then
       goes on without the token; errors only logged. The app now owns the manager
       and token loading; an open window refreshes after a background check.
       `ui/portal.py` wraps the Background portal (autostart + status); calling it
       to register autostart is step 5.
4. [ ] **Notifications.** One `Gio.Notification` per run listing the apps; clicking
       it only opens the window (no action buttons). Notify only about tags not
       notified before; withdraw it once updates are installed.
5. [ ] **Preferences "Updates" group.** Switch "Check for updates in the background"
       (opt-in, default off; calls `portal.request_autostart`), frequency dropdown
       (`check-interval-hours`: 0/6/12/24), "Last checked" (`last-check`); then
       `BackgroundService.restart()`. A portal refusal goes in an alert and turns
       the switch back off. (Keys added in step 3; `notified-tags` comes with step 4.)
6. [ ] **Dev/test.** Done in step 3: `TARSEER_CHECK_INTERVAL=<s>`, `--check-now`,
       dev settings keyfile, `dev_screenshot.py --background-check`. Left: screenshot
       of the Preferences group; manual test of the portal prompt, autostart after
       re-login, notification click, locked keyring / auto-login (the keyring may
       ask to be unlocked at login: the check goes on without the token after 30 s).
7. [ ] **Small extras.** `riscv64gc` arch alias; "Reinstall now" after changing
       the release file.
8. [ ] **Release 0.3.0.** Version bump, metainfo notes, README, merge to `main`, tag.

## Features
- [ ] **Background update checks + notifications.** Check periodically (or at
      login) and notify when updates are available. Needs a background portal
      or autostart entry; mind the GitHub rate limit.
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
      tooltip. Consider an "error details" action. (Rate limit and rejected
      token errors are already alerts, shown once per check.)
- [ ] `riscv64gc` (ripgrep's name for riscv64) isn't recognized as a foreign
      arch, so it's offered on x86_64. Match arch tokens by prefix or add aliases.

## Done
- [x] v0.2.1: review and security review fixes; version bump and metainfo
      release notes (`abcf182`).
- [x] Fixes from the 2026-11-24 security review: symlinks escaping the install
      dir after a single top-level folder is flattened are refused; file names
      with control characters are never offered or written to a menu entry;
      HTTPS-only opener that drops `Authorization` on cross-host redirects;
      downloads checked against the asset's `size` and `sha256` digest; app ids
      validated on load (an empty id made Remove delete all of `~/.local/opt`);
      extraction stops before leaving less than 512 MiB free (`ac86e90`).
- [x] Fixes from the v0.1.0..v0.2.0 review: `selectable_assets` test was never
      collected; rate limit / rejected token errors from a check are one alert,
      not a toast per app; rows no longer stay "Queued" after a details-dialog
      operation; a late keyring read no longer overwrites a token set in
      Preferences (Preferences refreshes and checks wait for it); 401 blamed on
      the token only when one was sent; cancel hidden at extraction even without
      Content-Length; release file / repo picks dropped because the app got busy
      now say so (`7d04e30`).
- [x] v0.2.0 "Update all, safely": re-pick release file, Update All, cancel
      downloads, GitHub token; version bump and metainfo release notes (`e82bbb8`).
- [x] Optional GitHub token in Preferences, validated against GitHub, stored
      with libsecret (Secret portal), sent only to api.github.com (`a312cd0`).
      Saving a real token was tested with a faked GitHub reply only; test
      manually with a real token.
- [x] Cancel a running download: ✕ next to a row's progress bar, Cancel on the
      Add App progress page, and Update All turns into Stop while it runs.
      Extraction can't be cancelled, so the buttons hide once the download ends (`e76ea68`).
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
