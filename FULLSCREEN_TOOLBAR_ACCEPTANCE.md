# ZorinMacBridge 0.14.5 Full-screen Toolbar acceptance

Do not tag v0.14.5 until these checks pass on a real Zorin desktop.

1. Connect to the Mac Desktop and double-click the remote image.
2. Confirm the app enters full-screen.
3. Confirm the top toolbar is above the Mac image and does not cover any Mac pixels.
4. Confirm the toolbar shows connection state, `Quality: ...`, input state, and `Exit Full Screen`.
5. Confirm the normal non-full-screen status/hint row is hidden while full-screen is active.
6. Click `Exit Full Screen`; confirm normal layout is restored.
7. Re-enter full-screen and press Alt+Esc; confirm it exits.
8. Re-enter full-screen and double-click the top toolbar; confirm it exits.
9. Confirm double-clicking inside the Mac image while already full-screen is delivered to the Mac and does not exit full-screen.
10. Confirm video scaling/aspect ratio and keyboard/mouse capture behavior are unchanged.
