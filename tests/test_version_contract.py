from __future__ import annotations

import re
from pathlib import Path

from updates import current_version

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r'^\d+\.\d+\.\d+$')


def main():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    assert SEMVER.fullmatch(version), version
    assert current_version() == version
    # Behavioral tests must not pin themselves to the current release number.
    # A release bump should not require editing dozens of unrelated tests.
    for test_file in (ROOT / 'tests').glob('test_*.py'):
        if test_file.name == Path(__file__).name:
            continue
        text = test_file.read_text(encoding='utf-8')
        assert f"== '{version}'" not in text, f'hardcoded current VERSION in {test_file.name}'
        assert f'== "{version}"' not in text, f'hardcoded current VERSION in {test_file.name}'
    print('version contract tests: OK')


if __name__ == '__main__':
    main()
