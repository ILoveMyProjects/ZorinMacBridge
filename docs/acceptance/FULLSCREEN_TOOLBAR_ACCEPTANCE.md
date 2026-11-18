# ZorinMacBridge 0.14.9 Full-screen Toolbar acceptance

Do not tag v0.14.9 until these checks pass on a real Zorin desktop.

1. Connect to the Mac Desktop. Confirm keyboard/mouse capture is ON by default.
2. Click **Full Screen**; confirm the app enters full-screen.
3. Confirm the top toolbar is above the Mac image and does not cover any Mac pixels.
4. Confirm the toolbar shows connection state, `Quality: ...`, input state, and `Exit Full Screen`.
5. Confirm the normal non-full-screen status/hint row is hidden while full-screen is active.
6. Double-click inside the remote Mac image; confirm the double-click is delivered to macOS and does not toggle full-screen.
7. Click `Exit Full Screen`; confirm normal layout is restored.
8. Re-enter full-screen and press Esc; confirm it exits.
9. Re-enter full-screen and press Alt+Esc; confirm it exits.
10. Confirm there is no local double-click gesture for entering or leaving full-screen.
