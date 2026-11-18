"""Regression checks for GTK scroll dispatch and targeting on Aqua.

No macOS runtime is available here: the CoreGraphics calls are deliberately mocked.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch
import sys
import struct
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mac_server
from protocol import SCROLL, pack_packet
from zmb_scroll_input import ScrollAccumulator


class FakeQuartz:
    def __init__(self):
        self.created = []
        self.locations = []
    def CGEventCreateMouseEvent(self, *args):
        return 45
    def CGEventCreateScrollWheelEvent(self, *args):
        self.created.append(args)
        return 123
    def CGEventSetLocation(self, event, position):
        self.locations.append((event, position.x, position.y))


cg = FakeQuartz()
inp = mac_server.MacInput.__new__(mac_server.MacInput)
inp.cg = cg
inp.held_buttons = set()
inp.held_modifiers = set()
inp._remote_cursor_position = None
inp._remote_cursor_display = None
posted = []
inp._post = lambda event, **kwargs: posted.append((event, dict(kwargs)))
inp._bounds = lambda: mac_server.CGRect(mac_server.CGPoint(20, 40), mac_server.CGSize(200, 100))
inp._cursor_location = lambda: mac_server.CGPoint(999, 999)
with patch.object(mac_server, 'active_input_display', return_value=42):
    inp.move(.5, .5)
    inp.scroll(0, -3)
assert cg.locations == [], cg.locations
assert posted[-1] == (123, {'tap': mac_server.MacInput.SESSION_TAP}), posted
assert mac_server.MacInput.SESSION_TAP == 1
# Scroll routing follows the current session pointer; changing display does not
# synthesize a separate wheel-event location.
with patch.object(mac_server, 'active_input_display', return_value=84):
    inp.scroll(0, -2)
assert cg.locations == [], cg.locations
assert posted[-1][1].get('tap') == 1

# Exercise actual GTK handler body with a fake window without loading libadwaita.
module = ast.parse((ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8'))
methods = [node for node in ast.walk(module) if isinstance(node, ast.FunctionDef) and node.name == '_gtk_scroll']
assert len(methods) == 1
fn = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
namespace = {'time': time, 'pack_packet': pack_packet, 'SCROLL': SCROLL}
exec(compile(fn, '<gtk_scroll>', 'exec'), namespace)
handler = namespace['_gtk_scroll']
class FakeCapture:
    def get(self):
        return True
class FakeClient:
    connected = True
    prelogin_session = False
    capture_input_var = FakeCapture()
    _last_scroll_tx_log_at = -99999.0
    _scroll_accumulator = ScrollAccumulator()
    def __init__(self):
        self.packets = []
        self.logs = []
    def _remote_input_ready(self):
        return True
    def _enqueue(self, data):
        self.packets.append(data)
    def _log(self, *data):
        self.logs.append(data)
client = FakeClient()
assert handler(client, None, 0, 1) is True
assert client.packets == [pack_packet(SCROLL, struct.pack('!ii', 0, -3))]
assert any('Mouse scroll GTK RX' in text for _, text in client.logs)

source = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
assert 'scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)' in source
assert 'self.picture.add_controller(scroll)' in source
print('0.15.50 scroll delivery regression: OK')
