> **0.15.20 correction:** Stage F originally made the LoginWindow agent and capability proof part of global READY. That was too strict. From 0.15.20, normal Aqua READY and reboot-to-LoginWindow verification are separate states.

# Stage F — Pre-login verification contract

Stage F does not alter the Stage D capture/input/handoff runtime. It records whether the reboot-to-LoginWindow path has been proven for the exact installed Mac app build.

Normal Aqua READY is evaluated independently from pre-login verification. A logged-in Mac can be READY for Desktop, Files, System, Terminal and Projects without signing out or rebooting just to run a capability probe.

Pre-login verification checks:

1. `/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist` has the expected LoginWindow contract.
2. Stage B `capability-probe` has produced `overall=PASS` for the exact installed build, including `capture_result=PASS` and either CoreHID or legacy input `PASS`.

The proof lives at:

`/Library/Application Support/ZorinMacBridge/Admin/prelogin-capability-status`

It contains no password, key material or screenshot. A new probe attempt removes the old proof before testing. App updates/re-signing invalidate the previous proof when version/CDHash changes.

The proof is **Mac/build-scoped, not client-scoped**. One successful capability probe launched from any paired Zorin client verifies that Mac/build for all paired clients.

`zmb-admin status` exposes the proof fields as `prelogin_probe_present`, `prelogin_probe_current`, `prelogin_probe_ready`, `prelogin_probe_overall`, `prelogin_probe_capture`, `prelogin_probe_input_corehid`, and `prelogin_probe_input_legacy`.

UI policy from 0.15.20:

- normal requirements missing -> global REQUIRED / not READY;
- normal requirements satisfied -> global READY;
- pre-login agent + proof current -> `Pre-login after reboot: VERIFIED`;
- agent present but proof missing/stale/failed -> `UNVERIFIED`;
- agent contract missing/broken -> `NOT READY`;
- none of these pre-login states blocks normal Aqua READY.
