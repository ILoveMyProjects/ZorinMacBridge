#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import platform
import queue
import shutil
import socket
import select
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from resources import resource_path
from zmb_macos_virtual_hid import DriverKitVirtualHIDClient, chunk_relative

from zmb_session_lifecycle import (
    PRELOGIN_CONTROL_REPLACEMENT_GRACE_SECONDS, PreloginControlLease,
    PreloginSessionCoordinator, PIPELINE_STAGES, make_capture_backend_state, make_capture_failure_state, make_capture_progress_state, make_handoff_state, make_input_probe_state, make_pipeline_state,
    make_input_event_state,
)

from protocol import (
    AUTH, AUTH_FAIL, AUTH_OK, CLIPBOARD_DATA, CLIPBOARD_GET, CLIPBOARD_SET,
    DOWNLOAD_BEGIN, DOWNLOAD_CHUNK, DOWNLOAD_END, DOWNLOAD_REQ, ERROR, SESSION_STATE, VIDEO_H264, VIDEO_STATS,
    KEY, LIST_REQ, LIST_RESP, MKDIR_OK, MKDIR_REQ, MOUSE_BUTTON, MOUSE_MOVE,
    PacketReader, SCROLL, TEXT, UPLOAD_BEGIN, UPLOAD_CHUNK, UPLOAD_END,
    pack_json, pack_packet, recv_one_blocking, unpack_json,
)

APP_DIR = Path.home() / '.zorin-mac-bridge'
CERT_PATH = APP_DIR / 'server-cert.pem'
KEY_PATH = APP_DIR / 'server-key.pem'
DEFAULT_SHARE = Path.home() / 'ZorinMac-Share'


def runtime_version() -> str:
    try:
        value = resource_path('VERSION').read_text(encoding='utf-8').strip()
        return value or '0.0.0'
    except Exception:
        return '0.0.0'


def configure_runtime_home(home: Path | str) -> None:
    """Reuse one user's server identity from the root LoginWindow helper."""
    global APP_DIR, CERT_PATH, KEY_PATH, DEFAULT_SHARE
    base = Path(home).expanduser()
    APP_DIR = base / '.zorin-mac-bridge'
    CERT_PATH = APP_DIR / 'server-cert.pem'
    KEY_PATH = APP_DIR / 'server-key.pem'
    DEFAULT_SHARE = base / 'ZorinMac-Share'


CHUNK = 256 * 1024
MAX_CLIPBOARD = 2 * 1024 * 1024

# Exactly one ScreenCaptureKit/VideoToolbox streamer may own the native capture
# pipeline at a time. Video reconnects are intentionally independent from the
# control channel, so serialize only the video role.  Keep the active TLS socket
# separately so a replacement can actively interrupt a stale owner instead of
# waiting behind it for 20 seconds.
_VIDEO_SESSION_LOCK = threading.Lock()
_VIDEO_SESSION_STATE_LOCK = threading.Lock()
_ACTIVE_VIDEO_SOCKET: ssl.SSLSocket | None = None
_ACTIVE_VIDEO_CANCEL: threading.Event | None = None
VIDEO_REPLACEMENT_GRACE_SECONDS = 4.0
VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 35.0
_PRELOGIN_SESSION_COORDINATOR = PreloginSessionCoordinator()


def request_prelogin_handoff(user: str, *, log=print) -> PreloginControlLease | None:
    """Notify the active LoginWindow client that Aqua is taking over."""
    lease = _PRELOGIN_SESSION_COORDINATOR.request_handoff(str(user), log=log)
    # Video has no useful work left once loginwindow hands off.  Interrupt it now
    # so the Aqua server can acquire the native capture pipeline quickly.
    _interrupt_active_video_session(log=log)
    return lease


def _set_active_video_socket(sock: ssl.SSLSocket | None, cancel_event: threading.Event | None = None) -> None:
    global _ACTIVE_VIDEO_SOCKET, _ACTIVE_VIDEO_CANCEL
    with _VIDEO_SESSION_STATE_LOCK:
        _ACTIVE_VIDEO_SOCKET = sock
        _ACTIVE_VIDEO_CANCEL = cancel_event


def _clear_active_video_socket(sock: ssl.SSLSocket) -> None:
    global _ACTIVE_VIDEO_SOCKET, _ACTIVE_VIDEO_CANCEL
    with _VIDEO_SESSION_STATE_LOCK:
        if _ACTIVE_VIDEO_SOCKET is sock:
            _ACTIVE_VIDEO_SOCKET = None
            _ACTIVE_VIDEO_CANCEL = None


def _interrupt_active_video_session(*, log=print) -> bool:
    with _VIDEO_SESSION_STATE_LOCK:
        active = _ACTIVE_VIDEO_SOCKET
        cancel_event = _ACTIVE_VIDEO_CANCEL
    if cancel_event is not None:
        cancel_event.set()
    if active is None:
        return cancel_event is not None
    try:
        # TCP shutdown is intentional here.  It wakes the old video thread out of
        # select()/recv immediately; that thread still owns close() and native cleanup.
        active.shutdown(socket.SHUT_RDWR)
        log('[video] forced stale video socket shutdown before replacement')
    except OSError as exc:
        log(f'[video] stale video socket shutdown warning: {type(exc).__name__}: {exc}')
    return True


def _acquire_video_session(sock: ssl.SSLSocket, *, log=print) -> threading.Event:
    if not _VIDEO_SESSION_LOCK.acquire(blocking=False):
        log('[video] previous video session still active; interrupting stale owner before replacement')
        _interrupt_active_video_session(log=log)
        if not _VIDEO_SESSION_LOCK.acquire(timeout=VIDEO_REPLACEMENT_GRACE_SECONDS):
            raise TimeoutError(
                f'previous video session did not stop within {VIDEO_REPLACEMENT_GRACE_SECONDS:g}s '
                'after forced socket shutdown'
            )
    cancel_event = threading.Event()
    _set_active_video_socket(sock, cancel_event)
    return cancel_event


def _release_video_session(sock: ssl.SSLSocket) -> None:
    _clear_active_video_socket(sock)
    _VIDEO_SESSION_LOCK.release()


MAX_FILE_SIZE = 100 * 1024**3

V4_ALLOWED = tuple(ipaddress.ip_network(n) for n in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '127.0.0.0/8', '169.254.0.0/16',
))
V6_ALLOWED = tuple(ipaddress.ip_network(n) for n in (
    '::1/128', 'fc00::/7', 'fe80::/10',
))


def is_lan_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split('%', 1)[0])
    except ValueError:
        return False
    nets = V4_ALLOWED if ip.version == 4 else V6_ALLOWED
    return any(ip in net for net in nets)


def ensure_certificate() -> str:
    APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not CERT_PATH.exists() or not KEY_PATH.exists():
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
        except ImportError as exc:
            raise SystemExit('The cryptography package is missing. Run scripts/setup-source-macos.sh') from exc

        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, 'ZorinMac-Bridge'),
        ])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('ZorinMac-Bridge')]), critical=False)
            .sign(key, hashes.SHA256())
        )
        KEY_PATH.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        os.chmod(KEY_PATH, 0o600)
        os.chmod(CERT_PATH, 0o600)

    pem = CERT_PATH.read_text(encoding='ascii')
    der = ssl.PEM_cert_to_DER_cert(pem)
    fp = hashlib.sha256(der).hexdigest().upper()
    return ':'.join(fp[i:i + 2] for i in range(0, len(fp), 2))


def normalize_relpath(value: str, *, allow_root: bool = True) -> PurePosixPath:
    if '\x00' in value:
        raise ValueError('NUL in path')
    value = value.replace('\\', '/')
    if value in {'', '.'}:
        if allow_root:
            return PurePosixPath('.')
        raise ValueError('root path not allowed')
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in {'', '.', '..'} for part in p.parts):
        raise ValueError('invalid relative path')
    return p


