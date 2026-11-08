# Contributing

Thanks for helping improve ZorinMacBridge.

## Development setup

Linux development environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
scripts/run-tests.sh
```

Before opening a pull request:

```bash
python -m compileall -q .
scripts/run-tests.sh
```

## Project principles

Changes should preserve these properties unless the change is explicitly discussed and documented:

1. Runtime must not require a vendor cloud service.
2. The server must not make unexpected outbound network connections.
3. Public IP connections remain blocked by default.
4. Remote filesystem access remains restricted to the configured share root.
5. Security-sensitive behavior must be visible in source and documentation.
6. No hidden persistence, autostart, telemetry, or remote installation.


## Testing policy

Prefer behavioral tests over source-text assertions. If a Python function/class can be imported and exercised, test its inputs, state transitions and outputs directly. Do not hardcode the current `VERSION` in unrelated regression tests.

Source-invariant checks are acceptable for boundaries that Linux CI cannot execute directly, such as Swift/C macOS framework code, GitHub Actions wiring, package manifests and cross-language exported symbols. Those checks do not replace the required Stage A macOS native build gate or physical LoginWindow acceptance tests.

Shared connection/session policy belongs in GUI-independent modules (`zmb_session_lifecycle.py` and `zmb_client_runtime.py`) so Tk, GTK and server code use one implementation. A new shared backend field must be initialized once in the shared runtime initializer rather than duplicated across frontend constructors.

## Pull requests

Keep changes focused. Describe:

- the problem being solved;
- protocol or security implications;
- platforms tested;
- manual macOS testing performed, if applicable;
- any new dependency and why it is necessary.

New protocol behavior should include automated tests where possible.

## Stage C macOS release signing

Production macOS releases require Apple approval for both restricted capabilities and the three GitHub Actions secrets documented in `SIGNING.md`. Do not commit a `.p12`, private key, or `.provisionprofile`. A release build must fail rather than fall back to ad-hoc signing when those credentials are unavailable.

Local/source development may continue to use the legacy ad-hoc/local-DR path, but that path is not a valid production substitute for restricted entitlements.
