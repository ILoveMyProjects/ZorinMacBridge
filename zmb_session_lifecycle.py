#!/usr/bin/env python3
from __future__ import annotations

"""Pure/shared session lifecycle policy for ZorinMacBridge.

This module deliberately has no GTK/Tk/PyAV/macOS framework dependencies.  It
contains the state and policy that both client and server need to agree on so
critical pre-login behavior can be exercised directly in Linux CI.
"""

import hmac
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

PRELOGIN_CONTROL_REPLACEMENT_GRACE_SECONDS = 3.0
HANDOFF_REASON_PREFIX = 'Pre-login handoff to Aqua Desktop'
PIPELINE_STAGES = (
    'capture-frame',
    'pixel-buffer',
    'encode-submit',
    'h264-written',
    'client-decoded',
)
_PIPELINE_INDEX = {stage: index for index, stage in enumerate(PIPELINE_STAGES)}


@dataclass
class PreloginControlLease:
    """Single-owner LoginWindow control lease shared by control, video and handoff."""

    token: str
    generation: int
    sock: Any
    peer_ip: str

    def __post_init__(self) -> None:
        self.handoff_event = threading.Event()
        self.handoff_sent_event = threading.Event()
        self.handoff_user = ''


class PreloginSessionCoordinator:
    """Serialize LoginWindow control/input ownership and bind video to that owner."""

    def __init__(self, *, replacement_grace_seconds: float = PRELOGIN_CONTROL_REPLACEMENT_GRACE_SECONDS) -> None:
        self._condition = threading.Condition()
        self._current: PreloginControlLease | None = None
        self._generation = 0
        self.replacement_grace_seconds = float(replacement_grace_seconds)

    @staticmethod
    def _shutdown_socket(sock, *, log=print, label: str = 'control') -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
            log(f'[prelogin/session] forced stale {label} socket shutdown')
        except OSError as exc:
            log(f'[prelogin/session] stale {label} shutdown warning: {type(exc).__name__}: {exc}')
        except Exception as exc:
            log(f'[prelogin/session] stale {label} shutdown warning: {type(exc).__name__}: {exc}')

    def acquire_control(self, sock, peer_ip: str, *, log=print) -> PreloginControlLease:
        deadline = time.monotonic() + self.replacement_grace_seconds
        stale: PreloginControlLease | None = None
        with self._condition:
            if self._current is not None:
                stale = self._current
        if stale is not None and stale.sock is not sock:
            log(
                f'[prelogin/session] replacing control generation={stale.generation} '
                f'peer={stale.peer_ip}'
            )
            self._shutdown_socket(stale.sock, log=log, label='control')
            with self._condition:
                while self._current is stale:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f'previous pre-login control owner did not release within '
                            f'{self.replacement_grace_seconds:g}s'
                        )
                    self._condition.wait(timeout=remaining)
        with self._condition:
            if self._current is not None:
                raise RuntimeError('pre-login control ownership changed while replacing stale session')
            self._generation += 1
            lease = PreloginControlLease(
                secrets.token_urlsafe(32), self._generation, sock, peer_ip,
            )
            self._current = lease
            log(
                f'[prelogin/session] control owner acquired generation={lease.generation} '
                f'peer={peer_ip}'
            )
            return lease

    def release_control(self, lease: PreloginControlLease, *, log=print) -> bool:
        with self._condition:
            if self._current is not lease:
                return False
            self._current = None
            self._condition.notify_all()
        log(
            f'[prelogin/session] control owner released generation={lease.generation} '
            f'peer={lease.peer_ip}'
        )
        return True

    def validate_video_token(self, token: str, peer_ip: str) -> PreloginControlLease:
        with self._condition:
            lease = self._current
            if lease is None:
                raise PermissionError('pre-login video requires an active Desktop control session')
            if not token or not hmac.compare_digest(token, lease.token):
                raise PermissionError('pre-login video session token does not match the active Desktop session')
            if peer_ip != lease.peer_ip:
                raise PermissionError('pre-login video peer does not match the active Desktop session')
            return lease

    def request_handoff(self, user: str, *, log=print) -> PreloginControlLease | None:
        with self._condition:
            lease = self._current
            if lease is None:
                log(f'[prelogin/handoff] Aqua user detected ({user}); no active control client')
                return None
            lease.handoff_user = user
            lease.handoff_event.set()
        log(
            f'[prelogin/handoff] Aqua user detected ({user}); notifying control '
            f'generation={lease.generation}'
        )
        return lease

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            lease = self._current
            if lease is None:
                return {'active': False, 'generation': self._generation}
            return {
                'active': True,
                'generation': lease.generation,
                'peer_ip': lease.peer_ip,
                'handoff': lease.handoff_event.is_set(),
            }


