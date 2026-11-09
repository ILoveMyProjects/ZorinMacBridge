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

from zmb_session_lifecycle import (
    PRELOGIN_CONTROL_REPLACEMENT_GRACE_SECONDS, PreloginControlLease,
    PreloginSessionCoordinator, make_capture_backend_state, make_capture_failure_state, make_handoff_state, make_pipeline_state,
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
VIDEO_REPLACEMENT_GRACE_SECONDS = 4.0
VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0
_PRELOGIN_SESSION_COORDINATOR = PreloginSessionCoordinator()


def request_prelogin_handoff(user: str, *, log=print) -> PreloginControlLease | None:
    """Notify the active LoginWindow client that Aqua is taking over."""
    lease = _PRELOGIN_SESSION_COORDINATOR.request_handoff(str(user), log=log)
    # Video has no useful work left once loginwindow hands off.  Interrupt it now
    # so the Aqua server can acquire the native capture pipeline quickly.
    _interrupt_active_video_session(log=log)
    return lease


def _set_active_video_socket(sock: ssl.SSLSocket | None) -> None:
    global _ACTIVE_VIDEO_SOCKET
    with _VIDEO_SESSION_STATE_LOCK:
        _ACTIVE_VIDEO_SOCKET = sock


def _clear_active_video_socket(sock: ssl.SSLSocket) -> None:
    global _ACTIVE_VIDEO_SOCKET
    with _VIDEO_SESSION_STATE_LOCK:
        if _ACTIVE_VIDEO_SOCKET is sock:
            _ACTIVE_VIDEO_SOCKET = None


def _interrupt_active_video_session(*, log=print) -> bool:
    with _VIDEO_SESSION_STATE_LOCK:
        active = _ACTIVE_VIDEO_SOCKET
    if active is None:
        return False
    try:
        # TCP shutdown is intentional here.  It wakes the old video thread out of
        # select()/recv immediately; that thread still owns close() and native cleanup.
        active.shutdown(socket.SHUT_RDWR)
        log('[video] forced stale video socket shutdown before replacement')
    except OSError as exc:
        log(f'[video] stale video socket shutdown warning: {type(exc).__name__}: {exc}')
    return True


def _acquire_video_session(sock: ssl.SSLSocket, *, log=print) -> None:
    if not _VIDEO_SESSION_LOCK.acquire(blocking=False):
        log('[video] previous video session still active; interrupting stale owner before replacement')
        _interrupt_active_video_session(log=log)
        if not _VIDEO_SESSION_LOCK.acquire(timeout=VIDEO_REPLACEMENT_GRACE_SECONDS):
            raise TimeoutError(
                f'previous video session did not stop within {VIDEO_REPLACEMENT_GRACE_SECONDS:g}s '
                'after forced socket shutdown'
            )
    _set_active_video_socket(sock)


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
        self.cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        self.cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self.cg.CGEventKeyboardSetUnicodeString.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint16)]
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cg.CGEventCreateScrollWheelEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        self.cg.CGEventCreateScrollWheelEvent.restype = ctypes.c_void_p
        self.held_buttons: set[int] = set()
        self.held_modifiers: set[str] = set()

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
    """Inject input into the macOS LoginWindow session through IOHIDSystem.

    CGEvent mouse movement can appear to work at loginwindow while clicks and
    keyboard events are silently discarded.  Keep a cached display rectangle
    from session startup and avoid further WindowServer queries during input.
    """

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

    def __init__(self) -> None:
        self.native = NativeStreamerLibrary()
        rc = int(self.native.lib.zmb_login_input_open())
        if rc != 0:
            raise RuntimeError(f'IOHIDSystem login-window input unavailable (IOReturn=0x{rc & 0xffffffff:08x})')
        try:
            # Cache geometry before ScreenCaptureKit starts.  Some macOS releases are
            # hostile to extra WindowServer calls from a loginwindow helper once the
            # capture connection exists.
            cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
            cg.CGDisplayBounds.restype = CGRect
            main = int(cg.CGMainDisplayID())
            bounds = cg.CGDisplayBounds(ctypes.c_uint32(main))
            if bounds.size.width <= 0 or bounds.size.height <= 0:
                raise RuntimeError('LoginWindow display bounds are unavailable')
            self.origin_x = float(bounds.origin.x)
            self.origin_y = float(bounds.origin.y)
            self.width = float(bounds.size.width)
            self.height = float(bounds.size.height)
        except Exception:
            self.native.lib.zmb_login_input_close()
            raise
        self.held_buttons: set[int] = set()
        self.held_modifiers: set[str] = set()
        self.last_nx = 0.5
        self.last_ny = 0.5

    def _flags(self) -> int:
        flags = 0
        for key in self.held_modifiers:
            flags |= self.MOD_FLAGS.get(key, 0)
        return flags

    @staticmethod
    def _check(rc: int, operation: str) -> None:
        if int(rc) != 0:
            raise RuntimeError(f'{operation} failed through IOHIDSystem (IOReturn=0x{int(rc) & 0xffffffff:08x})')

    def _point(self, nx: float, ny: float) -> tuple[int, int]:
        self.last_nx = min(1.0, max(0.0, float(nx)))
        self.last_ny = min(1.0, max(0.0, float(ny)))
        x = int(round(self.origin_x + self.last_nx * max(0.0, self.width - 1.0)))
        y = int(round(self.origin_y + self.last_ny * max(0.0, self.height - 1.0)))
        return x, y

    def move(self, nx: float, ny: float) -> None:
        x, y = self._point(nx, ny)
        if 1 in self.held_buttons:
            etype, button = self.LEFT_DRAGGED, 0
        elif 3 in self.held_buttons:
            etype, button = self.RIGHT_DRAGGED, 1
        elif 2 in self.held_buttons:
            etype, button = self.OTHER_DRAGGED, 2
        else:
            etype, button = self.MOUSE_MOVED, 0
        rc = self.native.lib.zmb_login_input_pointer(etype, x, y, button, self._flags())
        self._check(rc, 'login-window pointer move')

    def button(self, button_num: int, down: bool, nx: float, ny: float) -> None:
        x, y = self._point(nx, ny)
        cg_button = self.BUTTONS.get(int(button_num))
        if cg_button is None:
            return
        if button_num == 1:
            etype = self.LEFT_DOWN if down else self.LEFT_UP
        elif button_num == 3:
            etype = self.RIGHT_DOWN if down else self.RIGHT_UP
        else:
            etype = self.OTHER_DOWN if down else self.OTHER_UP
        rc = self.native.lib.zmb_login_input_pointer(etype, x, y, cg_button, self._flags())
        self._check(rc, 'login-window mouse button')
        if down:
            self.held_buttons.add(int(button_num))
        else:
            self.held_buttons.discard(int(button_num))

    def scroll(self, dx: int, dy: int) -> None:
        rc = self.native.lib.zmb_login_input_scroll(int(dx), int(dy), self._flags())
        self._check(rc, 'login-window scroll')

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
        rc = self.native.lib.zmb_login_input_key(int(code), 1 if down else 0, self._flags(), 1 if is_mod else 0)
        self._check(rc, 'login-window keyboard event')

    def text(self, text: str) -> None:
        # Normal printable keys arrive through KEY. TEXT is the Unicode fallback.
        # Support the complete printable ASCII password set here without using
        # clipboard/CGEvent at loginwindow; reject non-ASCII rather than silently
        # typing the wrong password.
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
        for key in list(self.held_modifiers):
            try:
                self.key(key, False)
            except Exception:
                pass
        for button in list(self.held_buttons):
            try:
                self.button(button, False, self.last_nx, self.last_ny)
            except Exception:
                pass
        self.held_modifiers.clear()
        self.held_buttons.clear()
        try:
            self.native.lib.zmb_login_input_close()
        except Exception:
            pass


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

    Keeping CoreHID in a separate dylib prevents the diagnostic-only macOS 26+
    dependency from changing the normal Desktop/LoginWindow streamer path.  The
    dylib is loaded into the existing app process so TCC and code-signing checks
    observe the exact ZorinMacBridge Server identity, not a helper executable.
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
        # LoginWindow input uses IOHIDSystem rather than CGEvent.  These C
        # shims are compiled into the same dylib as the streamer so the frozen
        # app has one native payload to sign and validate.
        self.lib.zmb_login_input_open.argtypes = []
        self.lib.zmb_login_input_open.restype = ctypes.c_int32
        self.lib.zmb_login_input_close.argtypes = []
        self.lib.zmb_login_input_close.restype = None
        self.lib.zmb_login_input_key.argtypes = [ctypes.c_uint16, ctypes.c_int32, ctypes.c_uint32, ctypes.c_int32]
        self.lib.zmb_login_input_key.restype = ctypes.c_int32
        self.lib.zmb_login_input_pointer.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_pointer.restype = ctypes.c_int32
        self.lib.zmb_login_input_scroll.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
        self.lib.zmb_login_input_scroll.restype = ctypes.c_int32

    def _owned_string(self, fn) -> str:
        pointer = fn()
        if not pointer:
            return ''
        try:
            return ctypes.string_at(pointer).decode('utf-8', 'replace')
        finally:
            self.lib.zmb_string_free(pointer)

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


