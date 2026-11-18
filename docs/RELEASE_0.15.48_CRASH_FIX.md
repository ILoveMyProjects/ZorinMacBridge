# ZorinMacBridge 0.15.48 — crash fix evidence and limits

## Inputs used

- `ZorinMacBridge Server-2026-11-12-123359.ips`: native `EXC_BAD_ACCESS / SIGSEGV`, `vtencoder-callback-queue`, `objc_retain`, `H264ScreenStreamer.createEncoder` output callback.
- `ZorinMacBridge Server-2026-11-12-123617.ips`: `SIGABRT` on Tk main thread from `_tkinter.PythonCmd` into `PyEval_RestoreThread`.
- Zorin 0.15.47 log: process disconnect during Adaptive `Efficient → Balanced` around 12:33:58, and later desktop/video disconnect around 12:36:14.

## Changes

1. Session-owned Swift callback refcon now strongly retains the streamer, with matching release after `VTCompressionSessionCompleteFrames` and `VTCompressionSessionInvalidate`. A serial capture-queue barrier prevents a running capture callback from submitting a new frame during retirement. Teardown remains asynchronous relative to the TLS session.
2. macOS GUI and menu-bar/tray actions can no longer call `root.after` off the Tk thread. Workers enqueue ordinary Python callbacks. The Tk thread drains the queue. Callbacks are discarded after close.
3. Persistent GUI logs and Python fatal trace logs under `~/Library/Logs/ZorinMacBridge/` give usable diagnostics if the process aborts before the UI can display an exception.
4. DriverKit relative mouse state begins from the first remote sample instead of an arbitrary `(0.5, 0.5)` pseudo-position and resets the reference when active display identity changes. This removes a first-event jump; DriverKit protocol v7 remains relative and cannot guarantee exact absolute cursor placement, especially on multi-display setups.

## Test boundary

Host-side Python/shell/YAML/source regression checks are not proof that macOS 27 can compile or run the changed Swift dylib. The `SIGSEGV` and `SIGABRT` reproductions have not been executed on physical macOS in this environment. An actual screen-to-cursor alignment guarantee would require a separate tested absolute pointer mechanism; do not claim it is implemented.

## Preserved interfaces

No changes to virtual-HID daemon protocol, driver activation, pairing secrets, TLS, wake, frame wire protocol, or on-disk pairing state.
