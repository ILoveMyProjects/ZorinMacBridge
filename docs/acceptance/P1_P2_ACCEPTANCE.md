# ZorinMacBridge 0.9.0 — P1/P2 acceptance checklist

This checklist is for the real Apple Silicon Mac after CI has built the 0.9.0 DMG.

## P0 regression

1. Install/update the 0.9.0 app on the Mac.
2. Confirm `Enable / Repair` succeeds or reports only the known manual Remote Login action when macOS blocks `systemsetup`.
3. Pair from Zorin `System → Pair / Configure`.
4. Refresh System status. It must return without freezing the Zorin GUI.
5. Restart ZMB, then use **Recover desktop** and confirm the desktop service returns.

## P1 system dashboard

1. Confirm the state is one of `DESKTOP READY`, `SYSTEM ONLINE`, `RECOVERY NEEDED`, or `DEGRADED`.
2. Verify power source, battery percentage/detail, CPU, RAM, disk, heartbeat, FileVault, Remote Login, and wake-on-network setting are displayed.
3. Toggle **Wake for network access** Enable → Disable → restore the desired setting; each action must return within the watchdog timeout.
4. Stop ZMB and confirm state becomes `RECOVERY NEEDED`; click **Recover desktop** and confirm it returns to `DESKTOP READY`.
5. Log out of the macOS GUI session and verify System administration still reports `SYSTEM ONLINE` while desktop/Xcode GUI actions fail cleanly instead of hanging.

## P2 Xcode / Simulator

1. With a logged-in macOS GUI session and Xcode selected as the active developer directory, open the **Xcode** tab on Zorin.
2. Refresh. Confirm Xcode version/build and developer directory appear.
3. Confirm available Simulator devices appear with runtime, state, and UDID.
4. Boot a Shutdown simulator. Refresh and confirm it becomes Booted.
5. Shutdown the same simulator. Refresh and confirm it becomes Shutdown.
6. Click **Open Simulator** and confirm Simulator opens in the Mac GUI session.
7. Click **Open Xcode** and confirm Xcode opens in the Mac GUI session.
8. Repeat Refresh while Xcode is closed: the operation must not freeze the Zorin GUI.
9. If no simulator runtime is installed, the tab should show the simctl error/status without freezing.

## Failure tests

- Disable Remote Login temporarily: System and Xcode actions must time out/recover the GUI, not hang it.
- Put the Mac to sleep: the client must remain responsive when a request fails.
- Change the DHCP address and run Find Macs: the paired machine should be rediscovered by `server_id` and the profile host updated.
- Send an invalid Simulator identifier through tests/backend: it must be rejected before SSH command execution.
