from __future__ import annotations

import socket
import threading
import time

from zmb_session_lifecycle import (
    HANDOFF_REASON_PREFIX,
    PIPELINE_STAGES,
    PipelineProgress,
    PreloginSessionCoordinator,
    make_capture_backend_state,
    make_handoff_state,
    make_pipeline_state,
    parse_session_state,
    reconnect_delay_seconds,
)


class FakeSock:
    def __init__(self):
        self.shutdowns = 0

    def shutdown(self, how):
        assert how == socket.SHUT_RDWR
        self.shutdowns += 1


def test_single_owner_replacement_and_video_token():
    coordinator = PreloginSessionCoordinator(replacement_grace_seconds=0.5)
    first_sock = FakeSock()
    second_sock = FakeSock()
    first = coordinator.acquire_control(first_sock, '192.168.1.10', log=lambda _m: None)
    result = {}

    def acquire_second():
        result['lease'] = coordinator.acquire_control(second_sock, '192.168.1.10', log=lambda _m: None)

    worker = threading.Thread(target=acquire_second)
    worker.start()
    deadline = time.monotonic() + 0.5
    while first_sock.shutdowns == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert first_sock.shutdowns == 1
    assert worker.is_alive(), 'replacement must wait until the old control owner releases'
    assert coordinator.release_control(first, log=lambda _m: None)
    worker.join(timeout=1.0)
    assert not worker.is_alive()

    second = result['lease']
    assert second.generation == first.generation + 1
    assert second.token != first.token
    assert coordinator.validate_video_token(second.token, '192.168.1.10') is second
    for token, peer in (('wrong-token', '192.168.1.10'), (second.token, '192.168.1.11')):
        try:
            coordinator.validate_video_token(token, peer)
        except PermissionError:
            pass
        else:
            raise AssertionError('invalid pre-login video binding must be rejected')
    assert coordinator.release_control(second, log=lambda _m: None)


def test_session_state_round_trip():
    payload = make_handoff_state(user='dev', generation=9)
    event = parse_session_state(payload)
    assert event.kind == 'handoff'
    assert event.target == 'aqua'
    assert event.user == 'dev'
    assert event.generation == 9

    for stage in PIPELINE_STAGES[:-1]:
        event = parse_session_state(make_pipeline_state(stage))
        assert event.kind == 'pipeline'
        assert event.stage == stage

    backend = parse_session_state(make_capture_backend_state('scstream'))
    assert backend.kind == 'capture-backend'
    assert backend.backend == 'scstream'

    future = make_pipeline_state('future-native-stage')
    assert future == {'state': 'pipeline', 'stage': 'future-native-stage'}
    assert parse_session_state(future).kind == 'unknown'
    assert parse_session_state({'state': 'handoff', 'target': 'other'}).kind == 'unknown'
    assert parse_session_state(None).kind == 'unknown'


def test_pipeline_progress_reports_missing_and_regression():
    progress = PipelineProgress()
    first = progress.observe('capture-frame')
    assert first.previous_stage is None and not first.missing and not first.regression
    skipped = progress.observe('encode-submit')
    assert skipped.missing == ('pixel-buffer',)
    assert progress.stage == 'encode-submit'
    regressed = progress.observe('pixel-buffer')
    assert regressed.regression is True
    assert progress.stage == 'encode-submit'
    progress.observe('h264-written')
    progress.observe('client-decoded')
    assert progress.complete is True


def test_reconnect_policy_is_deterministic():
    assert reconnect_delay_seconds(1, HANDOFF_REASON_PREFIX) == 0.25
    assert reconnect_delay_seconds(6, HANDOFF_REASON_PREFIX + ' user=dev') == 0.25
    assert [reconnect_delay_seconds(i, 'ConnectionError') for i in range(1, 7)] == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def main():
    test_single_owner_replacement_and_video_token()
    test_session_state_round_trip()
    test_pipeline_progress_reports_missing_and_regression()
    test_reconnect_policy_is_deterministic()
    print('shared session lifecycle tests: OK')


if __name__ == '__main__':
    main()
