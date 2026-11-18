# Stage G — Tests / Refactor

## Target

Make the critical reboot-to-remote-login lifecycle testable as behavior rather than source text, without changing the intended runtime flow.

## Shared lifecycle

`zmb_session_lifecycle.py` owns the Python-level pre-login session policy:

- single control owner and replacement grace;
- per-control video token binding;
- handoff and pipeline `SESSION_STATE` payloads;
- first-frame pipeline progress;
- handoff reconnect delay.

The module has no GTK, Tk, PyAV or macOS framework dependency and can run in Linux CI.

## Shared client runtime state

`zmb_client_runtime.py` initializes fields used by the shared `CoreClient` networking implementation. Both Tk and GTK constructors call the same initializer. Frontend-only GTK state stays in `ClientWindow.__init__`.

This removes the previous failure mode where a backend field could be added to the Tk constructor but omitted from GTK.

## Test policy

Python behavior is tested by importing/executing the API. Source-string assertions are retained only for boundaries that Linux cannot execute (native Swift/C contracts, workflow/package wiring). macOS compilation remains enforced by Stage A, and physical LoginWindow capture/input remains enforced by Stage B/F acceptance.

Release-number equality assertions were removed from unrelated behavioral tests. `tests/test_version_contract.py` now checks semantic version shape and consistency with `updates.current_version()`.

## Runtime compatibility

Stage G does not intentionally change authentication, capture backend selection, input injection, update/signing, discovery, wake behavior or LoginWindow→Aqua handoff semantics. Unknown future pre-login pipeline stages remain forward-compatible: the server can forward them and older clients ignore them.
