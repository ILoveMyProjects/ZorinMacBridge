"""Regression: 0.15.49 DEB omitted zmb_scroll_input despite passing checkout import."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
install_block = re.search(r'install -m 0644 linux_client_gtk\.py(.*?)"\$APP/"', workflow, re.DOTALL)
assert install_block, 'Linux DEB file-install list not found'
modules = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\.py\b', install_block.group()))
assert 'zmb_scroll_input.py' in modules, 'scroll module omitted from Linux DEB install command'
assert 'scripts/verify-linux-deb-staging.py' in workflow, 'staged/extracted package verifier missing'
assert 'SMOKE_CWD=' in workflow, 'import smoke test still runs inside source checkout'
assert 'extracted Linux DEB import OK' in workflow, 'final DEB isolated import test missing'
with tempfile.TemporaryDirectory() as tmp:
    stage = Path(tmp)
    for name in modules:
        shutil.copyfile(ROOT / name, stage / name)
    shutil.copyfile(ROOT / 'VERSION', stage / 'VERSION')
    verifier = ROOT / 'scripts' / 'verify-linux-deb-staging.py'
    ok = subprocess.run([sys.executable, str(verifier), str(stage)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    (stage / 'zmb_scroll_input.py').unlink()
    bad = subprocess.run([sys.executable, str(verifier), str(stage)], capture_output=True, text=True)
    assert bad.returncode != 0 and 'zmb_scroll_input.py is not staged' in bad.stderr, bad.stderr
print('Linux DEB packaging 0.15.50 regression: OK')
