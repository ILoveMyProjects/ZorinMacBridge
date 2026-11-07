from __future__ import annotations

import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
core_text = (root / 'linux_client.py').read_text(encoding='utf-8')
gtk_text = (root / 'linux_client_gtk.py').read_text(encoding='utf-8')

# The exact 0.13/0.14.0 regression: GTK dispatched an update result to a
# method that did not exist. Parse the class so this cannot silently return.
tree = ast.parse(gtk_text)
window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ClientWindow')
methods = {node.name for node in window.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
assert '_show_update_result' in methods

# The update event must carry a generation token and clear the busy flag before
# presenting the result. This also ensures late watchdog results are discardable.
assert "self.ui_queue.put(('update', token, info))" in core_text
assert "self.ui_queue.put(('update_error', token, str(exc)))" in core_text
assert "self.ui_queue.put(('update_timeout', watchdog_token, message))" in core_text
assert 'self._update_check_busy = True' in core_text
assert 'self.root.after(15000, watchdog)' in core_text
assert "check_for_updates(timeout=8.0)" in core_text

assert "elif kind == 'update':" in gtk_text
assert 'if token != self._update_check_serial:' in gtk_text
assert 'self._update_check_busy = False' in gtk_text
assert 'self._show_update_result(info)' in gtk_text
assert "elif kind == 'update_timeout':" in gtk_text

# A bad queue item must not remove the GLib periodic callback.
tick = gtk_text.split('    def _gtk_tick(self):', 1)[1].split('    def _process_gtk_queue', 1)[0]
assert 'try:' in tick
assert 'self._process_gtk_queue()' in tick
assert 'except Exception as exc:' in tick
assert 'return GLib.SOURCE_CONTINUE' in tick

# Ensure every self._show_* call in the GTK class resolves to a method in that
# same class or its known CoreClient base. The historical missing method is the
# important one; this static guard catches future repetitions of that pattern.
core_tree = ast.parse(core_text)
core_cls = next(node for node in core_tree.body if isinstance(node, ast.ClassDef) and node.name == 'ClientApp')
core_methods = {node.name for node in core_cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
calls = set()
for node in ast.walk(window):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        value = node.func.value
        if isinstance(value, ast.Name) and value.id == 'self' and node.func.attr.startswith('_show_'):
            calls.add(node.func.attr)
missing = sorted(calls - methods - core_methods)
assert not missing, f'GTK calls missing _show_* methods: {missing}'

assert (root / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.0'
print('update UI regression tests: OK')
