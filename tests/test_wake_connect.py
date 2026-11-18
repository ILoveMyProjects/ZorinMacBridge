from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
from discovery import desktop_connect_strategy

assert desktop_connect_strategy(desktop_available=True, system_available=True) == "connect"
assert desktop_connect_strategy(desktop_available=True, system_available=False, paired_only=True) == "connect"
assert desktop_connect_strategy(desktop_available=False, system_available=True) == "recover-online"
assert desktop_connect_strategy(desktop_available=False, system_available=False) == "wake"
assert desktop_connect_strategy(desktop_available=False, system_available=True, paired_only=True) == "wake"

backend_path = ROOT / "zmb_admin_backend.py"
spec = importlib.util.spec_from_file_location("zmb_admin_backend_wake_test", backend_path)
backend = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(backend)

assert backend.normalize_mac_address("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
assert backend.normalize_mac_address("bad") == ""

class FakeSocket:
    sent = []
    def setsockopt(self, *args):
        pass
    def settimeout(self, *args):
        pass
    def sendto(self, payload, target):
        self.sent.append((payload, target))
        return len(payload)
    def close(self):
        pass

old_socket = backend.socket.socket
try:
    backend.socket.socket = lambda *a, **k: FakeSocket()
    assert backend.send_wake_on_lan("AA:BB:CC:DD:EE:FF", rounds=1) is True
finally:
    backend.socket.socket = old_socket

assert FakeSocket.sent
payload, target = FakeSocket.sent[0]
assert payload[:6] == b"\xff" * 6
assert payload[6:] == bytes.fromhex("AABBCCDDEEFF") * 16
assert len(payload) == 102
assert target[1] in {7, 9}

gtk = (ROOT / "linux_client_gtk.py").read_text(encoding="utf-8")
assert "def _wake_sleeping_mac_then_connect" in gtk
assert "send_wake_on_lan(mac, rounds=5)" in gtk
assert "Mac woke up — connecting Desktop…" in gtk
assert "self.selected_paired_only = False" in gtk
assert "clearing saved/offline state" in gtk
assert "if self._wake_sleeping_mac_then_connect():" in gtk
assert "desktop_connect_strategy(" in gtk
assert "if strategy == 'recover-online':" in gtk
assert "using session recovery instead of Wake-on-LAN" in gtk
assert "automatic login" not in gtk.lower()

admin = (ROOT / "native/macos/admin/zmb-admin").read_text(encoding="utf-8")
assert "wake_mac_address()" in admin
assert "wake_mac_address=%s" in admin
assert "sysadminctl -autologin" not in admin

print("wake-on-connect regression tests: OK")
