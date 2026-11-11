# Stage B — LoginWindow capability proof

Stage B proves on the physical Mac, inside the actual `LoginWindow` launchd session and under the exact `ZorinMacBridge Server.app` process identity, that the same native mechanisms used by the production pre-login runtime are accepted by macOS.

## What it measures

The probe records the current code-signing identifier and the effective values of:

- `com.apple.developer.persistent-content-capture`
- `com.apple.developer.hid.virtual.device`

For capture it creates a real ScreenCaptureKit `SCStream`, waits for the first `.started`/`.complete` screen sample with bounded startup/first-frame deadlines, converts that frame to a `CGImage`, writes `/var/tmp/zmb-capability-probe.png`, records dimensions/SHA-256, and rejects a uniformly black frame. This mirrors the production LoginWindow video backend introduced in 0.15.27. `CGPreflightScreenCaptureAccess()` remains diagnostic only.

For input it calls the same `ZMBCoreHIDInput.swift` implementation compiled into the production streamer. The probe creates the production CoreHID virtual keyboard and absolute pointer, proves a harmless left-Shift down/up transition, moves the pointer a small bounded distance, verifies that movement, then restores the pointer. It never types text and never sends a password. Starting with 0.15.30 the IOHID/Quartz path is also the production fallback when CoreHID cannot be created. The probe continues to report it separately because CoreHID and fallback delivery have different guarantees.

## How it runs

The native probe is `libzmb_capability_probe.dylib`, loaded into the existing frozen server process through `--capability-probe`. A standalone helper would have a different TCC/code-signing identity and could produce a false result.

The restricted P0 command `zmb-admin capability-probe` creates a one-shot `LoginWindow` LaunchAgent for the installed app, waits for the structured result, then removes the temporary job. The probe refuses to run unless `/dev/console` is currently owned by `loginwindow`/root.

The structured output is stored at:

- `/var/tmp/zmb-capability-probe.result`
- `/var/tmp/zmb-capability-probe.png` when capture succeeds
- `/var/tmp/zmb-capability-probe.out`
- `/var/tmp/zmb-capability-probe.err`

Files are handed back to the configured target user with mode `0600` where possible.

## Interpretation

`overall=PASS` means all production prerequisites proved on LoginWindow for that exact build:

- `capture_result=PASS` — actual `SCStream` produced a usable first frame;
- `input_corehid_keyboard_result=PASS` — production CoreHID keyboard report was observed;
- `input_corehid_pointer_result=PASS` — production CoreHID pointer movement was observed/restored;
- `input_corehid_result=PASS` — both keyboard and pointer passed.

`overall=CAPTURE_ONLY` means video passed but production input did not.

`capture_result=BLOCKED_MISSING_ENTITLEMENT` or a ScreenCaptureKit missing-entitlements error is a signing/provisioning blocker for Persistent Content Capture.

`capture_result=BLOCKED_PERMISSION` means the installed app identity lacks the privacy authorization required by ScreenCaptureKit.

`capture_result=FAIL_BLACK_OR_UNIFORM` means ScreenCaptureKit delivered a frame object but not a usable LoginWindow image.

`input_legacy_result` remains separate from managed CoreHID readiness. A PASS is useful evidence for the production fallback, while managed `input_corehid_result=PASS` remains the stricter VERIFIED readiness signal.

Starting with 0.15.27, Stage B and the production LoginWindow runtime intentionally share the same ScreenCaptureKit/CoreHID mechanism class; the probe is no longer a proof of a different fallback path.
