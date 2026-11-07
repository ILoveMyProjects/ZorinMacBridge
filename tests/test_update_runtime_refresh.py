from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / 'zmb_system_page.py').read_text(encoding='utf-8')

assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.6'
assert 'elif action == "update-now":' in PAGE
assert 'def refresh_after_update():' in PAGE
assert 'discover = getattr(self.window, "discover_macs", None)' in PAGE
assert 'if callable(discover):' in PAGE
assert 'discover()' in PAGE
assert 'GLib.timeout_add(1800, refresh_after_update)' in PAGE

print('post-update runtime/discovery refresh regression tests: OK')
