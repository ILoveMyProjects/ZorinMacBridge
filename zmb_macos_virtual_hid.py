#!/usr/bin/env python3
"""Mac-side DriverKit virtual HID client used by ZorinMacBridge LoginWindow input.

The signed virtual-HID daemon owns the DriverKit device. ZMB talks to its
root-only Unix-domain socket using client protocol v7 and submits ordinary HID
keyboard and relative-pointer reports. No CGEvent/IOHIDPostEvent injection is
used by this backend.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable

SOCKET_PATH = Path(
    '/Library/Application Support/org.pqrs/tmp/rootonly/'
    'karabiner_virtual_hid_device_service.sock'
)
CLIENT_PROTOCOL_VERSION = 7
MAX_MESSAGE_SIZE = 32 * 1024
HEARTBEAT_SECONDS = 3.0

# request.hpp, protocol v7
CMD_INITIALIZE_KEYBOARD = 0
CMD_INITIALIZE_POINTING = 3
CMD_POST_KEYBOARD = 6
CMD_POST_POINTING = 11

# response.hpp
STATUS_DRIVER_ACTIVATED = 1
STATUS_DRIVER_CONNECTED = 2
STATUS_DRIVER_VERSION_MISMATCH = 3
STATUS_KEYBOARD_READY = 4
STATUS_POINTING_READY = 5

# Stable negative return values for LoginWindowInput diagnostics.
RC_NOT_ROOT = -210
RC_SOCKET_MISSING = -211
RC_CONNECT_FAILED = -212
RC_NOT_READY = -213
RC_DRIVER_VERSION_MISMATCH = -214
RC_DISCONNECTED = -215
RC_WRITE_FAILED = -216


class DriverKitVirtualHIDClient:
    """Protocol-v7 client with bounded startup and background liveness."""

    def __init__(
        self,
        *,
        socket_path: str | os.PathLike[str] = SOCKET_PATH,
        protocol_version: int = CLIENT_PROTOCOL_VERSION,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        require_root: bool = True,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self.socket_path = str(socket_path)
        self.protocol_version = int(protocol_version)
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.require_root = bool(require_root)
        self.socket_factory = socket_factory
        self._sock: socket.socket | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._reader: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._request_id = 0
        self.driver_activated: bool | None = None
        self.driver_connected: bool | None = None
        self.keyboard_ready = False
        self.pointing_ready = False
        self.driver_version_mismatched = False
        self.last_error = ''

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._sock is not None

    @property
    def ready(self) -> bool:
        with self._lock:
            return bool(
                self._sock is not None
                and self.keyboard_ready
                and self.pointing_ready
                and not self.driver_version_mismatched
            )

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                'connected': self._sock is not None,
                'driver_activated': self.driver_activated,
                'driver_connected': self.driver_connected,
                'keyboard_ready': bool(self.keyboard_ready),
                'pointing_ready': bool(self.pointing_ready),
                'driver_version_mismatched': bool(self.driver_version_mismatched),
                'protocol_version': self.protocol_version,
                'socket_path': self.socket_path,
                'last_error': self.last_error,
            }

    def open(self, *, wait_seconds: float = 2.5) -> int:
        self.close()
        if self.require_root and hasattr(os, 'geteuid') and os.geteuid() != 0:
            self.last_error = 'virtual HID daemon accepts root clients only'
            return RC_NOT_ROOT
        if not Path(self.socket_path).exists():
            self.last_error = f'virtual HID daemon socket missing: {self.socket_path}'
            return RC_SOCKET_MISSING

        sock = self.socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(2.0)
            sock.connect(self.socket_path)
            sock.settimeout(None)
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            self.last_error = f'connect failed: {type(exc).__name__}: {exc}'
            return RC_CONNECT_FAILED

        with self._lock:
            self._sock = sock
            self.driver_activated = None
            self.driver_connected = None
            self.keyboard_ready = False
            self.pointing_ready = False
            self.driver_version_mismatched = False
            self.last_error = ''
            self._request_id = 0
            self._ready_event.clear()
            self._stop_event.clear()

        self._reader = threading.Thread(target=self._read_loop, name='zmb-vhid-reader', daemon=True)
        self._reader.start()
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, name='zmb-vhid-heartbeat', daemon=True)
        self._heartbeat.start()

        # Exact v8.x parameter ABI: vendor_id, product_id, country_code are
        # three little-endian uint64 values (pqrs::hid strong typedefs).
        keyboard_params = struct.pack('<QQQ', 0x16C0, 0x27DB, 0)
        if not self._send_request(CMD_INITIALIZE_KEYBOARD, keyboard_params):
            self.close()
            return RC_WRITE_FAILED
        if not self._send_request(CMD_INITIALIZE_POINTING, b''):
            self.close()
            return RC_WRITE_FAILED

        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        while time.monotonic() < deadline:
            if self.ready:
                return 0
            if self.driver_version_mismatched:
                self.last_error = f'virtual HID driver version mismatch (client protocol={self.protocol_version})'
                self.close()
                return RC_DRIVER_VERSION_MISMATCH
            if not self.connected:
                return RC_DISCONNECTED
            self._ready_event.wait(min(0.05, max(0.0, deadline - time.monotonic())))
            self._ready_event.clear()

        status = self.status()
        self.last_error = (
            'virtual HID daemon connected but devices not ready: '
            f'driver_activated={status["driver_activated"]} '
            f'driver_connected={status["driver_connected"]} '
            f'keyboard={status["keyboard_ready"]} pointing={status["pointing_ready"]}'
        )
        # Keep the root-only daemon connection alive. DriverKit can finish
        # publishing the virtual devices after the initial bounded wait,
        # especially directly after boot. The background reader will promote
        # keyboard/pointing readiness as soon as the daemon reports it.
        return RC_NOT_READY

    def close(self) -> None:
        self._stop_event.set()
        with self._lock:
            sock = self._sock
            self._sock = None
            self.driver_activated = None
            self.driver_connected = None
            self.keyboard_ready = False
            self.pointing_ready = False
            self._ready_event.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def post_keyboard(self, modifiers: int, usages) -> int:
        if not self.ready:
            return RC_NOT_READY
        keys = sorted({int(k) & 0xFFFF for k in usages if int(k)})[:32]
        # Exact packed keyboard_input layout: report_id, modifiers, reserved,
        # then uint16_t keys[32].
        report = bytearray((1, int(modifiers) & 0xFF, 0))
        keys.extend([0] * (32 - len(keys)))
        for usage in keys:
            report.extend(struct.pack('<H', usage))
        return 0 if self._send_request(CMD_POST_KEYBOARD, bytes(report)) else RC_WRITE_FAILED

    def post_pointing(self, buttons: int, dx: int = 0, dy: int = 0, wheel_y: int = 0, wheel_x: int = 0) -> int:
        if not self.ready:
            return RC_NOT_READY

        def s8(value: int) -> int:
            return max(-127, min(127, int(value)))

        # Exact packed pointing_input layout: uint32 buttons then four uint8
        # fields. Signed relative values use their ordinary two's-complement bytes.
        report = struct.pack(
            '<Ibbbb',
            int(buttons) & 0xFFFFFFFF,
            s8(dx), s8(dy), s8(wheel_y), s8(wheel_x),
        )
        return 0 if self._send_request(CMD_POST_POINTING, report) else RC_WRITE_FAILED

    def release_all(self) -> None:
        if not self.ready:
            return
        self.post_keyboard(0, [])
        self.post_pointing(0, 0, 0, 0, 0)

    def _next_request_id(self) -> int:
        with self._lock:
            self._request_id = (self._request_id + 1) & 0xFFFFFFFFFFFFFFFF
            return self._request_id

    def _send_request(self, command: int, data: bytes) -> bool:
        payload = struct.pack('<H', self.protocol_version) + bytes((int(command) & 0xFF,)) + bytes(data)
        return self._send_frame(4, self._next_request_id(), payload)

    def _send_simple(self, frame_type: int) -> bool:
        return self._write_all(struct.pack('>I', 1) + bytes((int(frame_type) & 0xFF,)))

    def _send_frame(self, frame_type: int, request_id: int, payload: bytes) -> bool:
        body = bytes((int(frame_type) & 0xFF,)) + struct.pack('>Q', int(request_id)) + bytes(payload)
        return self._write_all(struct.pack('>I', len(body)) + body)

    def _write_all(self, data: bytes) -> bool:
        with self._write_lock:
            with self._lock:
                sock = self._sock
            if sock is None:
                return False
            try:
                sock.sendall(data)
                return True
            except OSError as exc:
                self.last_error = f'write failed: {type(exc).__name__}: {exc}'
                self._mark_disconnected(sock)
                return False

    def _recv_exact(self, count: int) -> bytes | None:
        with self._lock:
            sock = self._sock
        if sock is None:
            return None
        out = bytearray()
        try:
            while len(out) < count:
                part = sock.recv(count - len(out))
                if not part:
                    return None
                out.extend(part)
            return bytes(out)
        except OSError as exc:
            if not self._stop_event.is_set():
                self.last_error = f'read failed: {type(exc).__name__}: {exc}'
            return None

    def _read_loop(self) -> None:
        while not self._stop_event.is_set():
            header = self._recv_exact(4)
            if header is None:
                break
            length = struct.unpack('>I', header)[0]
            if length <= 0 or length > MAX_MESSAGE_SIZE + 9:
                self.last_error = f'invalid virtual HID frame length: {length}'
                break
            body = self._recv_exact(length)
            if body is None or not body:
                break
            frame_type = body[0]
            if frame_type == 0:  # heartbeat
                continue
            if frame_type == 2:  # health_check
                self._send_simple(3)
                continue
            if frame_type in (4, 5):  # request / response
                if len(body) < 9:
                    self.last_error = 'short virtual HID request/response frame'
                    break
                request_id = struct.unpack('>Q', body[1:9])[0]
                self._handle_status(body[9:])
                if frame_type == 4:
                    # Upstream client responds to daemon status requests with an
                    # empty response carrying the same request id.
                    self._send_frame(5, request_id, b'')
                continue
            if frame_type in (1, 3):  # user_data / health_check_response
                continue
            self.last_error = f'unknown virtual HID frame type: {frame_type}'
            break
        with self._lock:
            sock = self._sock
        self._mark_disconnected(sock)

    def _handle_status(self, payload: bytes) -> None:
        if len(payload) % 2:
            return
        changed = False
        with self._lock:
            for offset in range(0, len(payload), 2):
                status_type = payload[offset]
                value = payload[offset + 1] != 0
                if status_type == STATUS_DRIVER_ACTIVATED:
                    changed = changed or self.driver_activated is None or self.driver_activated != value
                    self.driver_activated = value
                elif status_type == STATUS_DRIVER_CONNECTED:
                    changed = changed or self.driver_connected is None or self.driver_connected != value
                    self.driver_connected = value
                elif status_type == STATUS_KEYBOARD_READY:
                    changed = changed or self.keyboard_ready != value
                    self.keyboard_ready = value
                elif status_type == STATUS_POINTING_READY:
                    changed = changed or self.pointing_ready != value
                    self.pointing_ready = value
                elif status_type == STATUS_DRIVER_VERSION_MISMATCH:
                    changed = changed or self.driver_version_mismatched != value
                    self.driver_version_mismatched = value
                    if value:
                        self.last_error = f'virtual HID driver version mismatch for protocol {self.protocol_version}'
        if changed:
            self._ready_event.set()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self.heartbeat_seconds):
            if not self.connected:
                return
            if not self._send_simple(0):
                return

    def _mark_disconnected(self, sock: socket.socket | None) -> None:
        with self._lock:
            if sock is not None and self._sock is not sock:
                return
            current = self._sock
            self._sock = None
            self.driver_activated = None
            self.driver_connected = None
            self.keyboard_ready = False
            self.pointing_ready = False
            self._ready_event.set()
        if current is not None:
            try:
                current.close()
            except OSError:
                pass


def chunk_relative(dx: int, dy: int):
    """Yield signed 8-bit relative-pointer chunks preserving total movement."""
    remaining_x, remaining_y = int(dx), int(dy)
    if remaining_x == 0 and remaining_y == 0:
        yield 0, 0
        return
    while remaining_x or remaining_y:
        sx = max(-127, min(127, remaining_x))
        sy = max(-127, min(127, remaining_y))
        yield sx, sy
        remaining_x -= sx
        remaining_y -= sy