def path_inside(base: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([str(base.resolve()), str(candidate.resolve(strict=False))]) == str(base.resolve())
    except (ValueError, OSError):
        return False


def safe_share_path(share: Path, rel: str, *, allow_root: bool = True) -> Path:
    pp = normalize_relpath(rel, allow_root=allow_root)
    target = share if str(pp) == '.' else share.joinpath(*pp.parts)
    if not path_inside(share, target):
        raise ValueError('path escapes share directory')
    return target


def rel_join(parent: str, name: str) -> str:
    if '/' in name or '\\' in name or name in {'', '.', '..'}:
        raise ValueError('invalid filename')
    return name if not parent else f'{parent.rstrip("/")}/{name}'




def screen_capture_permission_status() -> bool | None:
    # Return True/False on supported macOS versions, None if unavailable.
    try:
        cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        fn = getattr(cg, 'CGPreflightScreenCaptureAccess')
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return None


def request_screen_capture_permission() -> bool | None:
    # LOCAL-ONLY helper for the macOS GUI. This is never called by a remote
    # connection. Apple documents CGRequestScreenCaptureAccess() as the API
    # that requests Screen Recording authorization for the current process.
    try:
        cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        fn = getattr(cg, 'CGRequestScreenCaptureAccess')
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return None


def accessibility_permission_status() -> bool | None:
    # Prefer CoreGraphics' event-posting permission because remote control is
    # implemented with CGEventPost. Fall back to AX trust on older systems.
    try:
        cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        fn = getattr(cg, 'CGPreflightPostEventAccess')
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        pass
    try:
        app = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        fn = getattr(app, 'AXIsProcessTrusted')
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return None


def request_accessibility_permission() -> bool | None:
    # LOCAL-ONLY helper for mouse/keyboard control. CGRequestPostEventAccess()
    # requests permission for this process to post synthetic input events.
    # It is deliberately never invoked from a remote connection.
    try:
        cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        fn = getattr(cg, 'CGRequestPostEventAccess')
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return None


def permission_api_self_test() -> tuple[str, ...]:
    """Resolve, but do not call, the macOS privacy APIs used by the GUI.

    This is safe for CI because it cannot trigger a TCC prompt.
    """
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    required = (
        'CGPreflightScreenCaptureAccess',
        'CGRequestScreenCaptureAccess',
        'CGPreflightPostEventAccess',
        'CGRequestPostEventAccess',
    )
    for name in required:
        getattr(cg, name)
    return required


class CGPoint(ctypes.Structure):
    _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [('width', ctypes.c_double), ('height', ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [('origin', CGPoint), ('size', CGSize)]


_ACTIVE_INPUT_DISPLAY_LOCK = threading.Lock()
_ACTIVE_INPUT_DISPLAY_ID = 0


def set_active_input_display(display_id: int) -> None:
    global _ACTIVE_INPUT_DISPLAY_ID
    with _ACTIVE_INPUT_DISPLAY_LOCK:
        _ACTIVE_INPUT_DISPLAY_ID = max(0, int(display_id))


def active_input_display() -> int:
    with _ACTIVE_INPUT_DISPLAY_LOCK:
        return int(_ACTIVE_INPUT_DISPLAY_ID)


class MacInput:
    LEFT_DOWN = 1
    LEFT_UP = 2
    RIGHT_DOWN = 3
    RIGHT_UP = 4
    MOUSE_MOVED = 5
    LEFT_DRAGGED = 6
    RIGHT_DRAGGED = 7
    OTHER_DOWN = 25
    OTHER_UP = 26
    OTHER_DRAGGED = 27
    HID_TAP = 0

    BUTTONS = {1: 0, 2: 2, 3: 1}
    KEYCODES = {
        'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7,
        'c': 8, 'v': 9, 'b': 11, 'q': 12, 'w': 13, 'e': 14, 'r': 15,
        'y': 16, 't': 17, '1': 18, '2': 19, '3': 20, '4': 21, '6': 22,
        '5': 23, 'equal': 24, '9': 25, '7': 26, 'minus': 27, '8': 28,
        '0': 29, 'bracketright': 30, 'o': 31, 'u': 32, 'bracketleft': 33,
        'i': 34, 'p': 35, 'Return': 36, 'l': 37, 'j': 38, 'apostrophe': 39,
        'k': 40, 'semicolon': 41, 'backslash': 42, 'comma': 43, 'slash': 44,
        'n': 45, 'm': 46, 'period': 47, 'Tab': 48, 'space': 49,
        'grave': 50, 'BackSpace': 51, 'Escape': 53,
        'Meta_L': 55, 'Meta_R': 54, 'Shift_L': 56, 'Shift_R': 60,
        'Caps_Lock': 57, 'Alt_L': 58, 'Alt_R': 61, 'Control_L': 59,
        'Control_R': 62, 'F17': 64, 'KP_Decimal': 65, 'KP_Multiply': 67,
        'KP_Add': 69, 'Num_Lock': 71, 'KP_Divide': 75, 'KP_Enter': 76,
        'KP_Subtract': 78, 'F18': 79, 'F19': 80, 'KP_Equal': 81,
        'KP_0': 82, 'KP_1': 83, 'KP_2': 84, 'KP_3': 85, 'KP_4': 86,
        'KP_5': 87, 'KP_6': 88, 'KP_7': 89, 'F20': 90, 'KP_8': 91,
        'KP_9': 92, 'F5': 96, 'F6': 97, 'F7': 98, 'F3': 99, 'F8': 100,
        'F9': 101, 'F11': 103, 'F13': 105, 'F16': 106, 'F14': 107,
        'F10': 109, 'F12': 111, 'F15': 113, 'Home': 115, 'Prior': 116,
        'Delete': 117, 'F4': 118, 'End': 119, 'F2': 120, 'Next': 121,
        'F1': 122, 'Left': 123, 'Right': 124, 'Down': 125, 'Up': 126,
    }
    MOD_FLAGS = {
        'Caps_Lock': 0x00010000,
        'Shift_L': 0x00020000, 'Shift_R': 0x00020000,
        'Control_L': 0x00040000, 'Control_R': 0x00040000,
        'Alt_L': 0x00080000, 'Alt_R': 0x00080000,
        'Meta_L': 0x00100000, 'Meta_R': 0x00100000,
    }

    def __init__(self) -> None:
        self.cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.cg.CGMainDisplayID.restype = ctypes.c_uint32
        self.cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        self.cg.CGDisplayBounds.restype = CGRect
        self.cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
        self.cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        self.cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
        self.cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        self.cg.CGEventCreate.argtypes = [ctypes.c_void_p]
        self.cg.CGEventCreate.restype = ctypes.c_void_p
        self.cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        self.cg.CGEventGetLocation.restype = CGPoint
        self.cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
        self.cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
        self.cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        self.cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self.cg.CGEventKeyboardSetUnicodeString.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint16)]
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cg.CGEventCreateScrollWheelEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        self.cg.CGEventCreateScrollWheelEvent.restype = ctypes.c_void_p
        self.held_buttons: set[int] = set()
        self.held_modifiers: set[str] = set()

    def _combined_session_flags(self) -> int:
        # kCGEventSourceStateCombinedSessionState == 0.  Querying this state is
        # how the pre-login input matrix proves that a modifier report was
        # actually observed by WindowServer/LoginWindow rather than merely
        # accepted by an injection API.
        return int(self.cg.CGEventSourceFlagsState(ctypes.c_int32(0)))

    def _cursor_location(self) -> CGPoint | None:
        event = self.cg.CGEventCreate(None)
        if not event:
            return None
        try:
            return self.cg.CGEventGetLocation(event)
        finally:
            self.cf.CFRelease(event)

    def _bounds(self) -> CGRect:
        display = active_input_display() or int(self.cg.CGMainDisplayID())
        bounds = self.cg.CGDisplayBounds(ctypes.c_uint32(display))
        if bounds.size.width <= 0 or bounds.size.height <= 0:
            bounds = self.cg.CGDisplayBounds(self.cg.CGMainDisplayID())
        return bounds

    def _flags(self) -> int:
        flags = 0
        for key in self.held_modifiers:
            flags |= self.MOD_FLAGS.get(key, 0)
        return flags

    def _post(self, event, *, with_flags: bool = False) -> None:
        if not event:
            return
        if with_flags:
            self.cg.CGEventSetFlags(event, ctypes.c_uint64(self._flags()))
        self.cg.CGEventPost(self.HID_TAP, event)
        self.cf.CFRelease(event)

    def move(self, nx: float, ny: float) -> None:
        nx = min(1.0, max(0.0, float(nx)))
        ny = min(1.0, max(0.0, float(ny)))
        bounds = self._bounds()
        x = bounds.origin.x + nx * bounds.size.width
        y = bounds.origin.y + ny * bounds.size.height
        if 1 in self.held_buttons:
            etype, button = self.LEFT_DRAGGED, 0
        elif 3 in self.held_buttons:
            etype, button = self.RIGHT_DRAGGED, 1
        elif 2 in self.held_buttons:
            etype, button = self.OTHER_DRAGGED, 2
        else:
            etype, button = self.MOUSE_MOVED, 0
        self._post(self.cg.CGEventCreateMouseEvent(None, etype, CGPoint(x, y), button))

    def button(self, button_num: int, down: bool, nx: float, ny: float) -> None:
        self.move(nx, ny)
        cg_button = self.BUTTONS.get(button_num)
        if cg_button is None:
            return
        if button_num == 1:
            etype = self.LEFT_DOWN if down else self.LEFT_UP
        elif button_num == 3:
            etype = self.RIGHT_DOWN if down else self.RIGHT_UP
        else:
            etype = self.OTHER_DOWN if down else self.OTHER_UP
        bounds = self._bounds()
        x = bounds.origin.x + min(1.0, max(0.0, nx)) * bounds.size.width
        y = bounds.origin.y + min(1.0, max(0.0, ny)) * bounds.size.height
        self._post(self.cg.CGEventCreateMouseEvent(None, etype, CGPoint(x, y), cg_button))
        if down:
            self.held_buttons.add(button_num)
        else:
            self.held_buttons.discard(button_num)

    def scroll(self, dx: int, dy: int) -> None:
        event = self.cg.CGEventCreateScrollWheelEvent(
            None, ctypes.c_uint32(1), ctypes.c_uint32(2), ctypes.c_int32(dy), ctypes.c_int32(dx)
        )
        self._post(event)

    def key(self, keysym: str, down: bool) -> None:
        code = self.KEYCODES.get(keysym)
        if code is None and len(keysym) == 1:
            code = self.KEYCODES.get(keysym.lower())
        if code is None:
            return
        is_mod = keysym in self.MOD_FLAGS
        if is_mod and down:
            self.held_modifiers.add(keysym)
        elif is_mod and not down:
            self.held_modifiers.discard(keysym)
        event = self.cg.CGEventCreateKeyboardEvent(None, code, bool(down))
        self._post(event, with_flags=True)

    def text(self, text: str) -> None:
        """Unicode fallback for characters that cannot use the physical KEY path."""
        if not text:
            return
        units = text.encode('utf-16-le')
        count = len(units) // 2
        arr = (ctypes.c_uint16 * count).from_buffer_copy(units)
        event = self.cg.CGEventCreateKeyboardEvent(None, 0, True)
        if not event:
            raise RuntimeError('CGEventCreateKeyboardEvent returned NULL for Unicode key-down')
        self.cg.CGEventKeyboardSetUnicodeString(event, count, arr)
        self._post(event)
        event = self.cg.CGEventCreateKeyboardEvent(None, 0, False)
        if not event:
            raise RuntimeError('CGEventCreateKeyboardEvent returned NULL for Unicode key-up')
        self.cg.CGEventKeyboardSetUnicodeString(event, count, arr)
        self._post(event)

    def release_all(self) -> None:
        for key in list(self.held_modifiers):
            try:
                self.key(key, False)
            except Exception:
                pass
        self.held_modifiers.clear()
        self.held_buttons.clear()


class LoginWindowInput(MacInput):
    """Bounded LoginWindow input activation with runtime backend failover.

    The critical control-session startup path must never depend on observing
    WindowServer state through CoreGraphics.  On macOS LoginWindow those
    observation calls can block even while capture/video is healthy.  Instead
    we open one native backend at a time, report OPEN_READY immediately, and
    treat the first real user event as the delivery attempt.  A native dispatch
    error closes that backend, opens the next candidate, and retries the same
    event without tearing down Desktop/video.
    """

    HID_USAGES = {
        **{chr(ord('a') + i): 0x04 + i for i in range(26)},
        '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21, '5': 0x22,
        '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26, '0': 0x27,
        'Return': 0x28, 'Escape': 0x29, 'BackSpace': 0x2A, 'Tab': 0x2B,
        'space': 0x2C, 'minus': 0x2D, 'equal': 0x2E,
        'bracketleft': 0x2F, 'bracketright': 0x30, 'backslash': 0x31,
        'semicolon': 0x33, 'apostrophe': 0x34, 'grave': 0x35,
        'comma': 0x36, 'period': 0x37, 'slash': 0x38, 'Caps_Lock': 0x39,
        'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D,
        'F5': 0x3E, 'F6': 0x3F, 'F7': 0x40, 'F8': 0x41,
        'F9': 0x42, 'F10': 0x43, 'F11': 0x44, 'F12': 0x45,
        'Home': 0x4A, 'Prior': 0x4B, 'Delete': 0x4C, 'End': 0x4D,
        'Next': 0x4E, 'Right': 0x4F, 'Left': 0x50, 'Down': 0x51, 'Up': 0x52,
        'Num_Lock': 0x53, 'KP_Divide': 0x54, 'KP_Multiply': 0x55,
        'KP_Subtract': 0x56, 'KP_Add': 0x57, 'KP_Enter': 0x58,
        'KP_1': 0x59, 'KP_2': 0x5A, 'KP_3': 0x5B, 'KP_4': 0x5C,
        'KP_5': 0x5D, 'KP_6': 0x5E, 'KP_7': 0x5F, 'KP_8': 0x60,
        'KP_9': 0x61, 'KP_0': 0x62, 'KP_Decimal': 0x63, 'KP_Equal': 0x67,
        'F13': 0x68, 'F14': 0x69, 'F15': 0x6A, 'F16': 0x6B,
        'F17': 0x6C, 'F18': 0x6D, 'F19': 0x6E, 'F20': 0x6F,
        'Control_L': 0xE0, 'Shift_L': 0xE1, 'Alt_L': 0xE2, 'Meta_L': 0xE3,
        'Control_R': 0xE4, 'Shift_R': 0xE5, 'Alt_R': 0xE6, 'Meta_R': 0xE7,
    }
    _SHIFTED_ASCII = {
        '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6',
        '&': '7', '*': '8', '(': '9', ')': '0', '_': 'minus', '+': 'equal',
        '{': 'bracketleft', '}': 'bracketright', '|': 'backslash',
        ':': 'semicolon', '"': 'apostrophe', '<': 'comma', '>': 'period',
        '?': 'slash', '~': 'grave',
    }
    _PLAIN_ASCII = {
        '-': 'minus', '=': 'equal', '[': 'bracketleft', ']': 'bracketright',
        '\\': 'backslash', ';': 'semicolon', "'": 'apostrophe',
        ',': 'comma', '.': 'period', '/': 'slash', '`': 'grave', ' ': 'space',
        '\t': 'Tab', '\n': 'Return', '\r': 'Return',
    }

    # CoreHID remains first for a provisioned build.  For the current ad-hoc
    # LoginWindow path prefer root IOHIDSystem before IOHIDUserDevice: it posts
    # directly into the system event path and does not rely on a virtual-device
    # service being accepted by LoginWindow.
    BACKEND_ORDER = (
        ('driverkit', -1),
        ('corehid', 0),
        ('iohidsystem', 2),
        ('userdevice', 1),
        ('quartz', 3),
    )
    BACKEND_LABELS = {
        'driverkit': 'DriverKit virtual HID keyboard + mouse',
        'corehid': 'CoreHID HIDVirtualDevice',
        'userdevice': 'IOHIDUserDevice virtual HID',
        'iohidsystem': 'IOHIDSystem/IOHIDPostEvent',
        'quartz': 'kCGSessionEventTap',
        'unavailable': 'unavailable',
    }

    def __init__(self) -> None:
        # Keep the already-working LoginWindow capture/wake path untouched.
        super().__init__()
        self.native = NativeStreamerLibrary()
        self.virtual_hid = DriverKitVirtualHIDClient()
        self._driverkit_keys: set[int] = set()
        self.backend = 'unavailable'
        self.legacy_backend: int | None = None
        self.corehid_open_rc: int | None = None
        self.legacy_open_rc: int | None = None
        self.input_probe_results: list[dict[str, object]] = []
        self.last_nx = 0.5
        self.last_ny = 0.5
        self._state_callback = None
        self._event_callback = None
        self._channel_override = {'keyboard': '', 'pointer': ''}
        self._async_dispatch_results: queue.SimpleQueue[tuple] = queue.SimpleQueue()
        self._quartz_inflight = {'keyboard': None, 'pointer': None}
        self._quartz_disabled: set[str] = set()
        self._quartz_token = 0
        self._released = False
        self._activate_from_index(0)

    def set_state_callback(self, callback, *, replay: bool = True) -> None:
        self._state_callback = callback
        if replay and callback is not None:
            for row in self.input_probe_states():
                callback(row)

    def set_event_callback(self, callback) -> None:
        self._event_callback = callback

    @staticmethod
    def _rc_text(rc: int | None) -> str:
        if rc is None:
            return 'not-run'
        value = int(rc)
        return f'{value} (0x{value & 0xffffffff:08x})'

    def _emit_row(self, row: dict[str, object]) -> None:
        callback = self._state_callback
        if callback is not None:
            callback(dict(row))

    def _emit_event(self, *, channel: str, action: str, backend: str, rc: int, result: str, detail: str = '') -> None:
        callback = self._event_callback
        if callback is not None:
            callback({
                'channel': str(channel),
                'action': str(action),
                'backend': str(backend),
                'rc': int(rc),
                'result': str(result),
                'detail': str(detail),
            })

    def _row_for(self, backend: str) -> dict[str, object] | None:
        return next((row for row in self.input_probe_results if row.get('backend') == backend), None)

    def _close_all_backends(self) -> None:
        try:
            self.virtual_hid.release_all()
        except Exception:
            pass
        try:
            self.virtual_hid.close()
        except Exception:
            pass
        self._driverkit_keys.clear()
        try:
            self.native.lib.zmb_corehid_input_close()
        except Exception:
            pass
        try:
            self.native.lib.zmb_login_input_close()
        except Exception:
            pass
        self.backend = 'unavailable'
        self.legacy_backend = None

    def _open_backend(self, backend: str, backend_id: int) -> int:
        if backend == 'driverkit':
            return int(self.virtual_hid.open(wait_seconds=2.5))
        if backend == 'corehid':
            return int(self.native.lib.zmb_corehid_input_open())
        return int(self.native.lib.zmb_login_input_open_backend(int(backend_id)))

    def _activate_from_index(self, start_index: int) -> bool:
        self._close_all_backends()
        self._channel_override = {'keyboard': '', 'pointer': ''}
        for backend, backend_id in self.BACKEND_ORDER[max(0, int(start_index)):]:
            open_rc = self._open_backend(backend, backend_id)
            row = self._row_for(backend)
            if row is None:
                row = {
                    'backend': backend,
                    'label': self.BACKEND_LABELS[backend],
                    'open_rc': open_rc,
                    'keyboard': 'UNVERIFIED',
                    'pointer': 'UNVERIFIED',
                    'result': 'FAIL_OPEN' if open_rc != 0 else 'OPEN_READY',
                    'reason': '',
                    'selected': False,
                }
                self.input_probe_results.append(row)
            else:
                row.update({
                    'open_rc': open_rc,
                    'keyboard': 'UNVERIFIED',
                    'pointer': 'UNVERIFIED',
                    'result': 'FAIL_OPEN' if open_rc != 0 else 'OPEN_READY',
                    'reason': '',
                    'selected': False,
                })
            if backend == 'corehid':
                self.corehid_open_rc = open_rc
            if open_rc != 0:
                if backend == 'driverkit':
                    row['reason'] = self.virtual_hid.last_error or 'DriverKit virtual HID backend not ready'
                else:
                    row['reason'] = 'native backend open failed'
                self._emit_row(row)
                continue
            self.backend = backend
            row['selected'] = True
            row['reason'] = 'backend opened; delivery will be verified by real user input'
            if backend != 'corehid':
                self.legacy_backend = int(backend_id)
                self.legacy_open_rc = open_rc
            self._emit_row(row)
            return True

        unavailable = {
            'backend': 'unavailable',
            'label': self.BACKEND_LABELS['unavailable'],
            'open_rc': None,
            'keyboard': 'UNAVAILABLE',
            'pointer': 'UNAVAILABLE',
            'result': 'UNAVAILABLE',
            'reason': 'no LoginWindow input backend could be opened',
            'selected': False,
        }
        old = self._row_for('unavailable')
        if old is None:
            self.input_probe_results.append(unavailable)
        else:
            old.update(unavailable)
            unavailable = old
        self._emit_row(unavailable)
        return False

    def _backend_index(self, backend: str) -> int:
        for index, (name, _backend_id) in enumerate(self.BACKEND_ORDER):
            if name == backend:
                return index
        return len(self.BACKEND_ORDER)

    QUARTZ_ASYNC_TIMEOUT_SECONDS = 0.75

    def _mark_runtime_result(
        self, backend: str, channel: str, *, rc: int, ok: bool, operation: str,
        preserve_selected_on_failure: bool = False,
    ) -> None:
        row = self._row_for(backend)
        if row is None:
            return
        if channel in ('keyboard', 'pointer'):
            row[channel] = 'DISPATCH_OK' if ok else 'DISPATCH_FAIL'
        row['result'] = 'ACTIVE' if ok else 'DISPATCH_FAIL'
        row['reason'] = (
            f'{operation} dispatch accepted by native backend'
            if ok else f'{operation} failed: rc={self._rc_text(rc)}; trying next backend'
        )
        if not ok and not preserve_selected_on_failure:
            row['selected'] = False
        # Emit first real delivery-attempt result and every failure/failover.
        marker = f'_reported_{channel}_ok'
        if not ok or not row.get(marker):
            row[marker] = bool(ok)
            self._emit_row(row)

    def _queue_quartz_dispatch(self, channel: str, action: str, send_quartz_direct) -> bool:
        """Run Quartz outside the control thread with a bounded diagnostic timeout.

        CGEventPost has no delivery acknowledgement and has physically stalled the
        LoginWindow control path on macOS.  Never execute it inline.  At most one
        Quartz call per channel may be in flight; if that call does not return in
        time, the channel is disabled for Quartz while the control/TLS/video session
        stays alive.
        """
        if send_quartz_direct is None or self._released:
            return False
        if channel in self._quartz_disabled:
            self._emit_event(
                channel=channel, action=action, backend='quartz-async', rc=-1,
                result='DISPATCH_UNAVAILABLE',
                detail='Quartz fallback disabled after an earlier timeout/failure; control thread remains live',
            )
            return False
        if self._quartz_inflight.get(channel) is not None:
            # Mouse movement is coalesced by the Linux client.  Do not create an
            # unbounded thread backlog while one native Quartz call is unresolved.
            return True

        self._quartz_token += 1
        token = self._quartz_token
        done = threading.Event()
        self._quartz_inflight[channel] = token
        self._channel_override[channel] = 'quartz-async'
        self._emit_event(
            channel=channel, action=action, backend='quartz-async', rc=0,
            result='DISPATCH_PENDING',
            detail='Quartz fallback isolated from control thread; awaiting bounded native return',
        )

        def worker() -> None:
            try:
                rc = int(send_quartz_direct())
                error = ''
            except Exception as exc:
                rc = -32768
                error = f'{type(exc).__name__}: {exc}'
            finally:
                done.set()
            self._async_dispatch_results.put(('result', token, channel, action, rc, error))

        def watchdog() -> None:
            if not done.wait(self.QUARTZ_ASYNC_TIMEOUT_SECONDS):
                self._async_dispatch_results.put(('timeout', token, channel, action, -1, ''))

        threading.Thread(target=worker, name=f'zmb-quartz-{channel}', daemon=True).start()
        threading.Thread(target=watchdog, name=f'zmb-quartz-watchdog-{channel}', daemon=True).start()
        return True

    def drain_async_events(self) -> None:
        """Publish worker results from the control-session thread only."""
        while True:
            try:
                kind, token, channel, action, rc, error = self._async_dispatch_results.get_nowait()
            except queue.Empty:
                return
            if self._quartz_inflight.get(channel) != token:
                # Late result from a native call that already timed out.
                continue
            if kind == 'timeout':
                self._quartz_inflight[channel] = None
                self._quartz_disabled.add(channel)
                self._channel_override[channel] = 'quartz-disabled'
                self._emit_event(
                    channel=channel, action=action, backend='quartz-async', rc=rc,
                    result='DISPATCH_TIMEOUT',
                    detail=(
                        f'Quartz native call did not return within {self.QUARTZ_ASYNC_TIMEOUT_SECONDS:.2f}s; '
                        'fallback disabled for this channel, control thread remains live'
                    ),
                )
                continue

            self._quartz_inflight[channel] = None
            if rc == 0:
                self._channel_override[channel] = 'quartz-async'
                self._emit_event(
                    channel=channel, action=action, backend='quartz-async', rc=rc,
                    result='DISPATCH_ACCEPTED_UNVERIFIED',
                    detail='CGEventPost returned success; LoginWindow delivery is not independently verified',
                )
            else:
                self._quartz_disabled.add(channel)
                self._channel_override[channel] = 'quartz-disabled'
                self._emit_event(
                    channel=channel, action=action, backend='quartz-async', rc=rc,
                    result='DISPATCH_FAIL',
                    detail='Quartz fallback returned failure' + (f': {error}' if error else ''),
                )

    def _dispatch_with_failover(
        self, operation: str, channel: str, send_current, *, action: str,
        send_quartz_direct=None, channel_local_quartz: bool = False,
    ) -> None:
        failures: list[str] = []
        override = self._channel_override.get(channel, '')
        if override in ('quartz-async', 'quartz-disabled') and send_quartz_direct is not None:
            if override == 'quartz-disabled':
                self._emit_event(
                    channel=channel, action=action, backend='quartz-async', rc=-1,
                    result='DISPATCH_UNAVAILABLE',
                    detail='Quartz fallback disabled for this channel; control thread remains live',
                )
                return
            self._queue_quartz_dispatch(channel, action, send_quartz_direct)
            return

        while self.backend != 'unavailable':
            active = self.backend

            # Quartz is diagnostic-only at LoginWindow.  Never call CGEventPost
            # synchronously from the control thread because a native stall would
            # stop every following keyboard/mouse packet.
            if active == 'quartz' and send_quartz_direct is not None:
                self._queue_quartz_dispatch(channel, action, send_quartz_direct)
                return

            try:
                rc = int(send_current())
            except Exception as exc:
                rc = -32768
                failures.append(f'{active}:{type(exc).__name__}:{exc}')
            else:
                if rc == 0:
                    self._mark_runtime_result(active, channel, rc=rc, ok=True, operation=operation)
                    self._emit_event(
                        channel=channel, action=action, backend=active, rc=rc,
                        result='DISPATCH_OK',
                    )
                    return
                failures.append(f'{active}:rc={self._rc_text(rc)}')

            preserve_selected = bool(active == 'iohidsystem' and channel_local_quartz)
            self._mark_runtime_result(
                active, channel, rc=rc, ok=False, operation=operation,
                preserve_selected_on_failure=preserve_selected,
            )

            # The physical 0.15.39 test proved that IOHIDSystem pointer dispatch
            # can fail with kIOReturnNotPrivileged while the keyboard route still
            # needs to be tried.  Keep the global IOHIDSystem backend intact and
            # isolate only the failing pointer/scroll channel behind async Quartz.
            if active == 'iohidsystem' and channel_local_quartz and send_quartz_direct is not None:
                self._emit_event(
                    channel=channel, action=action, backend=active, rc=rc,
                    result='DISPATCH_FAIL', detail='trying isolated per-channel Quartz fallback',
                )
                self._queue_quartz_dispatch(channel, action, send_quartz_direct)
                return

            next_index = self._backend_index(active) + 1
            if not self._activate_from_index(next_index):
                break
        detail = '; '.join(failures) or 'no backend available'
        raise RuntimeError(f'{operation} unavailable after LoginWindow backend failover: {detail}')

    def backend_summary(self) -> str:
        label = self.BACKEND_LABELS.get(self.backend, self.backend)
        if self.backend == 'unavailable':
            return 'unavailable — no LoginWindow input backend opened'
        return f'{label}; OPEN_READY; real-event dispatch verification active'

    def input_probe_states(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.input_probe_results]

    def _ensure_backend(self) -> None:
        if self.backend == 'unavailable':
            matrix = '; '.join(
                f'{row.get("backend")}:open={self._rc_text(row.get("open_rc"))},result={row.get("result")}'
                for row in self.input_probe_results
            )
            raise RuntimeError(f'LoginWindow input unavailable after backend activation: {matrix}')

    @staticmethod
    def _axis(value: float) -> int:
        return int(round(min(1.0, max(0.0, float(value))) * 32767.0))

    def _button_mask(self) -> int:
        mask = 0
        if 1 in self.held_buttons:
            mask |= 0x01
        if 3 in self.held_buttons:
            mask |= 0x02
        if 2 in self.held_buttons:
            mask |= 0x04
        return mask

    def _legacy_xy(self) -> tuple[int, int]:
        bounds = self._bounds()
        width = max(1.0, float(bounds.size.width))
        height = max(1.0, float(bounds.size.height))
        x = int(round(float(bounds.origin.x) + self.last_nx * max(0.0, width - 1.0)))
        y = int(round(float(bounds.origin.y) + self.last_ny * max(0.0, height - 1.0)))
        return x, y

    def _send_pointer_current(
        self, *, wheel: int = 0, explicit_event: tuple[int, int] | None = None,
        relative_delta: tuple[int, int] | None = None,
    ) -> int:
        if self.backend == 'driverkit':
            dx, dy = relative_delta or (0, 0)
            if dx or dy:
                for step_x, step_y in chunk_relative(dx, dy):
                    rc = int(self.virtual_hid.post_pointing(
                        self._button_mask(), step_x, step_y, int(wheel), 0
                    ))
                    if rc != 0:
                        return rc
                    wheel = 0
                return 0
            return int(self.virtual_hid.post_pointing(
                self._button_mask(), 0, 0, int(wheel), 0
            ))
        if self.backend == 'corehid':
            return int(self.native.lib.zmb_corehid_input_pointer(
                self._axis(self.last_nx), self._axis(self.last_ny), self._button_mask(), int(wheel)
            ))
        if self.backend == 'userdevice':
            return int(self.native.lib.zmb_login_input_hid_pointer(
                self._axis(self.last_nx), self._axis(self.last_ny), self._button_mask(), int(wheel)
            ))
        if wheel:
            return int(self.native.lib.zmb_login_input_scroll(0, int(wheel), ctypes.c_uint32(self._flags())))
        if self.backend == 'iohidsystem':
            if explicit_event is not None:
                _etype, button = explicit_event
                down = _etype in (self.LEFT_DOWN, self.RIGHT_DOWN, self.OTHER_DOWN)
                return int(self.native.lib.zmb_login_input_button(
                    int(button), 1 if down else 0, ctypes.c_uint32(self._flags())
                ))
            dx, dy = relative_delta or (0, 0)
            return int(self.native.lib.zmb_login_input_pointer_relative(
                int(dx), int(dy), ctypes.c_uint8(self._button_mask()), ctypes.c_uint32(self._flags())
            ))
        x, y = self._legacy_xy()
        if explicit_event is not None:
            etype, button = explicit_event
        elif 1 in self.held_buttons:
            etype, button = self.LEFT_DRAGGED, 0
        elif 3 in self.held_buttons:
            etype, button = self.RIGHT_DRAGGED, 1
        elif 2 in self.held_buttons:
            etype, button = self.OTHER_DRAGGED, 2
        else:
            etype, button = self.MOUSE_MOVED, 0
        return int(self.native.lib.zmb_login_input_pointer(
            ctypes.c_uint32(etype), x, y, int(button), ctypes.c_uint32(self._flags())
        ))

    def _send_quartz_pointer_direct(self, *, wheel: int = 0, explicit_event: tuple[int, int] | None = None) -> int:
        if wheel:
            return int(self.native.lib.zmb_login_input_quartz_scroll(
                0, int(wheel), ctypes.c_uint32(self._flags())
            ))
        x, y = self._legacy_xy()
        if explicit_event is not None:
            etype, button = explicit_event
        elif 1 in self.held_buttons:
            etype, button = self.LEFT_DRAGGED, 0
        elif 3 in self.held_buttons:
            etype, button = self.RIGHT_DRAGGED, 1
        elif 2 in self.held_buttons:
            etype, button = self.OTHER_DRAGGED, 2
        else:
            etype, button = self.MOUSE_MOVED, 0
        return int(self.native.lib.zmb_login_input_quartz_pointer(
            ctypes.c_uint32(etype), x, y, int(button), ctypes.c_uint32(self._flags())
        ))

    def _pointer_report(
        self, *, wheel: int = 0, explicit_event: tuple[int, int] | None = None,
        relative_delta: tuple[int, int] | None = None, action: str = 'move',
    ) -> None:
        self._ensure_backend()
        self._dispatch_with_failover(
            'login-window pointer report', 'pointer',
            lambda: self._send_pointer_current(
                wheel=wheel, explicit_event=explicit_event, relative_delta=relative_delta,
            ),
            action=action,
            send_quartz_direct=lambda: self._send_quartz_pointer_direct(
                wheel=wheel, explicit_event=explicit_event,
            ),
            channel_local_quartz=True,
        )

    def move(self, nx: float, ny: float) -> None:
        old_x, old_y = self._legacy_xy()
        self.last_nx = min(1.0, max(0.0, float(nx)))
        self.last_ny = min(1.0, max(0.0, float(ny)))
        new_x, new_y = self._legacy_xy()
        self._pointer_report(relative_delta=(new_x - old_x, new_y - old_y), action='move')

    def button(self, button_num: int, down: bool, nx: float, ny: float) -> None:
        if int(button_num) not in (1, 2, 3):
            return
        self.last_nx = min(1.0, max(0.0, float(nx)))
        self.last_ny = min(1.0, max(0.0, float(ny)))
        button_num = int(button_num)
        if down:
            self.held_buttons.add(button_num)
        else:
            self.held_buttons.discard(button_num)
        if button_num == 1:
            etype, button = (self.LEFT_DOWN if down else self.LEFT_UP), 0
        elif button_num == 3:
            etype, button = (self.RIGHT_DOWN if down else self.RIGHT_UP), 1
        else:
            etype, button = (self.OTHER_DOWN if down else self.OTHER_UP), 2
        self._pointer_report(
            explicit_event=(etype, button),
            action='button-down' if down else 'button-up',
        )

    def scroll(self, dx: int, dy: int) -> None:
        self._ensure_backend()
        # Preserve horizontal scroll for native legacy backends; HID reports use
        # the existing signed vertical wheel byte.
        if self.backend in ('iohidsystem', 'quartz'):
            self._dispatch_with_failover(
                'login-window scroll report', 'pointer',
                lambda: int(self.native.lib.zmb_login_input_scroll(
                    int(dx), int(dy), ctypes.c_uint32(self._flags())
                )) if self.backend in ('iohidsystem', 'quartz') else self._send_pointer_current(wheel=int(dy)),
                action='scroll',
                send_quartz_direct=lambda: int(self.native.lib.zmb_login_input_quartz_scroll(
                    int(dx), int(dy), ctypes.c_uint32(self._flags())
                )),
                channel_local_quartz=True,
            )
            return
        remaining = int(dy)
        while remaining:
            step = max(-127, min(127, remaining))
            self._pointer_report(wheel=step, action='scroll')
            remaining -= step

    def _send_key_current(self, keysym: str, down: bool) -> int:
        is_mod = keysym in self.MOD_FLAGS
        if self.backend == 'driverkit':
            usage = self.HID_USAGES.get(keysym)
            if usage is None and len(keysym) == 1:
                usage = self.HID_USAGES.get(keysym.lower())
            if usage is None:
                return 0
            if 0xE0 <= int(usage) <= 0xE7:
                modifier_bits = 0
                for name in self.held_modifiers:
                    mod_usage = self.HID_USAGES.get(name)
                    if mod_usage is not None and 0xE0 <= mod_usage <= 0xE7:
                        modifier_bits |= 1 << (mod_usage - 0xE0)
            else:
                if down:
                    self._driverkit_keys.add(int(usage))
                else:
                    self._driverkit_keys.discard(int(usage))
                modifier_bits = 0
                for name in self.held_modifiers:
                    mod_usage = self.HID_USAGES.get(name)
                    if mod_usage is not None and 0xE0 <= mod_usage <= 0xE7:
                        modifier_bits |= 1 << (mod_usage - 0xE0)
            return int(self.virtual_hid.post_keyboard(modifier_bits, self._driverkit_keys))
        if self.backend in ('corehid', 'userdevice'):
            usage = self.HID_USAGES.get(keysym)
            if usage is None and len(keysym) == 1:
                usage = self.HID_USAGES.get(keysym.lower())
            if usage is None:
                return 0
            if self.backend == 'corehid':
                return int(self.native.lib.zmb_corehid_input_key(int(usage), 1 if down else 0))
            return int(self.native.lib.zmb_login_input_hid_key(int(usage), 1 if down else 0))
        code = self.KEYCODES.get(keysym)
        if code is None and len(keysym) == 1:
            code = self.KEYCODES.get(keysym.lower())
        if code is None:
            return 0
        return int(self.native.lib.zmb_login_input_key(
            int(code), 1 if down else 0, ctypes.c_uint32(self._flags()), 1 if is_mod else 0
        ))

    def _send_quartz_key_direct(self, keysym: str, down: bool) -> int:
        code = self.KEYCODES.get(keysym)
        if code is None and len(keysym) == 1:
            code = self.KEYCODES.get(keysym.lower())
        if code is None:
            return 0
        return int(self.native.lib.zmb_login_input_quartz_key(
            int(code), 1 if down else 0, ctypes.c_uint32(self._flags())
        ))

    def key(self, keysym: str, down: bool) -> None:
        self._ensure_backend()
        is_mod = keysym in self.MOD_FLAGS
        if is_mod and down:
            self.held_modifiers.add(keysym)
        elif is_mod and not down:
            self.held_modifiers.discard(keysym)
        self._dispatch_with_failover(
            'login-window keyboard report', 'keyboard',
            lambda: self._send_key_current(keysym, down),
            action='key-down' if down else 'key-up',
            send_quartz_direct=lambda: self._send_quartz_key_direct(keysym, down),
        )

    def text(self, text: str) -> None:
        for char in text:
            shift = False
            if 'a' <= char <= 'z' or '0' <= char <= '9':
                key = char
            elif 'A' <= char <= 'Z':
                key, shift = char.lower(), True
            elif char in self._SHIFTED_ASCII:
                key, shift = self._SHIFTED_ASCII[char], True
            else:
                key = self._PLAIN_ASCII.get(char)
            if not key:
                raise RuntimeError('LoginWindow TEXT supports printable ASCII only; use physical key events for this character')
            if shift:
                self.key('Shift_L', True)
            try:
                self.key(key, True)
                self.key(key, False)
            finally:
                if shift:
                    self.key('Shift_L', False)

    def release_all(self) -> None:
        if self.backend == 'driverkit':
            try:
                self.virtual_hid.release_all()
            except Exception:
                pass
            self._driverkit_keys.clear()
            self.held_modifiers.clear()
            self.held_buttons.clear()
            self._released = True
            self._close_all_backends()
            return
        self._released = True
        for key in list(self.held_modifiers):
            try:
                self.key(key, False)
            except Exception:
                pass
        self.held_modifiers.clear()
        if self.held_buttons:
            self.held_buttons.clear()
            try:
                self._pointer_report()
            except Exception:
                pass
        self._close_all_backends()


class MacClipboard:
    def __init__(self) -> None:
        self.pbcopy = shutil.which('pbcopy') or '/usr/bin/pbcopy'
        self.pbpaste = shutil.which('pbpaste') or '/usr/bin/pbpaste'

    def set_text(self, text: str) -> None:
        data = text.encode('utf-8')
        if len(data) > MAX_CLIPBOARD:
            raise ValueError('clipboard text too large')
        subprocess.run([self.pbcopy], input=data, check=True, timeout=3)

    def get_text(self) -> str:
        proc = subprocess.run([self.pbpaste], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
        data = proc.stdout[:MAX_CLIPBOARD]
        return data.decode('utf-8', 'replace')


def capability_probe_library() -> Path:
    """Return the Stage-B diagnostic dylib without loading it during normal runtime.

    The probe dylib is loaded into the existing app process so TCC and
    code-signing checks observe the exact ZorinMacBridge Server identity. Its
    CoreHID implementation is the same source file used by the production
    LoginWindow streamer payload.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidate = Path(getattr(sys, '_MEIPASS')) / 'libzmb_capability_probe.dylib'
    else:
        candidate = Path(__file__).resolve().parent / 'build' / 'libzmb_capability_probe.dylib'
    if not candidate.is_file():
        raise RuntimeError(
            'Stage-B capability probe library is missing. Release builds must bundle '
            'libzmb_capability_probe.dylib; source builds can compile it with '
            'scripts/build-macos-capability-probe.sh.'
        )
    return candidate


class CapabilityProbeLibrary:
    """Load the diagnostic-only native probe into the existing app process."""
    def __init__(self) -> None:
        self.path = capability_probe_library()
        self.lib = ctypes.CDLL(str(self.path))
        self.lib.zmb_capability_probe_run.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.lib.zmb_capability_probe_run.restype = ctypes.c_int32

    def run(self, output_path: Path, image_path: Path) -> int:
        return int(self.lib.zmb_capability_probe_run(
            os.fsencode(str(output_path)), os.fsencode(str(image_path))
        ))


def streamer_library() -> Path:
    """Return the bundled in-process ScreenCaptureKit/VideoToolbox library.

    Screen capture must execute inside the main ZorinMacBridge Server process so
    macOS TCC sees one stable app identity. A separate capture subprocess can be
    treated as separate responsible code and trigger another Screen Recording
    prompt when a client opens the video channel.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidate = Path(getattr(sys, '_MEIPASS')) / 'libzmb_streamer.dylib'
    else:
        candidate = Path(__file__).resolve().parent / 'build' / 'libzmb_streamer.dylib'
    if not candidate.is_file():
        raise RuntimeError(
            'Native in-process H.264 streaming library is missing. Release builds must bundle '
            'libzmb_streamer.dylib; source builds can compile it with scripts/build-macos-streamer.sh.'
        )
    return candidate


class NativeStreamerLibrary:
    """ctypes wrapper around the Swift dylib loaded into this server process."""
    def __init__(self) -> None:
        self.path = streamer_library()
        self.lib = ctypes.CDLL(str(self.path))
        self.lib.zmb_streamer_create.argtypes = [
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32
        ]
        self.lib.zmb_streamer_create.restype = ctypes.c_void_p
        self.lib.zmb_streamer_run.argtypes = [ctypes.c_void_p]
        self.lib.zmb_streamer_run.restype = ctypes.c_int32
        self.lib.zmb_streamer_stop.argtypes = [ctypes.c_void_p]
        self.lib.zmb_streamer_stop.restype = None
        self.lib.zmb_streamer_destroy.argtypes = [ctypes.c_void_p]
        self.lib.zmb_streamer_destroy.restype = None
        self.lib.zmb_virtual_display_ensure.argtypes = [ctypes.c_int32, ctypes.c_int32]
        self.lib.zmb_virtual_display_ensure.restype = ctypes.c_uint32
        self.lib.zmb_virtual_display_release.argtypes = []
        self.lib.zmb_virtual_display_release.restype = None
        self.lib.zmb_virtual_display_current_id.argtypes = []
        self.lib.zmb_virtual_display_current_id.restype = ctypes.c_uint32
        self.lib.zmb_virtual_display_last_error.argtypes = []
        self.lib.zmb_virtual_display_last_error.restype = ctypes.c_void_p
        self.lib.zmb_display_list_json.argtypes = []
        self.lib.zmb_display_list_json.restype = ctypes.c_void_p
        self.lib.zmb_string_free.argtypes = [ctypes.c_void_p]
        self.lib.zmb_string_free.restype = None
        self.lib.zmb_streamer_build_version.argtypes = []
        self.lib.zmb_streamer_build_version.restype = ctypes.c_void_p
        # LoginWindow production input is an Apple-managed CoreHID virtual
        # keyboard/pointer compiled into the same signed native payload.
        self.lib.zmb_corehid_input_open.argtypes = []
        self.lib.zmb_corehid_input_open.restype = ctypes.c_int32
        self.lib.zmb_corehid_input_close.argtypes = []
        self.lib.zmb_corehid_input_close.restype = None
        self.lib.zmb_corehid_input_key.argtypes = [ctypes.c_uint16, ctypes.c_int32]
        self.lib.zmb_corehid_input_key.restype = ctypes.c_int32
        self.lib.zmb_corehid_input_pointer.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8, ctypes.c_int32]
        self.lib.zmb_corehid_input_pointer.restype = ctypes.c_int32
        # Built-in native fallback for LoginWindow when CoreHID virtual devices
        # cannot be created (for example on an ad-hoc/Aqua release).
        self.lib.zmb_login_input_open.argtypes = []
        self.lib.zmb_login_input_open.restype = ctypes.c_int32
        self.lib.zmb_login_input_open_backend.argtypes = [ctypes.c_int32]
        self.lib.zmb_login_input_open_backend.restype = ctypes.c_int32
        self.lib.zmb_login_input_backend.argtypes = []
        self.lib.zmb_login_input_backend.restype = ctypes.c_int32
        self.lib.zmb_login_input_hid_key.argtypes = [ctypes.c_uint16, ctypes.c_int32]
        self.lib.zmb_login_input_hid_key.restype = ctypes.c_int32
        self.lib.zmb_login_input_hid_pointer.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8, ctypes.c_int32]
        self.lib.zmb_login_input_hid_pointer.restype = ctypes.c_int32
        self.lib.zmb_login_input_close.argtypes = []
        self.lib.zmb_login_input_close.restype = None
        self.lib.zmb_login_input_key.argtypes = [ctypes.c_uint16, ctypes.c_int32, ctypes.c_uint32, ctypes.c_int32]
        self.lib.zmb_login_input_key.restype = ctypes.c_int32
        self.lib.zmb_login_input_pointer.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_pointer.restype = ctypes.c_int32
        self.lib.zmb_login_input_pointer_relative.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8, ctypes.c_uint32]
        self.lib.zmb_login_input_pointer_relative.restype = ctypes.c_int32
        self.lib.zmb_login_input_button.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_button.restype = ctypes.c_int32
        self.lib.zmb_login_input_scroll.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_scroll.restype = ctypes.c_int32
        self.lib.zmb_login_input_quartz_key.argtypes = [ctypes.c_uint16, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_quartz_key.restype = ctypes.c_int32
        self.lib.zmb_login_input_quartz_pointer.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_quartz_pointer.restype = ctypes.c_int32
        self.lib.zmb_login_input_quartz_scroll.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_quartz_scroll.restype = ctypes.c_int32

    def _owned_string(self, fn) -> str:
        pointer = fn()
        if not pointer:
            return ''
        try:
            return ctypes.string_at(pointer).decode('utf-8', 'replace')
        finally:
            self.lib.zmb_string_free(pointer)

    def build_version(self) -> str:
        value = self._owned_string(self.lib.zmb_streamer_build_version)
        return value or 'unknown'

    def ensure_virtual_display(self, width: int, height: int) -> int:
        display_id = int(self.lib.zmb_virtual_display_ensure(int(width), int(height)))
        if display_id <= 0:
            detail = self._owned_string(self.lib.zmb_virtual_display_last_error)
            raise RuntimeError('virtual display unavailable' + (f': {detail}' if detail else ''))
        return display_id

    def release_virtual_display(self) -> None:
        self.lib.zmb_virtual_display_release()

    def current_virtual_display_id(self) -> int:
        return int(self.lib.zmb_virtual_display_current_id())

    def displays(self) -> list[dict]:
        pointer = self.lib.zmb_display_list_json()
        if not pointer:
            return []
        try:
            raw = ctypes.string_at(pointer).decode('utf-8', 'replace')
            data = json.loads(raw)
            if not isinstance(data, list):
                return []
            rows = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    display_id = int(item.get('id', 0))
                    width = int(item.get('width', 0))
                    height = int(item.get('height', 0))
                except (TypeError, ValueError):
                    continue
                if display_id <= 0 or width <= 0 or height <= 0:
                    continue
                rows.append({
                    'id': display_id,
                    'width': width,
                    'height': height,
                    'builtin': bool(item.get('builtin')),
                    'main': bool(item.get('main')),
                    'virtual': bool(item.get('virtual')),
                })
            return rows
        finally:
            self.lib.zmb_string_free(pointer)


def available_displays(*, log=print) -> list[dict]:
    try:
        return [item for item in NativeStreamerLibrary().displays() if not item.get('virtual')]
    except Exception as exc:
        log(f'[video] display enumeration unavailable: {type(exc).__name__}: {exc}')
        return []


def _prepare_video_capture_display(native: NativeStreamerLibrary, display_id: int, target_width: int, target_height: int, *, prelogin: bool, log=print) -> int:
    """Select the native capture display without touching Aqua-only virtual state from LoginWindow.

    The pre-login helper is a separate LoginWindow process after reboot. It does
    not own the Aqua virtual-display singleton and must never call the Swift
    release path, which dispatches synchronously to the app main queue.
    """
    capture_display_id = max(0, int(display_id))
    virtual_mode = int(target_width) > 0 and int(target_height) > 0
    if virtual_mode:
        if prelogin:
            raise RuntimeError('virtual display mode is unavailable at macOS LoginWindow')
        capture_display_id = native.ensure_virtual_display(int(target_width), int(target_height))
        log(f'[video] virtual remote desktop ready: {target_width}x{target_height} display_id={capture_display_id}')
    elif prelogin:
        # Critical: do not call zmb_virtual_display_release() from LoginWindow.
        # Virtual display ownership exists only in the Aqua server process.
        log(f'[video] LoginWindow physical display selected: display_id={capture_display_id or "auto"}')
    else:
        native.release_virtual_display()
        log(f'[video] physical display mode selected: display_id={capture_display_id or "auto"}')
    return capture_display_id


def _read_exact_file(stream, size: int, *, timeout: float | None = 15.0, disconnect_sock: ssl.SSLSocket | None = None, cancel_event: threading.Event | None = None, idle_callback=None) -> bytes:
    """Read native pipe bytes while also noticing a closed video client.

    Reconfiguration closes the old TLS video socket before opening the next one.
    The native encoder can be quiet for many seconds on an unchanged screen, so
    waiting only on its pipe used to keep the old streamer alive until the 15 s
    deadline. Monitor the TLS socket in the same select loop so cleanup starts as
    soon as the old client disconnects.
    """
    out = bytearray()
    deadline = None if timeout is None else time.monotonic() + max(0.5, float(timeout))
    fd = stream.fileno()
    while len(out) < size:
        if cancel_event is not None and cancel_event.is_set():
            raise ConnectionError('video session cancelled for replacement')
        if idle_callback is not None:
            idle_callback()
        if deadline is None:
            wait_for = 0.1 if idle_callback is not None else 0.5
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'native streamer pipe stalled for {timeout:g}s')
            wait_for = min(0.1 if idle_callback is not None else 0.5, remaining)
        watchers = [fd]
        if disconnect_sock is not None:
            watchers.append(disconnect_sock)
        readable, _, _ = select.select(watchers, [], [], wait_for)
        if cancel_event is not None and cancel_event.is_set():
            raise ConnectionError('video session cancelled for replacement')
        if not readable:
            continue
        if disconnect_sock is not None and disconnect_sock in readable:
            try:
                probe = disconnect_sock.recv(1)
            except (ssl.SSLWantReadError, BlockingIOError, socket.timeout):
                probe = None
            except (ssl.SSLError, OSError) as exc:
                raise ConnectionError(f'video client disconnected during native capture: {exc}') from exc
            if probe == b'':
                raise ConnectionError('video client disconnected during native capture')
            if probe:
                raise ConnectionError('unexpected client data on one-way video stream')
        if fd not in readable:
            continue
        chunk = os.read(fd, size - len(out))
        if not chunk:
            raise EOFError('native streamer closed its output')
        out.extend(chunk)
    return bytes(out)


