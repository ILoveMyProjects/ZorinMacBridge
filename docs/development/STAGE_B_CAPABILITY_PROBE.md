# Stage B — LoginWindow capability proof

Stage B is a diagnostic milestone, not another remote-desktop workaround. Its job is to prove on the physical Mac, inside the actual `LoginWindow` launchd session and under the exact `ZorinMacBridge Server.app` process identity, which capture and input capabilities macOS really grants.

## What it measures

The probe records the current code-signing identifier and the effective values of:

- `com.apple.developer.persistent-content-capture`
- `com.apple.developer.hid.virtual.device`

For capture it calls ScreenCaptureKit directly, captures one `CGImage` with `SCScreenshotManager`, writes `/var/tmp/zmb-capability-probe.png`, records its dimensions and SHA-256, and downsamples it to verify that the frame is not uniformly black. `CGPreflightScreenCaptureAccess()` is recorded as a diagnostic only; it is **not** used as a gate because macOS 27 has a distinct Remote Desktop privacy category without a public silent authorization query.

For input it creates a CoreHID `HIDVirtualDevice` keyboard and dispatches only a left-Shift down/up report. It proves delivery by checking that the combined session modifier state toggles and returns to normal. It never types text and never sends a password. The existing IOHID/Quartz path is also measured as a comparison.

## How it runs

The native code is a separate `libzmb_capability_probe.dylib`, but it is loaded into the existing frozen server process through `--capability-probe`. This is intentional: a standalone helper would have a different TCC/code-signing identity and could produce a false Screen Recording result.

The restricted P0 command `zmb-admin capability-probe` creates a one-shot `LoginWindow` LaunchAgent for the installed app, waits for the structured result, then removes the temporary job. The probe refuses to run unless `/dev/console` is currently owned by `loginwindow`/root.

The structured output is stored at:

- `/var/tmp/zmb-capability-probe.result`
- `/var/tmp/zmb-capability-probe.png` when capture succeeds
- `/var/tmp/zmb-capability-probe.out`
- `/var/tmp/zmb-capability-probe.err`

Files are handed back to the configured target user with mode `0600` where possible.

## Running remotely from Zorin

With the paired System profile already installed, a source checkout can run:

```bash
PYTHONPATH=. python3 - <<'PY'
from zmb_admin_backend import list_profiles, run_action_for_profile
profiles = list_profiles()
if len(profiles) != 1:
    raise SystemExit(f"expected one paired Mac, found {len(profiles)}")
result = run_action_for_profile(profiles[0], "capability-probe", timeout=45)
print(result.stdout, end="")
if result.stderr:
    print(result.stderr, end="")
raise SystemExit(result.returncode)
PY
```

The Mac must currently be showing LoginWindow. The command does not require a GUI user session and does not type the login password.

## Interpretation

`overall=PASS` means a non-uniform LoginWindow frame was captured and at least one input path toggled Shift in the LoginWindow session.

`overall=CAPTURE_ONLY` means the image path is proven but input is not. If `entitlement_hid_virtual_device=0`, Stage C must obtain/configure the CoreHID virtual-device entitlement before treating CoreHID failure as an implementation bug.

`capture_result=BLOCKED_MISSING_ENTITLEMENT` or a ScreenCaptureKit missing-entitlements error means Stage C must address Persistent Content Capture signing/provisioning.

`capture_result=BLOCKED_PERMISSION` means the current app identity lacks the privacy authorization required by ScreenCaptureKit.

`capture_result=FAIL_BLACK_OR_UNIFORM` means the API returned a frame object but not a usable LoginWindow image.

`input_legacy_result=UNCONFIRMED_DELIVERY` means the legacy IOHID/Quartz call returned success but the session modifier state did not prove delivery; it must not be treated as working login input.

Stage B does not change the normal Desktop streamer, LoginWindow streamer, authentication, reconnect, handoff, or input runtime paths.
