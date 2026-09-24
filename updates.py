from __future__ import annotations

import json
import re
import urllib.request
import webbrowser
from dataclasses import dataclass

from resources import resource_path

REPOSITORY = 'ILoveMyProjects/ZorinMacBridge'
LATEST_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases/latest'


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    available: bool
    page_url: str


def current_version() -> str:
    try:
        return resource_path('VERSION').read_text(encoding='utf-8').strip()
    except Exception:
        return '0.0.0'


def _version_tuple(value: str) -> tuple[int, ...]:
    value = value.strip().lstrip('vV')
    match = re.match(r'^(\d+(?:\.\d+)*)', value)
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split('.'))


def check_for_updates(timeout: float = 8.0) -> UpdateInfo:
    """Perform an explicit, user-initiated GitHub release check.

    This is intentionally never called in the background or at application start.
    """
    request = urllib.request.Request(
        LATEST_API,
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': f'ZorinMacBridge/{current_version()}',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    latest = str(data.get('tag_name') or '').strip().lstrip('vV')
    if not latest:
        raise RuntimeError('GitHub did not return a release version.')
    page = str(data.get('html_url') or RELEASES_URL)
    current = current_version()
    return UpdateInfo(
        current=current,
        latest=latest,
        available=_version_tuple(latest) > _version_tuple(current),
        page_url=page,
    )


def open_release_page(url: str | None = None) -> None:
    webbrowser.open(url or RELEASES_URL)
