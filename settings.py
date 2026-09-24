from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path.home() / '.zorin-mac-bridge'
SERVER_CONFIG = APP_DIR / 'server-settings.json'
CLIENT_CONFIG = Path.home() / '.config' / 'zorinmacbridge' / 'clients.json'
PBKDF2_ITERATIONS = 350_000


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def load_server_settings() -> dict:
    return _read_json(SERVER_CONFIG)


def update_server_settings(**updates) -> dict:
    data = load_server_settings()
    data.update(updates)
    _write_json(SERVER_CONFIG, data)
    return data


def persistent_server_id() -> str:
    data = load_server_settings()
    value = str(data.get('server_id', '')).strip()
    if value:
        return value
    value = str(uuid.uuid4())
    data['server_id'] = value
    _write_json(SERVER_CONFIG, data)
    return value


@dataclass(frozen=True)
class PasswordVerifier:
    salt: bytes
    digest: bytes
    iterations: int = PBKDF2_ITERATIONS

    @classmethod
    def from_password(cls, password: str, *, iterations: int = PBKDF2_ITERATIONS) -> 'PasswordVerifier':
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
        return cls(salt=salt, digest=digest, iterations=iterations)

    @classmethod
    def from_settings(cls, data: dict) -> 'PasswordVerifier | None':
        try:
            auth = data['password_verifier']
            return cls(
                salt=base64.b64decode(auth['salt'], validate=True),
                digest=base64.b64decode(auth['digest'], validate=True),
                iterations=int(auth['iterations']),
            )
        except Exception:
            return None

    def verify(self, password: str) -> bool:
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), self.salt, self.iterations)
        return hmac.compare_digest(actual, self.digest)

    def to_json(self) -> dict:
        return {
            'salt': base64.b64encode(self.salt).decode('ascii'),
            'digest': base64.b64encode(self.digest).decode('ascii'),
            'iterations': self.iterations,
            'algorithm': 'pbkdf2-hmac-sha256',
        }


def save_server_password(password: str) -> PasswordVerifier:
    verifier = PasswordVerifier.from_password(password)
    update_server_settings(password_verifier=verifier.to_json())
    return verifier


def load_server_password_verifier() -> PasswordVerifier | None:
    return PasswordVerifier.from_settings(load_server_settings())


def load_clients() -> dict:
    return _read_json(CLIENT_CONFIG)


def save_client_record(server_id: str, *, name: str, ip: str, port: int, fingerprint: str) -> None:
    data = load_clients()
    data[str(server_id)] = {
        'name': str(name),
        'ip': str(ip),
        'port': int(port),
        'fingerprint': str(fingerprint),
    }
    _write_json(CLIENT_CONFIG, data)


def client_record(server_id: str) -> dict:
    return dict(load_clients().get(str(server_id), {}))
