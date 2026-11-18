#!/usr/bin/env python3
"""Fail if a staged/extracted Linux client omits required local Python modules.

Only module-scope imports are checked: platform-specific imports in lazily
called macOS updater functions do not belong in a Linux package.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def module_imports(statements):
    """Walk executable module-level statements, not function/class bodies."""
    for statement in statements:
        if isinstance(statement, ast.Import):
            for item in statement.names:
                yield item.name.split('.', 1)[0]
        elif isinstance(statement, ast.ImportFrom) and statement.module and statement.level == 0:
            yield statement.module.split('.', 1)[0]
        elif isinstance(statement, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            yield from module_imports(statement.body)
            yield from module_imports(getattr(statement, 'orelse', []))
            yield from module_imports(getattr(statement, 'finalbody', []))
            for handler in getattr(statement, 'handlers', []):
                yield from module_imports(handler.body)


def verify(stage: Path, source: Path) -> list[str]:
    problems = []
    local_names = {file.stem for file in source.glob('*.py')}
    if not (stage / 'VERSION').is_file():
        problems.append('Missing VERSION in staged Linux package')
    if not (stage / 'linux_client_gtk.py').is_file():
        problems.append('Missing Linux GTK entry point in staged package')
    for module_path in sorted(stage.glob('*.py')):
        try:
            imports = set(module_imports(ast.parse(module_path.read_text(encoding='utf-8')) .body))
        except (OSError, SyntaxError, UnicodeError) as exc:
            problems.append(f'{module_path.name}: cannot inspect imports: {exc}')
            continue
        for module in sorted(imports & local_names):
            if not (stage / (module + '.py')).is_file():
                problems.append(f'{module_path.name}: required local module {module}.py is not staged')
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print('Usage: verify-linux-deb-staging.py STAGED_APP_DIRECTORY', file=sys.stderr)
        return 2
    staged = Path(sys.argv[1]).resolve()
    source = Path(__file__).resolve().parents[1]
    problems = verify(staged, source)
    if problems:
        for problem in problems:
            print(f'ERROR: {problem}', file=sys.stderr)
        return 1
    print(f'Linux DEB first-party module staging OK ({len(list(staged.glob("*.py")))} modules)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
