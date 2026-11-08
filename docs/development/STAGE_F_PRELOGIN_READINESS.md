> **0.15.19 correction:** paid Developer ID/provisioning is no longer a READY requirement. READY is based on the pre-login agent plus a current physical capability proof for the installed build.

# Stage F — Pre-login readiness contract

Stage F changes only the definition and reporting of **READY**. It does not alter the Stage D capture/input/handoff runtime.

A Mac is not fully unattended-ready until all normal requirements pass **and** the pre-login path itself is proven:

1. `/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist` exists and has the expected LoginWindow contract (`LimitLoadToSessionType=LoginWindow`, `RunAtLoad`, `KeepAlive`, installed app binary, `--prelogin-server`).
2. `/Applications/ZorinMacBridge Server.app` verifies as the Stage C Developer ID/provisioned release with both restricted capabilities.
3. Stage B `capability-probe` has produced `overall=PASS` for the exact installed build. The persistent proof stores the app version and code-signing CDHash and must also contain `capture_result=PASS` plus either CoreHID or legacy input `PASS`.

The proof lives at:

`/Library/Application Support/ZorinMacBridge/Admin/prelogin-capability-status`

It contains no password, key material or screenshot. A new probe attempt removes the old proof before testing. App updates and re-signing naturally invalidate the old proof because the current app version/CDHash no longer matches.

`zmb-admin status` exposes the proof state as `prelogin_probe_present`, `prelogin_probe_current`, `prelogin_probe_ready`, `prelogin_probe_overall`, `prelogin_probe_capture`, `prelogin_probe_input_corehid`, and `prelogin_probe_input_legacy`.

The macOS GUI and terminal setup use the same `mac_requirements.py` policy, so neither may emit READY/`SETUP_OK=1` while any pre-login requirement is missing.
