# 0.14.11 Wake + Fullscreen acceptance

Real-device checks for Zorin + macOS. These are not claimed as executed by the source test suite.

1. Pair the Mac while it is awake, open **System**, and verify `Wake MAC` is populated.
2. Connect Desktop once, then put the Mac to sleep.
3. From the saved `paired/offline` Mac entry press **Connect**.
4. Confirm Zorin shows `Waking Mac…`, the Mac wakes, and the normal macOS login/lock screen becomes visible without changing login policy.
5. Enter the macOS password manually through the remote Desktop as usual.
6. While connected, press **Full Screen** and verify the GTK window actually enters full screen.
7. Verify **Exit Full Screen** exits immediately.
8. Re-enter and verify plain **Esc** exits.
9. Re-enter and verify **Alt+Esc** exits when the desktop/window manager delivers that chord to the app.
10. Trigger an automatic Desktop reconnect while full-screen and verify Exit/Esc still work afterwards.
11. Confirm double-click remains a remote Mac gesture and does not toggle full screen.
12. Confirm no macOS automatic-login/FileVault/login policy was changed by the release.
