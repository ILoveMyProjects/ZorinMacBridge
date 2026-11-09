> **0.15.20 correction:** normal Aqua READY and reboot-to-LoginWindow verification are separate states.

# Stage F — Pre-login verification contract

Stage F records whether the reboot-to-LoginWindow path has been proven for the exact installed Mac app build. Normal Aqua READY remains independent.

Pre-login verification checks:

1. `/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist` has the expected LoginWindow contract.
2. The installed app has the managed Developer ID/provisioning identity required by the pre-login capability tier.
3. Stage B `capability-probe` has produced `overall=PASS` for the exact installed build, including `capture_result=PASS` and `input_corehid_result=PASS`.

Starting with 0.15.27, `input_corehid_result=PASS` means both the production CoreHID keyboard proof and production absolute-pointer proof passed. Legacy IOHID/Quartz is comparison data only and cannot satisfy readiness.

The proof lives at:

`/Library/Application Support/ZorinMacBridge/Admin/prelogin-capability-status`

It contains no password, key material or screenshot. A new probe attempt removes the old proof before testing. App updates/re-signing invalidate the previous proof when version/CDHash changes.

The proof is **Mac/build-scoped, not client-scoped**. One successful capability probe launched from any paired Zorin client verifies that Mac/build for all paired clients.

`zmb-admin status` exposes the proof fields as `prelogin_probe_present`, `prelogin_probe_current`, `prelogin_probe_ready`, `prelogin_probe_overall`, `prelogin_probe_capture`, `prelogin_probe_input_corehid`, and `prelogin_probe_input_legacy`.

UI policy:

- normal requirements missing -> global REQUIRED / not READY;
- normal requirements satisfied -> global READY;
- managed identity + LoginWindow agent + current production-aligned proof -> `Pre-login after reboot: VERIFIED`;
- proof missing/stale/failed -> `UNVERIFIED`;
- managed identity or agent contract missing/broken -> `NOT READY`;
- none of these pre-login states blocks normal Aqua READY.
