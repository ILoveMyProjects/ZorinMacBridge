# ZorinMacBridge 0.14.7 — Update / shutdown / lifecycle acceptance

## Update retry

1. Publish/serve a valid newer release.
2. Trigger **Check for updates** and start installation.
3. If a transient `408/429/500/502/503/504` or socket timeout/reset occurs, confirm the GUI reports a retry attempt instead of immediately failing.
4. Confirm the downloaded package is still SHA-256 verified before installation.
5. Confirm permanent errors such as `404` fail without repeated retries.

## Window close

1. Start a Desktop connection and optionally a Terminal/Project action.
2. Close the main ZorinMacBridge window using the window close button.
3. The window must disappear immediately; GNOME must not display **not responding**.
4. Repeat while an update check/download is active. Late worker results must not reopen or keep the GUI alive.
5. Repeat with the system tray enabled. Tray cleanup must not block the GTK window close.

## Unexpected-exit evidence

1. Start the client and confirm this file is created/appended:
   `~/.local/state/zorinmacbridge/client.log`
2. Trigger **Check for updates** and leave the main window open. The update check/download must not close the application.
3. Close normally and confirm the log contains an explicit shutdown reason and normal `atexit` line.
4. If the window/process ever disappears without an explicit close, inspect the last lines of the persistent file. A shutdown line with `reason=unrequested`, an unhandled exception, or Python faulthandler output identifies the class of failure even though the in-window log is gone.