def control_session(sock: ssl.SSLSocket, *, prelogin: bool = False, prelogin_lease: PreloginControlLease | None = None, log=print) -> None:
    """Low-latency input/clipboard channel. Video is deliberately separate."""
    log('[control] connected')
    if prelogin:
        log('[control] initializing LoginWindow native input')
        last_pointer_move_log_at = 0.0

        def emit_input_state(row: dict[str, object]) -> None:
            backend = str(row.get('backend') or '')
            keyboard = str(row.get('keyboard') or '')
            pointer = str(row.get('pointer') or '')
            open_rc = LoginWindowInput._rc_text(row.get('open_rc'))
            selected = bool(row.get('selected'))
            result = str(row.get('result') or '')
            reason = str(row.get('reason') or '')
            log(
                '[prelogin/input] '
                f'backend={backend} open={open_rc} result={result} '
                f'keyboard={keyboard} pointer={pointer} selected={int(selected)}'
            )
            try:
                sock.sendall(pack_json(SESSION_STATE, make_input_probe_state(
                    backend, result=result, keyboard=keyboard, pointer=pointer,
                    open_rc=open_rc, selected=selected, reason=reason,
                )))
            except OSError as exc:
                raise ConnectionError('client disconnected while receiving pre-login input state') from exc

        def emit_input_event(event: dict[str, object]) -> None:
            nonlocal last_pointer_move_log_at
            channel = str(event.get('channel') or '')
            action = str(event.get('action') or '')
            backend = str(event.get('backend') or '')
            result = str(event.get('result') or '')
            rc = LoginWindowInput._rc_text(event.get('rc'))
            detail = str(event.get('detail') or '')
            if channel == 'pointer' and action == 'move' and result in ('DISPATCH_OK', 'DISPATCH_ACCEPTED_UNVERIFIED'):
                now = time.monotonic()
                if now - last_pointer_move_log_at < 0.5:
                    return
                last_pointer_move_log_at = now
            log(
                '[prelogin/input-event] '
                f'channel={channel} action={action} backend={backend} result={result} rc={rc}'
                + (f' detail={detail}' if detail else '')
            )
            try:
                sock.sendall(pack_json(SESSION_STATE, make_input_event_state(
                    channel, action, backend, result=result, rc=rc, detail=detail,
                )))
            except OSError as exc:
                raise ConnectionError('client disconnected while receiving pre-login input event state') from exc

        inp = LoginWindowInput()
        set_state_callback = getattr(inp, 'set_state_callback', None)
        if callable(set_state_callback):
            set_state_callback(emit_input_state, replay=True)
        else:
            probe_states = getattr(inp, 'input_probe_states', None)
            if callable(probe_states):
                for row in probe_states():
                    emit_input_state(row)
        set_event_callback = getattr(inp, 'set_event_callback', None)
        if callable(set_event_callback):
            set_event_callback(emit_input_event)
        backend_summary = getattr(inp, 'backend_summary', None)
        backend_name = backend_summary() if callable(backend_summary) else type(inp).__name__
        log(f'[control] LoginWindow input backend: {backend_name}')
        clipboard = None
    else:
        log('[control] initializing CoreGraphics input')
        inp = MacInput()
        log('[control] CoreGraphics input initialized')
        clipboard = MacClipboard()
    reader = PacketReader()
    sock.settimeout(0.25)
    try:
        while True:
            drain_async = getattr(inp, 'drain_async_events', None)
            if callable(drain_async):
                drain_async()
            if prelogin_lease is not None and prelogin_lease.handoff_event.is_set():
                payload = make_handoff_state(
                    user=prelogin_lease.handoff_user, generation=prelogin_lease.generation,
                )
                sock.sendall(pack_json(SESSION_STATE, payload))
                prelogin_lease.handoff_sent_event.set()
                log(
                    f'[prelogin/handoff] control handoff sent generation={prelogin_lease.generation} '
                    f'user={prelogin_lease.handoff_user or "unknown"}'
                )
                return
            try:
                data = sock.recv(65536)
                if not data:
                    raise ConnectionError('client disconnected')
            except socket.timeout:
                continue
            for kind, payload in reader.feed(data):
                if kind == MOUSE_MOVE and len(payload) == 8:
                    nx, ny = struct.unpack('!ff', payload)
                    try:
                        inp.move(nx, ny)
                    except Exception as exc:
                        log(f'[control] pointer move rejected: {type(exc).__name__}: {exc}')
                elif kind == MOUSE_BUTTON and len(payload) == 10:
                    button, down, nx, ny = struct.unpack('!BBff', payload)
                    try:
                        inp.button(button, bool(down), nx, ny)
                    except Exception as exc:
                        log(f'[control] mouse button rejected: {type(exc).__name__}: {exc}')
                elif kind == SCROLL and len(payload) == 8:
                    dx, dy = struct.unpack('!ii', payload)
                    try:
                        inp.scroll(dx, dy)
                    except Exception as exc:
                        log(f'[control] scroll event rejected: {type(exc).__name__}: {exc}')
                elif kind == KEY:
                    msg = unpack_json(payload)
                    try:
                        inp.key(str(msg.get('keysym', '')), bool(msg.get('down')))
                    except Exception as exc:
                        log(f'[control] keyboard event rejected: {type(exc).__name__}: {exc}')
                elif kind == TEXT:
                    try:
                        inp.text(payload.decode('utf-8', 'replace'))
                    except Exception as exc:
                        log(f'[control] text event rejected: {type(exc).__name__}: {exc}')
                elif kind == CLIPBOARD_SET and clipboard is not None:
                    clipboard.set_text(payload.decode('utf-8', 'replace'))
                elif kind == CLIPBOARD_GET and clipboard is not None:
                    text = clipboard.get_text()
                    sock.sendall(pack_packet(CLIPBOARD_DATA, text.encode('utf-8')))
    finally:
        inp.release_all()
        log('[control] disconnected')