def _read_exact_file(stream, size: int, *, timeout: float = 15.0, disconnect_sock: ssl.SSLSocket | None = None, idle_callback=None) -> bytes:
    """Read native pipe bytes while also noticing a closed video client.

    Reconfiguration closes the old TLS video socket before opening the next one.
    The native encoder can be quiet for many seconds on an unchanged screen, so
    waiting only on its pipe used to keep the old streamer alive until the 15 s
    deadline. Monitor the TLS socket in the same select loop so cleanup starts as
    soon as the old client disconnects.
    """
    out = bytearray()
    deadline = time.monotonic() + max(0.5, float(timeout))
    fd = stream.fileno()
    while len(out) < size:
        if idle_callback is not None:
            idle_callback()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'native streamer pipe stalled for {timeout:g}s')
        watchers = [fd]
        if disconnect_sock is not None:
            watchers.append(disconnect_sock)
        readable, _, _ = select.select(watchers, [], [], min(0.1 if idle_callback is not None else 0.5, remaining))
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
        log('[control] initializing LoginWindow IOHID input')
        inp = LoginWindowInput()
        log('[control] LoginWindow IOHID input initialized')
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


def video_session(sock: ssl.SSLSocket, fps: float, max_width: int, bitrate: int, display_id: int = 0, target_width: int = 0, target_height: int = 0, *, prelogin: bool = False, log=print, release_ownership=None) -> None:
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
    if prelogin:
        # LoginWindow always exposes the physical macOS login screen.  A user's
        # saved 2K/4K preference is intentionally deferred until Aqua takes over.
        display_id = 0
        target_width = 0
        target_height = 0
        max_width = min(max(640, int(max_width)), 1920)
        fps = min(max(1.0, float(fps)), 30.0)
        bitrate = min(max(1_000_000, int(bitrate)), 8_000_000)
    capture_display_id = max(0, int(display_id))
    virtual_mode = int(target_width) > 0 and int(target_height) > 0
    if virtual_mode:
        capture_display_id = native.ensure_virtual_display(int(target_width), int(target_height))
        log(f'[video] virtual remote desktop ready: {target_width}x{target_height} display_id={capture_display_id}')
    else:
        # Auto/native mode returns to the physical Mac display arrangement.
        native.release_virtual_display()
        log(f'[video] physical display mode selected: display_id={capture_display_id or "auto"}')
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
                            session_events.put(make_pipeline_state(line.split('=', 1)[1]))
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
    runner.start()
    logger.start()

    sock.settimeout(10.0)
    frames = 0
    stat_started = time.monotonic()
    stat_frames = 0
    stat_bytes = 0
    try:
        with os.fdopen(data_r, 'rb', buffering=0) as stream:
            while True:
                startup_timeout = VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS if frames == 0 else 15.0
                try:
                    header = _read_exact_file(stream, 4, timeout=startup_timeout, disconnect_sock=sock, idle_callback=flush_pipeline_events)
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
                    payload = _read_exact_file(stream, size, timeout=startup_timeout, disconnect_sock=sock, idle_callback=flush_pipeline_events)
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
        try:
            native.lib.zmb_streamer_stop(handle)
        except Exception:
            pass
        # The native streamer is now logically stopped.  Do not keep the global
        # video-session ownership while Swift drains/cancels its background work:
        # a slow FileHandle/VideoToolbox cleanup must not block the next client.
        if release_ownership is not None:
            try:
                release_ownership()
            except Exception as exc:
                log(f'[video] ownership release warning: {type(exc).__name__}: {exc}')
        runner.join(timeout=3.0)
        try:
            native.lib.zmb_streamer_destroy(handle)
        except Exception:
            pass
        # These descriptors are owned by Swift FileHandle and normally close
        # when runBlocking returns. Close defensively if they are still open.
        for fd in (native_data_w, native_log_w):
            try:
                os.close(fd)
            except OSError:
                pass
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
        auth_ok = {'role': role, 'server': 'ZorinMac-Bridge', 'prelogin': bool(prelogin)}
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
            _acquire_video_session(sock, log=log)
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
                    prelogin=prelogin, log=log, release_ownership=release_video_ownership,
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
