from pathlib import Path

source = Path('mac_terminal_setup.py').read_text(encoding='utf-8')
installer = Path('install-macos.sh').read_text(encoding='utf-8')

assert "open('/dev/tty', 'r+'" in source
assert "_prompt_text('Choose [1/2/3, default 3]: ')" in source
assert "_prompt_text('A ZorinMacBridge session password is already configured. Replace it? [y/N]: ')" in source
assert 'curl -fsSL' not in installer or '--setup-assistant' in installer

print('terminal setup controlling-TTY regression: OK')
