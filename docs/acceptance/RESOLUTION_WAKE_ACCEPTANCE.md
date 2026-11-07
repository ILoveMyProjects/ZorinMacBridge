# 0.14.14 VirtualDisplayWake acceptance

Run on the real Zorin + MacBook setup. The 2K/4K monitors are connected to **Zorin**, not to the MacBook.

1. Connect Desktop with **Resolution = 1080p**. Confirm the normal physical MacBook desktop is visible.
2. Select **2K**. Confirm macOS gets a new virtual remote desktop and the decoded stream reports **2560×1440**. It must not be a crop/upscale of the MacBook panel.
3. Confirm the virtual 2K desktop becomes the only active workspace used by the remote session: the built-in MacBook panel is detached for this login session, so menu bar/new windows appear on the remote desktop.
4. Select **4K**. Confirm the virtual desktop becomes **3840×2160** and the decoded stream reports that exact size. Test with **Ultra (40 Mbit/s)**. If 4K fails to prepare, the working 2K desktop must remain available.
5. Move/click the pointer across all four corners and edges. Input must align with the virtual 2K/4K desktop.
6. Switch **Resolution = 1080p**. Confirm the virtual display disappears and the built-in MacBook display is re-enabled for the current session.
7. Re-select **2K** or **4K**, then allow the MacBook panel to turn off naturally. The ZMB server must remain reachable; there must be no automatic whole-Mac idle sleep caused merely by display-off.
8. While the panel is off/locked, confirm Desktop control/input stays connected and video remains available from the virtual display. If ScreenCaptureKit briefly fails, only H.264 may retry; the control session must not enter a full reconnect loop.
9. Explicitly choose macOS **Sleep**. Then click **Connect** on the saved Mac from Zorin. Wake-on-Connect should attempt to wake it and resume the normal login/lock screen; enter the password yourself as before.
10. Verify no automatic-login, FileVault, password, `pmset` sleep policy, or lock-screen setting changed.
11. Regression: Full Screen / Exit Full Screen / Esc / Alt+Esc, keyboard/mouse, clipboard and Files recursive folder download still work.

If step 2 or 4 reports `virtual display unavailable`, collect the Mac server log. That indicates the private `CGVirtualDisplay` API is missing/changed on that macOS build; ZMB must not fall back to fake upscaling.

If step 7-9 fails, collect `~/.local/state/zorinmacbridge/client.log` from Zorin plus P0/System logs from the Mac.