@dataclass(frozen=True)
class SessionStateEvent:
    kind: str
    stage: str = ''
    target: str = ''
    user: str = ''
    backend: str = ''
    reason: str = ''
    generation: int | None = None


def make_handoff_state(*, user: str, generation: int) -> dict[str, Any]:
    return {
        'state': 'handoff',
        'target': 'aqua',
        'user': str(user or ''),
        'generation': int(generation),
    }


def make_capture_backend_state(backend: str) -> dict[str, str]:
    return {'state': 'capture-backend', 'backend': str(backend or '')}


def make_capture_failure_state(reason: str) -> dict[str, str]:
    return {'state': 'capture-failure', 'reason': str(reason or '')}


def make_pipeline_state(stage: str) -> dict[str, str]:
    # Keep the wire protocol forward-compatible. A newer native streamer may
    # introduce a diagnostic stage before the Python client knows about it.
    # The server forwards it; parse_session_state() on an older client safely
    # classifies it as unknown instead of tearing down Video.
    return {'state': 'pipeline', 'stage': str(stage or '')}


def parse_session_state(value: Any) -> SessionStateEvent:
    if not isinstance(value, dict):
        return SessionStateEvent('unknown')
    state = str(value.get('state') or '')
    if state == 'pipeline':
        stage = str(value.get('stage') or '')
        if stage in _PIPELINE_INDEX and stage != 'client-decoded':
            return SessionStateEvent('pipeline', stage=stage)
        return SessionStateEvent('unknown')
    if state == 'capture-backend':
        backend = str(value.get('backend') or '')
        if backend:
            return SessionStateEvent('capture-backend', backend=backend)
        return SessionStateEvent('unknown')
    if state == 'capture-failure':
        reason = str(value.get('reason') or '')
        if reason:
            return SessionStateEvent('capture-failure', reason=reason)
        return SessionStateEvent('unknown')
    if state == 'handoff' and str(value.get('target') or '') == 'aqua':
        generation = value.get('generation')
        try:
            generation = int(generation) if generation is not None else None
        except (TypeError, ValueError):
            generation = None
        return SessionStateEvent(
            'handoff',
            target='aqua',
            user=str(value.get('user') or ''),
            generation=generation,
        )
    return SessionStateEvent('unknown')


@dataclass(frozen=True)
class PipelineTransition:
    stage: str
    previous_stage: str | None
    missing: tuple[str, ...]
    regression: bool


class PipelineProgress:
    """Track first-frame progress without coupling tests to log/source strings."""

    def __init__(self) -> None:
        self._highest = -1

    @property
    def stage(self) -> str | None:
        return PIPELINE_STAGES[self._highest] if self._highest >= 0 else None

    @property
    def complete(self) -> bool:
        return self.stage == 'client-decoded'

    def observe(self, stage: str) -> PipelineTransition:
        if stage not in _PIPELINE_INDEX:
            raise ValueError(f'unknown pre-login pipeline stage: {stage!r}')
        index = _PIPELINE_INDEX[stage]
        previous = self.stage
        regression = index < self._highest
        missing: tuple[str, ...] = ()
        if index > self._highest + 1:
            missing = tuple(PIPELINE_STAGES[self._highest + 1:index])
        if index > self._highest:
            self._highest = index
        return PipelineTransition(stage, previous, missing, regression)


def is_handoff_reason(reason: str) -> bool:
    return str(reason).startswith(HANDOFF_REASON_PREFIX)


def reconnect_delay_seconds(attempt: int, reason: str) -> float:
    """Deterministic reconnect policy shared by GTK and tests."""
    attempt = max(1, int(attempt))
    if is_handoff_reason(reason):
        return 0.25
    return min(10.0, 1.0 * (2 ** min(attempt - 1, 4)))
