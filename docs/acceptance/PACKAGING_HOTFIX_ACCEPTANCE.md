# 0.14.16 PackagingHotfix acceptance

## Automated

- `scripts/run-tests.sh`
- `python3 -m compileall -q .`
- shell syntax checks
- `scripts/verify-release-source.sh`
- release workflow contains `zmb_shutdown.py` in the Linux package manifest
- release workflow smoke-imports `linux_client_gtk` from the staged `/usr/lib/zorinmacbridge` tree
- generated DEB layout verification checks `usr/lib/zorinmacbridge/zmb_shutdown.py`

## Real Zorin acceptance

1. Install/update to 0.14.16 from the release `.deb`.
2. Choose **Restart now** after an in-app update.
3. Confirm the old UI closes and the new client UI opens again.
4. Run `/usr/bin/zorinmacbridge` from a terminal and confirm there is no `ModuleNotFoundError: zmb_shutdown`.