def video_session(sock: ssl.SSLSocket, fps: float, max_width: int, bitrate: int, display_id: int = 0, target_width: int = 0, target_height: int = 0, *, prelogin: bool = False, log=print, release_ownership=None, cancel_event: threading.Event | None = None) -> None:
    """Stream H.264 from ScreenCaptureKit running inside the main app process.

    The native Swift code is a dylib loaded with ctypes, not a child executable.
    Screen Recording authorization therefore belongs to ZorinMacBridge Server.
    Crucially, never trigger the macOS permission prompt from a remote Connect:
    if this exact running build is not already authorized, fail before touching
    ScreenCaptureKit and tell the client what the operator must do locally.
    """
    permission = screen_capture_permission_status()
    if permission is False:
        if prelogin:
            raise RuntimeError(
                'Screen Recording is not granted to ZorinMacBridge Server in the LoginWindow session. '
                'Grant Screen & System Audio Recording to ZorinMacBridge Server while logged in, then reboot. '
                'Pre-login video cannot silently bypass macOS TCC.'
            )
        raise RuntimeError(
            'Screen Recording is not granted to this exact ZorinMacBridge Server build. '
            'Open Privacy & Security > Screen Recording on the Mac, enable ZorinMacBridge Server, '
            'then fully quit and reopen the app. The server will not request permission from a remote Connect.'
        )
    native = NativeStreamerLibrary()
    native_version = native.build_version()
    app_version = runtime_version()
    if native_version != app_version:
        raise RuntimeError(f'native streamer build version mismatch: app={app_version} dylib={native_version}')
    log(f'[video/native] build version verified: {native_version}')
    if prelogin:
        # LoginWindow always exposes the physical macOS login screen.  A user's
        # saved 2K/4K preference is intentionally deferred until Aqua takes over.
        display_id = 0
        target_width = 0
        target_height = 0
        max_width = min(max(640, int(max_width)), 1920)
        fps = min(max(1.0, float(fps)), 30.0)
        bitrate = min(max(1_000_000, int(bitrate)), 8_000_000)
    capture_display_id = _prepare_video_capture_display(
        native, display_id, target_width, target_height, prelogin=prelogin, log=log,
    )
    set_active_input_display(capture_display_id)

    data_r, data_w = os.pipe()
    log_r, log_w = os.pipe()
    # Give Swift its own descriptors so Python can close its local write ends.
    native_data_w = os.dup(data_w)
    native_log_w = os.dup(log_w)
    os.close(data_w)
    os.close(log_w)

    handle = native.lib.zmb_streamer_create(
        max(1, min(int(round(fps)), 60)),
        max(640, int(max_width)),
        max(1_000_000, int(bitrate)),
        max(0, min(int(target_width), 3840)),
        max(0, min(int(target_height), 2160)),
        max(0, min(int(capture_display_id), 0xFFFFFFFF)),
        1 if prelogin else 0,
        native_data_w,
        native_log_w,
    )
    if not handle:
        os.close(data_r)
        os.close(log_r)
        os.close(native_data_w)
        os.close(native_log_w)
        raise RuntimeError('failed to create in-process ScreenCaptureKit streamer')

    result: dict[str, int] = {'code': -999}
    native_messages: list[str] = []
    session_events: queue.SimpleQueue[dict] = queue.SimpleQueue()

    def run_native() -> None:
        try:
            result['code'] = int(native.lib.zmb_streamer_run(handle))
        except Exception as exc:
            result['code'] = -998
            log(f'[video/native] dylib run failed: {exc}')

    def log_worker() -> None:
        try:
            with os.fdopen(log_r, 'rb', buffering=0) as stream:
                for raw in iter(stream.readline, b''):
                    line = raw.decode('utf-8', 'replace').rstrip()
                    if line:
                        native_messages.append(line)
                        if len(native_messages) > 20:
                            del native_messages[:-20]
                        if prelogin and line.startswith('prelogin-stage='):
                            stage = line.split('=', 1)[1]
                            if stage in PIPELINE_STAGES:
                                session_events.put(make_pipeline_state(stage))
                            else:
                                session_events.put(make_capture_progress_state(stage))
                        elif prelogin and line.startswith('prelogin-backend='):
                            session_events.put(make_capture_backend_state(line.split('=', 1)[1]))
                        elif prelogin and line.startswith('prelogin-failure='):
                            session_events.put(make_capture_failure_state(line.split('=', 1)[1]))
                        log('[video/native] ' + line)
        except Exception:
            pass

    def flush_pipeline_events() -> None:
        if not prelogin:
            return
        while True:
            try:
                event = session_events.get_nowait()
            except queue.Empty:
                return
            sock.sendall(pack_json(SESSION_STATE, event))

    runner = threading.Thread(target=run_native, name='zmb-native-video', daemon=True)
    logger = threading.Thread(target=log_worker, name='zmb-native-video-log', daemon=True)
    log('[video] starting in-process ScreenCaptureKit + VideoToolbox H.264 stream')
    if prelogin:
        sock.sendall(pack_json(SESSION_STATE, make_capture_progress_state('native-runner-starting')))
    logger.start()
    runner.start()
    if prelogin:
        sock.sendall(pack_json(SESSION_STATE, make_capture_progress_state('native-runner-started')))

    sock.settimeout(10.0)
    frames = 0
    stat_started = time.monotonic()
    stat_frames = 0
    stat_bytes = 0
    try:
        with os.fdopen(data_r, 'rb', buffering=0) as stream:
            while True:
                startup_timeout = VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS if frames == 0 else None
                try:
                    header = _read_exact_file(stream, 4, timeout=startup_timeout, disconnect_sock=sock, cancel_event=cancel_event, idle_callback=flush_pipeline_events)
                except EOFError:
                    runner.join(timeout=0.25)
                    code = result.get('code', -999)
                    detail = next((m for m in reversed(native_messages) if m.startswith(('fatal=', 'stream-stopped', 'encode-error', 'pipe-write-error'))), '')
                    suffix = f': {detail}' if detail else ''
                    raise RuntimeError(f'native in-process H.264 streamer stopped (code={code}){suffix}')
                except TimeoutError as exc:
                    if frames == 0:
                        detail = ' | '.join(native_messages[-6:]) or 'no native capture diagnostics'
                        raise TimeoutError(
                            f'no first H.264 access unit within {startup_timeout:g}s; native={detail}'
                        ) from exc
                    raise
                size = struct.unpack('!I', header)[0]
                if not (1 <= size <= 16 * 1024 * 1024):
                    raise RuntimeError(f'invalid native H.264 access-unit size: {size}')
                try:
                    payload = _read_exact_file(stream, size, timeout=startup_timeout, disconnect_sock=sock, cancel_event=cancel_event, idle_callback=flush_pipeline_events)
                except TimeoutError as exc:
                    if frames == 0:
                        detail = ' | '.join(native_messages[-6:]) or 'no native capture diagnostics'
                        raise TimeoutError(
                            f'first H.264 access unit payload stalled for {startup_timeout:g}s; native={detail}'
                        ) from exc
                    raise
                flush_pipeline_events()
                sock.sendall(pack_packet(VIDEO_H264, payload))
                frames += 1
                stat_frames += 1
                stat_bytes += len(payload)
                if frames == 1:
                    log(f'[video] first H.264 access unit sent ({len(payload)} bytes)')
                now = time.monotonic()
                elapsed = now - stat_started
                if elapsed >= 2.0:
                    sock.sendall(pack_json(VIDEO_STATS, {
                        'fps': round(stat_frames / max(0.001, elapsed), 1),
                        'mbps': round(stat_bytes * 8.0 / max(0.001, elapsed) / 1_000_000.0, 2),
                        'frames': stat_frames,
                    }))
                    stat_started = now
                    stat_frames = 0
                    stat_bytes = 0
    finally:
        # Release the Python video-owner lock before asking Swift to tear down.
        # zmb_streamer_stop must never be able to hold the next TLS video session
        # hostage if ScreenCaptureKit/VideoToolbox cleanup is slow.
        if release_ownership is not None:
            try:
                release_ownership()
            except Exception as exc:
                log(f'[video] ownership release warning: {type(exc).__name__}: {exc}')
        try:
            native.lib.zmb_streamer_stop(handle)
        except Exception as exc:
            log(f'[video] native stop warning: {type(exc).__name__}: {exc}')
        runner.join(timeout=6.0)

        def destroy_after_runner() -> None:
            try:
                if runner.is_alive():
                    runner.join()
                native.lib.zmb_streamer_destroy(handle)
            except Exception as exc:
                log(f'[video] late native destroy warning: {type(exc).__name__}: {exc}')
            for fd in (native_data_w, native_log_w):
                try:
                    os.close(fd)
                except OSError:
                    pass

        if runner.is_alive():
            log('[video] native runner still unwinding after 6s; deferring destroy without blocking replacement')
            threading.Thread(target=destroy_after_runner, name='zmb-native-video-destroy', daemon=True).start()
        else:
            destroy_after_runner()
        logger.join(timeout=1.0)
        log('[video] disconnected')


