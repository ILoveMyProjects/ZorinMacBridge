from __future__ import annotations

SERVICE = 'ZorinMacBridge'


def _keyring():
    import keyring
    return keyring


def get_password(server_id: str) -> str | None:
    try:
        return _keyring().get_password(SERVICE, str(server_id))
    except Exception:
        return None


def set_password(server_id: str, password: str) -> bool:
    try:
        _keyring().set_password(SERVICE, str(server_id), password)
        return True
    except Exception:
        return False


def delete_password(server_id: str) -> None:
    try:
        _keyring().delete_password(SERVICE, str(server_id))
    except Exception:
        pass
