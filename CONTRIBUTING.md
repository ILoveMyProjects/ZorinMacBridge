# Contributing

Thanks for helping improve ZorinMacBridge.

## Development setup

Linux development environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python test_protocol.py
python test_support.py
```

Before opening a pull request:

```bash
python -m compileall -q .
python test_protocol.py
python test_support.py
```

## Project principles

Changes should preserve these properties unless the change is explicitly discussed and documented:

1. Runtime must not require a vendor cloud service.
2. The server must not make unexpected outbound network connections.
3. Public IP connections remain blocked by default.
4. Remote filesystem access remains restricted to the configured share root.
5. Security-sensitive behavior must be visible in source and documentation.
6. No hidden persistence, autostart, telemetry, or remote installation.

## Pull requests

Keep changes focused. Describe:

- the problem being solved;
- protocol or security implications;
- platforms tested;
- manual macOS testing performed, if applicable;
- any new dependency and why it is necessary.

New protocol behavior should include automated tests where possible.