def send_error(sock: ssl.SSLSocket, message: str) -> None:
    previous_timeout = None
    try:
        previous_timeout = sock.gettimeout()
        # Desktop streaming uses a short read timeout. Do not reuse it for
        # diagnostics or the real server error may be lost before disconnect.
        sock.settimeout(2.0)
        sock.sendall(pack_packet(ERROR, message.encode('utf-8', 'replace')))
    except Exception:
        pass
    finally:
        try:
            sock.settimeout(previous_timeout)
        except Exception:
            pass


def list_dir(share: Path, rel: str) -> list[dict]:
    folder = safe_share_path(share, rel, allow_root=True)
    if folder.is_symlink() or not folder.is_dir():
        raise NotADirectoryError(rel or '/')
    items: list[dict] = []
    for p in sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            st = p.lstat()
        except OSError:
            continue
        if p.is_symlink():
            continue
        if p.is_dir():
            items.append({'name': p.name, 'kind': 'dir', 'size': 0, 'mtime': int(st.st_mtime)})
        elif p.is_file():
            items.append({'name': p.name, 'kind': 'file', 'size': st.st_size, 'mtime': int(st.st_mtime)})
    return items


def file_session(sock: ssl.SSLSocket, share: Path) -> None:
    reader = PacketReader()
    sock.settimeout(120)
    current_upload = None
    current_upload_tmp: Path | None = None
    current_upload_target: Path | None = None
    expected = 0
    received = 0
    try:
        while True:
            kind, payload = recv_one_blocking(sock, reader)
            if kind == LIST_REQ:
                rel = ''
                if payload:
                    rel = str(unpack_json(payload).get('path', ''))
                sock.sendall(pack_json(LIST_RESP, {'path': rel, 'items': list_dir(share, rel)}))

            elif kind == MKDIR_REQ:
                rel = str(unpack_json(payload).get('path', ''))
                target = safe_share_path(share, rel, allow_root=False)
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                if target.is_symlink() or not path_inside(share, target):
                    raise ValueError('invalid directory')
                sock.sendall(pack_json(MKDIR_OK, {'path': rel}))

            elif kind == UPLOAD_BEGIN:
                if current_upload is not None:
                    raise ValueError('upload already active')
                meta = unpack_json(payload)
                rel = str(meta['path'])
                expected = int(meta['size'])
                if expected < 0 or expected > MAX_FILE_SIZE:
                    raise ValueError('invalid upload size')
                target = safe_share_path(share, rel, allow_root=False)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not path_inside(share, target.parent):
                    raise ValueError('upload parent outside share')
                tmp = target.parent / f'.upload-{uuid.uuid4().hex}.part'
                current_upload = open(tmp, 'wb')
                current_upload_tmp = tmp
                current_upload_target = target
                received = 0

            elif kind == UPLOAD_CHUNK:
                if current_upload is None:
                    raise ValueError('no active upload')
                received += len(payload)
                if received > expected:
                    raise ValueError('upload exceeds declared size')
                current_upload.write(payload)

            elif kind == UPLOAD_END:
                if current_upload is None or current_upload_tmp is None or current_upload_target is None:
                    raise ValueError('no active upload')
                current_upload.flush()
                os.fsync(current_upload.fileno())
                current_upload.close()
                current_upload = None
                if received != expected:
                    current_upload_tmp.unlink(missing_ok=True)
                    raise ValueError(f'upload size mismatch: got {received}, expected {expected}')
                os.replace(current_upload_tmp, current_upload_target)
                os.chmod(current_upload_target, 0o600)
                current_upload_tmp = None
                current_upload_target = None
                sock.sendall(pack_json(UPLOAD_END, {'ok': True}))

            elif kind == DOWNLOAD_REQ:
                rel = str(unpack_json(payload).get('path', ''))
                target = safe_share_path(share, rel, allow_root=False)
                if target.is_symlink() or not target.is_file():
                    raise FileNotFoundError(rel)
                size = target.stat().st_size
                sock.sendall(pack_json(DOWNLOAD_BEGIN, {'path': rel, 'size': size}))
                with open(target, 'rb') as f:
                    while True:
                        block = f.read(CHUNK)
                        if not block:
                            break
                        sock.sendall(pack_packet(DOWNLOAD_CHUNK, block))
                sock.sendall(pack_packet(DOWNLOAD_END))

            else:
                raise ValueError(f'unsupported file packet: {kind}')
    except (ConnectionError, socket.timeout, ssl.SSLError):
        pass
    except Exception as exc:
        send_error(sock, str(exc))
    finally:
        if current_upload is not None:
            try:
                current_upload.close()
            except Exception:
                pass
        if current_upload_tmp is not None:
            current_upload_tmp.unlink(missing_ok=True)




