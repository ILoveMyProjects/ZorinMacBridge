#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

from zmb_video_policy import ADAPTIVE_TIERS, choose_adaptive_step, initial_tier_for_viewport, profile_for_tier

ROOT = Path(__file__).resolve().parent


def test_policy() -> None:
    assert len(ADAPTIVE_TIERS) == 4
    assert initial_tier_for_viewport(900) == 0
    assert initial_tier_for_viewport(1300) == 1
    assert initial_tier_for_viewport(1800) == 2
    assert initial_tier_for_viewport(3000) == 3
    assert profile_for_tier(-10)[0] == 'Eco'
    assert profile_for_tier(99)[0] == 'Sharp'
    assert choose_adaptive_step(decoded=100, dropped=20, stable_windows=0, current_tier=2, viewport_width=2500) == -1
    assert choose_adaptive_step(decoded=100, dropped=0, stable_windows=3, current_tier=1, viewport_width=2500) == 0
    assert choose_adaptive_step(decoded=100, dropped=0, stable_windows=4, current_tier=1, viewport_width=2500) == 1
    assert choose_adaptive_step(decoded=100, dropped=0, stable_windows=99, current_tier=3, viewport_width=1200) == -1


def test_protocol_and_source_guards() -> None:
    protocol = (ROOT / 'protocol.py').read_text()
    assert 'VIDEO_STATS = 12' in protocol
    client = (ROOT / 'linux_client.py').read_text()
    assert "'Adaptive'" in client
    assert 'queue.Queue(maxsize=1)' in client
    assert '_video_reconfigure_event' in client
    server = (ROOT / 'mac_server.py').read_text()
    assert 'TCP_NODELAY' in server
    assert 'VIDEO_STATS' in server
    swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text()
    assert 'config.queueDepth = 2' in swift
    assert 'writePending' in swift
    assert 'MaxFrameDelayCount' in swift


def test_python_syntax() -> None:
    for name in ('linux_client.py', 'linux_client_gtk.py', 'mac_server.py', 'protocol.py', 'zmb_video_policy.py'):
        ast.parse((ROOT / name).read_text(), filename=name)


if __name__ == '__main__':
    test_policy()
    test_protocol_and_source_guards()
    test_python_syntax()
    print('P5 tests: PASS')
