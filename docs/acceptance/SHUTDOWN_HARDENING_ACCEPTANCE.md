# 0.14.15 ShutdownHardening acceptance

On Zorin, test each case separately:

1. Start the client while disconnected. Close with the window X. The window must disappear immediately and the process must exit.
2. Connect Desktop, then close with X while video is active. No "not responding" dialog is acceptable.
3. Open the embedded Terminal, connect it, then close the main window. The client must exit without waiting for VTE/SSH.
4. Start a Project Action or Project Sync, then close the main window. The client must exit without waiting for the child process.
5. Trigger Bonjour refresh or update check, then immediately close the main window. Late callbacks must not reopen or keep the app alive.
6. Test Quit from the tray/menu if available. The process must disappear within the hard-exit grace period even if the tray backend misbehaves.
7. Relaunch the client after each case and verify normal startup, Desktop, Files, keyboard/mouse, fullscreen, and update UI still work.

Diagnostic check after a normal clean close:

```bash
pgrep -af zorinmacbridge
```

Expected: no old client process remains.