def verify_session_password(expected, supplied: str) -> bool:
    if hasattr(expected, 'verify'):
        try:
            return bool(expected.verify(supplied))
        except Exception:
            return False
    return hmac.compare_digest(str(supplied), str(expected))

def video_options_from_auth(auth: dict, defaults) -> tuple[float, int, int, str, int, int, int]:
    """Return clamped per-client video preferences from the authenticated request.

    The client may request a preset, but the server always clamps untrusted values
    to sane LAN streaming limits. Older clients simply receive server defaults.
    """
    request = auth.get('video', {})
    if not isinstance(request, dict):
        request = {}
    try:
        fps = float(request.get('fps', defaults.fps))
    except (TypeError, ValueError):
        fps = float(defaults.fps)
    try:
        max_width = int(request.get('max_width', defaults.max_width))
    except (TypeError, ValueError):
        max_width = int(defaults.max_width)
    try:
        bitrate = int(request.get('bitrate', defaults.bitrate))
    except (TypeError, ValueError):
        bitrate = int(defaults.bitrate)
    fps = max(5.0, min(60.0, fps))
    max_width = max(960, min(3840, max_width))
    bitrate = max(1_000_000, min(40_000_000, bitrate))
    try:
        target_width = int(request.get('target_width', 0) or 0)
        target_height = int(request.get('target_height', 0) or 0)
    except (TypeError, ValueError):
        target_width = target_height = 0
    allowed_targets = {(2560, 1440), (3840, 2160)}
    if (target_width, target_height) not in allowed_targets:
        target_width = target_height = 0
    quality = str(auth.get('quality', 'Custom')).strip()[:32] or 'Custom'
    try:
        display_id = int(auth.get('display_id', 0) or 0)
    except (TypeError, ValueError):
        display_id = 0
    display_id = max(0, min(display_id, 0xFFFFFFFF))
    return fps, max_width, bitrate, quality, display_id, target_width, target_height


