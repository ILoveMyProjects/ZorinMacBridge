#!/usr/bin/env python3
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
CLIENT = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
GTK = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
RUNTIME = (ROOT / 'zmb_client_runtime.py').read_text(encoding='utf-8')
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

assert 'client.prelogin_input_armed = False' in RUNTIME
assert 'client.prelogin_input_ready = False' in RUNTIME
assert "self.ui_queue.put(('prelogin_input_armed', event.backend, event.result))" in CLIENT
assert "self.ui_queue.put(('prelogin_input_ready', event.backend, event.result))" in CLIENT
assert "event.result == 'OPEN_READY'" in CLIENT
assert "event.result == 'ACTIVE'" in CLIENT
assert "self.ui_queue.put(('prelogin_input_unavailable'" in CLIENT
assert "elif kind == 'prelogin_input_armed':" in GTK
assert "elif kind == 'prelogin_input_ready':" in GTK
assert "elif kind == 'prelogin_input_unavailable':" in GTK
assert 'not self.prelogin_input_armed' in GTK
assert 'waiting for LoginWindow input backend' in GTK
assert 'remote input armed' in GTK
assert 'remote input active' in GTK
assert 'set_state_callback(emit_input_state, replay=True)' in SERVER

# The old UI bug enabled input on first video frame alone. The pre-login input
# gate must now require BOTH independent readiness flags.
tree = ast.parse(GTK)
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == 'ClientWindow':
        for child in node.body:
            if isinstance(child, ast.FunctionDef) and child.name == '_remote_input_ready':
                src = ast.get_source_segment(GTK, child) or ''
                assert 'self.prelogin_video_ready' in src
                assert 'self.prelogin_input_armed' in src
                break
        else:
            raise AssertionError('ClientWindow._remote_input_ready missing')
        break
else:
    raise AssertionError('ClientWindow missing')

print('pre-login input readiness gate regression tests: OK')