def prepare_remote_display_from_auth(auth: dict, *, log=print) -> dict:
    request = auth.get('remote_display', {})
    if not isinstance(request, dict):
        request = {}
    name = str(request.get('name') or '1080p')[:16]
    try:
        target_width = int(request.get('target_width', 0) or 0)
        target_height = int(request.get('target_height', 0) or 0)
    except (TypeError, ValueError):
        target_width = target_height = 0
    if (target_width, target_height) not in {(2560, 1440), (3840, 2160)}:
        target_width = target_height = 0
        name = '1080p'
    native = NativeStreamerLibrary()
    try:
        if target_width and target_height:
            display_id = native.ensure_virtual_display(target_width, target_height)
            set_active_input_display(display_id)
            log(f'[desktop] prepared headless remote display {name}: {target_width}x{target_height} id={display_id}')
            return {
                'ready': True, 'name': name, 'virtual': True, 'display_id': display_id,
                'width': target_width, 'height': target_height,
            }
        native.release_virtual_display()
        set_active_input_display(0)
        log('[desktop] prepared physical Mac display mode (1080p/default)')
        return {'ready': True, 'name': '1080p', 'virtual': False, 'display_id': 0, 'width': 0, 'height': 0}
    except Exception as exc:
        # Control/TLS authentication must still succeed. Display preparation is a
        # video concern and can retry without making Connect itself fail.
        detail = f'{type(exc).__name__}: {exc}'
        log(f'[desktop] remote display preparation failed: {detail}')
        return {
            'ready': False, 'name': name, 'virtual': bool(target_width and target_height),
            'display_id': 0, 'width': target_width, 'height': target_height, 'error': detail,
        }


def handle_client(raw: socket.socket, addr, context: ssl.SSLContext, password: str, share: Path, args, *, prelogin: bool = False, log=print) -> None:
    peer_ip = addr[0]
    if not is_lan_ip(peer_ip):
        log(f'[reject] non-LAN peer {peer_ip}')
        raw.close()
        return
    sock = None
    prelogin_control_lease: PreloginControlLease | None = None
    prelogin_video_token = ''
    try:
        log(f'[client {peer_ip}] TCP connection accepted')
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        raw.settimeout(10)
        sock = context.wrap_socket(raw, server_side=True)
        log(f'[client {peer_ip}] TLS handshake completed')
        kind, payload = recv_one_blocking(sock)
        if kind != AUTH:
            sock.sendall(pack_packet(AUTH_FAIL, b'auth required'))
            log(f'[client {peer_ip}] rejected: authentication packet required')
            return
        auth = unpack_json(payload)
        supplied = str(auth.get('password', ''))
        role = str(auth.get('role', ''))
        if not verify_session_password(password, supplied):
            time.sleep(1.0)
            sock.sendall(pack_packet(AUTH_FAIL, b'bad password'))
            log(f'[auth] failed from {peer_ip}')
            return
        if role not in {'desktop', 'video', 'file'}:
            sock.sendall(pack_packet(AUTH_FAIL, b'bad role'))
            log(f'[client {peer_ip}] rejected: bad role {role!r}')
            return
        if prelogin and role == 'file':
            sock.sendall(pack_packet(AUTH_FAIL, b'file role unavailable at macOS login window'))
            log(f'[client {peer_ip}] pre-login file role rejected')
            return
        if prelogin and role == 'desktop':
            try:
                prelogin_control_lease = _PRELOGIN_SESSION_COORDINATOR.acquire_control(sock, peer_ip, log=log)
            except Exception as exc:
                sock.sendall(pack_packet(AUTH_FAIL, f'pre-login control busy: {exc}'.encode('utf-8', 'replace')))
                log(f'[prelogin/session] control acquisition rejected for {peer_ip}: {type(exc).__name__}: {exc}')
                return
        if prelogin and role == 'video':
            prelogin_video_token = str(auth.get('session_token', ''))
            try:
                _PRELOGIN_SESSION_COORDINATOR.validate_video_token(prelogin_video_token, peer_ip)
            except PermissionError as exc:
                sock.sendall(pack_packet(AUTH_FAIL, str(exc).encode('utf-8', 'replace')))
                log(f'[prelogin/session] video token rejected for {peer_ip}: {exc}')
                return
        auth_ok = {'role': role, 'server': 'ZorinMac-Bridge', 'prelogin': bool(prelogin), 'runtime_version': runtime_version()}
        if role == 'desktop':
            if prelogin:
                if prelogin_control_lease is None:
                    raise RuntimeError('pre-login control lease missing after successful acquisition')
                auth_ok['session_token'] = prelogin_control_lease.token
                auth_ok['session_generation'] = prelogin_control_lease.generation
                auth_ok['remote_display'] = {
                    'ready': True, 'name': '1080p', 'virtual': False, 'display_id': 0,
                    'width': 0, 'height': 0, 'prelogin': True,
                }
                auth_ok['displays'] = available_displays(log=log)
                log('[desktop] pre-login mode: physical macOS LoginWindow display prepared')
            else:
                auth_ok['remote_display'] = prepare_remote_display_from_auth(auth, log=log)
                auth_ok['displays'] = available_displays(log=log)
        sock.sendall(pack_json(AUTH_OK, auth_ok))
        log(f'[client {peer_ip}] authenticated role={role}')
        if role == 'desktop':
            try:
                control_session(sock, prelogin=prelogin, prelogin_lease=prelogin_control_lease, log=log)
            finally:
                if not prelogin:
                    # The Aqua control channel owns the remote workspace. If it
                    # ends, restore the physical Mac display so a stale headless
                    # mode cannot survive an abandoned client session.
                    try:
                        NativeStreamerLibrary().release_virtual_display()
                    except Exception as exc:
                        log(f'[desktop] physical display restore warning: {exc}')
                    set_active_input_display(0)
        elif role == 'video':
            fps, max_width, bitrate, quality, display_id, target_width, target_height = video_options_from_auth(auth, args)
            if prelogin:
                display_id = 0
                target_width = target_height = 0
                max_width = min(max_width, 1920)
                fps = min(fps, 30.0)
                bitrate = min(bitrate, 8_000_000)
                quality = f'{quality}/prelogin'
            log(
                f'[video] client profile={quality!r} max_width={max_width} '
                f'target={target_width}x{target_height} fps={fps:g} bitrate={bitrate} display_id={display_id}'
            )
            video_cancel_event = _acquire_video_session(sock, log=log)
            video_ownership_released = False
            if prelogin:
                # Close the AUTH -> native-lock race. If the control owner vanished
                # while this video socket waited for the previous native streamer,
                # this generation must not start with a stale token. The socket is
                # already registered as active, so a later control release can also
                # interrupt it deterministically.
                _PRELOGIN_SESSION_COORDINATOR.validate_video_token(prelogin_video_token, peer_ip)

            def release_video_ownership() -> None:
                nonlocal video_ownership_released
                if video_ownership_released:
                    return
                video_ownership_released = True
                _release_video_session(sock)

            try:
                video_session(
                    sock, fps, max_width, bitrate, display_id, target_width, target_height,
                    prelogin=prelogin, log=log, release_ownership=release_video_ownership, cancel_event=video_cancel_event,
                )
            finally:
                release_video_ownership()
        else:
            file_session(sock, share)
    except (ssl.SSLError, ConnectionError, OSError) as exc:
        log(f'[client {peer_ip}] {type(exc).__name__}: {exc}')
        if sock is not None:
            send_error(sock, f'{type(exc).__name__}: {exc}')
    except Exception as exc:
        log(f'[client {peer_ip}] ERROR {type(exc).__name__}: {exc}')
        if sock is not None:
            send_error(sock, f'{type(exc).__name__}: {exc}')
    finally:
        if prelogin_control_lease is not None:
            # Interrupt video before publishing the control lease as free. Otherwise
            # a replacement Desktop could acquire a new generation and start video
            # in the tiny window before stale-owner cleanup shuts the active socket.
            _interrupt_active_video_session(log=log)
            _PRELOGIN_SESSION_COORDINATOR.release_control(prelogin_control_lease, log=log)
        try:
            if sock is not None:
                sock.close()
            else:
                raw.close()
        except Exception:
            pass


class IdleSleepInhibitor:
    """Prevent automatic system idle sleep while the remote server is alive.

    The display may still turn off and the normal lock/login policy remains in
    force. Explicit user-requested sleep is not disabled. This avoids the common
    MacBook failure mode where display-off immediately makes the LAN server
    unreachable before Wake-on-Connect can help.
    """
    def __init__(self, *, log=print) -> None:
        self.log = log
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self.proc is not None:
            return
        try:
            self.proc = subprocess.Popen(
                ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.log('[power] Remote Ready: automatic idle system sleep inhibited; display sleep/login policy unchanged.')
        except Exception as exc:
            self.proc = None
            self.log(f'[power] WARNING: could not start idle-sleep inhibitor: {type(exc).__name__}: {exc}')

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def serve(bind: str, port: int, share: Path, fps: float, max_width: int, bitrate: int, password: str, *, stop_event=None, log=print, ready_callback=None, prelogin: bool = False) -> None:
    if platform.system() != 'Darwin':
        raise RuntimeError('This server is intended for macOS only.')
    if not is_lan_ip(bind):
        raise ValueError('Bind address must be a literal private/loopback/link-local IP address.')
    if not (1 <= int(port) <= 65535):
        raise ValueError('Invalid port.')
    bitrate = max(1_000_000, min(50_000_000, int(bitrate)))
    share = Path(share).expanduser().resolve()
    if not prelogin:
        share.mkdir(mode=0o700, parents=True, exist_ok=True)

    fingerprint = ensure_certificate()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(CERT_PATH), str(KEY_PATH))

    from types import SimpleNamespace
    args = SimpleNamespace(fps=float(fps), max_width=int(max_width), bitrate=bitrate)

    listener = socket.socket(socket.AF_INET6 if ':' in bind else socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind, int(port)))
    listener.listen(8)
    listener.settimeout(0.5)

    log(f'Listening on {bind}:{port}')
    log(f'Session type: {"LoginWindow/pre-login" if prelogin else "Aqua/user"}')
    if not prelogin:
        log(f'Share directory: {share}')
    log(f'TLS SHA-256: {fingerprint}')
    log('Core server: no DNS, cloud, relay, telemetry, or Internet connections')
    log('Clients: RFC1918/ULA/link-local/loopback only')
    if ready_callback is not None:
        ready_callback(fingerprint)

    idle_sleep = IdleSleepInhibitor(log=log)
    idle_sleep.start()

    # Bound concurrent pre-auth/TLS client threads. A LAN peer should not be able
    # to exhaust the server merely by opening many sockets and waiting for the
    # handshake/auth timeout. One normal ZMB client uses only a few connections.
    client_slots = threading.BoundedSemaphore(16)

    def client_worker(raw_sock, peer_addr) -> None:
        try:
            handle_client(raw_sock, peer_addr, context, password, share, args, prelogin=prelogin, log=log)
        finally:
            client_slots.release()

    try:
        while stop_event is None or not stop_event.is_set():
            try:
                raw, addr = listener.accept()
            except socket.timeout:
                continue
            if not client_slots.acquire(blocking=False):
                log(f'[reject] connection limit reached for {addr[0]}')
                try:
                    raw.close()
                except OSError:
                    pass
                continue
            threading.Thread(
                target=client_worker,
                args=(raw, addr),
                daemon=True,
            ).start()
    finally:
        listener.close()
        idle_sleep.stop()
        if not prelogin:
            try:
                NativeStreamerLibrary().release_virtual_display()
            except Exception:
                pass
            set_active_input_display(0)
        log('Server stopped.')


def main() -> None:
    if platform.system() != 'Darwin':
        raise SystemExit('This server is intended for macOS only.')

    ap = argparse.ArgumentParser(description='LAN-only remote desktop bridge: Linux -> macOS')
    ap.add_argument('--bind', required=True, help='Private IP address of the Mac, e.g. 192.168.1.50')
    ap.add_argument('--port', type=int, default=45950)
    ap.add_argument('--share', type=Path, default=DEFAULT_SHARE)
    ap.add_argument('--fps', type=float, default=10.0)
    ap.add_argument('--max-width', type=int, default=1920)
    ap.add_argument('--bitrate', type=int, default=8_000_000, help='H.264 bitrate in bits/s')
    args = ap.parse_args()

    password = os.environ.get('ZORIN_MAC_BRIDGE_PASSWORD')
    if password is None:
        password = getpass.getpass('Session password (not stored): ')
    if len(password) < 12:
        print('WARNING: a session password of at least 12 characters is recommended.')

    print('\nZorinMacBridge server')
    print('macOS must grant this app/Terminal Screen Recording and Accessibility permissions.')
    print('Ctrl+C stops the server. No autostart is installed.\n')

    try:
        serve(args.bind, args.port, args.share, args.fps, args.max_width, args.bitrate, password)
    except KeyboardInterrupt:
        print('\nStopping.')


if __name__ == '__main__':
    main()
