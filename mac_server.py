#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import hmac
import io
import ipaddress
import os
import platform
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from PIL import Image

from protocol import (
    AUTH, AUTH_FAIL, AUTH_OK, CLIPBOARD_DATA, CLIPBOARD_GET, CLIPBOARD_SET,
    DOWNLOAD_BEGIN, DOWNLOAD_CHUNK, DOWNLOAD_END, DOWNLOAD_REQ, ERROR, FRAME,
    KEY, LIST_REQ, LIST_RESP, MKDIR_OK, MKDIR_REQ, MOUSE_BUTTON, MOUSE_MOVE,
    PacketReader, SCROLL, TEXT, UPLOAD_BEGIN, UPLOAD_CHUNK, UPLOAD_END,
    pack_json, pack_packet, recv_one_blocking, unpack_json,
)

APP_DIR = Path.home() / '.zorin-mac-bridge'
CERT_PATH = APP_DIR / 'server-cert.pem'
KEY_PATH = APP_DIR / 'server-key.pem'
DEFAULT_SHARE = Path.home() / 'ZorinMac-Share'
CHUNK = 256 * 1024
MAX_CLIPBOARD = 2 * 1024 * 1024
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
            raise SystemExit('The cryptography package is missing. Run install_macos.sh') from exc

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


class CGPoint(ctypes.Structure):
    _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [('width', ctypes.c_double), ('height', ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [('origin', CGPoint), ('size', CGSize)]


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
        display = self.cg.CGMainDisplayID()
        self.bounds = self.cg.CGDisplayBounds(display)

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
        x = self.bounds.origin.x + nx * self.bounds.size.width
        y = self.bounds.origin.y + ny * self.bounds.size.height
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
        x = self.bounds.origin.x + min(1.0, max(0.0, nx)) * self.bounds.size.width
        y = self.bounds.origin.y + min(1.0, max(0.0, ny)) * self.bounds.size.height
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
        if not text:
            return
        units = text.encode('utf-16-le')
        count = len(units) // 2
        arr = (ctypes.c_uint16 * count).from_buffer_copy(units)
        event = self.cg.CGEventCreateKeyboardEvent(None, 0, True)
        self.cg.CGEventKeyboardSetUnicodeString(event, count, arr)
        self._post(event)
        event = self.cg.CGEventCreateKeyboardEvent(None, 0, False)
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


class ScreenGrabber:
    def __init__(self, max_width: int, quality: int) -> None:
        self.max_width = max_width
        self.quality = quality
        exe = shutil.which('screencapture')
        if not exe:
            raise RuntimeError('macOS screencapture utility not found')
        self.exe = exe
        self.path = Path(tempfile.gettempdir()) / f'zorin-mac-bridge-{os.getpid()}-{uuid.uuid4().hex}.jpg'

    def close(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass

    def capture(self) -> tuple[int, int, bytes]:
        proc = subprocess.run(
            [self.exe, '-x', '-m', '-t', 'jpg', str(self.path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        if proc.returncode != 0:
            msg = proc.stderr.decode('utf-8', 'replace').strip()
            raise RuntimeError(f'screencapture failed: {msg or proc.returncode}')
        with Image.open(self.path) as im:
            im = im.convert('RGB')
            if self.max_width and im.width > self.max_width:
                h = max(1, round(im.height * self.max_width / im.width))
                im = im.resize((self.max_width, h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=self.quality, optimize=False)
            return im.width, im.height, buf.getvalue()


def send_frame(sock: ssl.SSLSocket, width: int, height: int, jpeg: bytes) -> None:
    sock.sendall(pack_packet(FRAME, struct.pack('!II', width, height) + jpeg))


def recv_available(sock: ssl.SSLSocket, reader: PacketReader) -> list[tuple[int, bytes]]:
    packets: list[tuple[int, bytes]] = []
    while True:
        try:
            data = sock.recv(65536)
            if not data:
                raise ConnectionError('client disconnected')
            packets.extend(reader.feed(data))
            if len(data) < 65536:
                break
        except (socket.timeout, ssl.SSLWantReadError, BlockingIOError):
            break
    return packets


def desktop_session(sock: ssl.SSLSocket, fps: float, max_width: int, quality: int) -> None:
    print('[desktop] connected')
    inp = MacInput()
    clipboard = MacClipboard()
    grabber = ScreenGrabber(max_width=max_width, quality=quality)
    reader = PacketReader()
    sock.settimeout(0.002)
    interval = 1.0 / max(1.0, min(float(fps), 20.0))
    next_frame = 0.0
    try:
        while True:
            for kind, payload in recv_available(sock, reader):
                if kind == MOUSE_MOVE and len(payload) == 8:
                    nx, ny = struct.unpack('!ff', payload)
                    inp.move(nx, ny)
                elif kind == MOUSE_BUTTON and len(payload) == 10:
                    button, down, nx, ny = struct.unpack('!BBff', payload)
                    inp.button(button, bool(down), nx, ny)
                elif kind == SCROLL and len(payload) == 8:
                    dx, dy = struct.unpack('!ii', payload)
                    inp.scroll(dx, dy)
                elif kind == KEY:
                    msg = unpack_json(payload)
                    inp.key(str(msg.get('keysym', '')), bool(msg.get('down')))
                elif kind == TEXT:
                    inp.text(payload.decode('utf-8', 'replace'))
                elif kind == CLIPBOARD_SET:
                    clipboard.set_text(payload.decode('utf-8', 'replace'))
                elif kind == CLIPBOARD_GET:
                    text = clipboard.get_text()
                    sock.sendall(pack_packet(CLIPBOARD_DATA, text.encode('utf-8')))

            now = time.monotonic()
            if now >= next_frame:
                width, height, jpeg = grabber.capture()
                send_frame(sock, width, height, jpeg)
                next_frame = now + interval
            else:
                time.sleep(min(0.003, next_frame - now))
    finally:
        inp.release_all()
        grabber.close()
        print('[desktop] disconnected')


def send_error(sock: ssl.SSLSocket, message: str) -> None:
    try:
        sock.sendall(pack_packet(ERROR, message.encode('utf-8', 'replace')))
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


def handle_client(raw: socket.socket, addr, context: ssl.SSLContext, password: str, share: Path, args) -> None:
    peer_ip = addr[0]
    if not is_lan_ip(peer_ip):
        print(f'[reject] non-LAN peer {peer_ip}')
        raw.close()
        return
    sock = None
    try:
        raw.settimeout(10)
        sock = context.wrap_socket(raw, server_side=True)
        kind, payload = recv_one_blocking(sock)
        if kind != AUTH:
            sock.sendall(pack_packet(AUTH_FAIL, b'auth required'))
            return
        auth = unpack_json(payload)
        supplied = str(auth.get('password', ''))
        role = str(auth.get('role', ''))
        if not hmac.compare_digest(supplied, password):
            time.sleep(1.0)
            sock.sendall(pack_packet(AUTH_FAIL, b'bad password'))
            print(f'[auth] failed from {peer_ip}')
            return
        if role not in {'desktop', 'file'}:
            sock.sendall(pack_packet(AUTH_FAIL, b'bad role'))
            return
        sock.sendall(pack_json(AUTH_OK, {'role': role, 'server': 'ZorinMac-Bridge'}))
        if role == 'desktop':
            desktop_session(sock, args.fps, args.max_width, args.quality)
        else:
            file_session(sock, share)
    except (ssl.SSLError, ConnectionError, OSError) as exc:
        print(f'[client {peer_ip}] {exc}')
    except Exception as exc:
        print(f'[client {peer_ip}] ERROR: {exc}')
    finally:
        try:
            if sock is not None:
                sock.close()
            else:
                raw.close()
        except Exception:
            pass


def serve(bind: str, port: int, share: Path, fps: float, max_width: int, quality: int, password: str, *, stop_event=None, log=print, ready_callback=None) -> None:
    if platform.system() != 'Darwin':
        raise RuntimeError('This server is intended for macOS only.')
    if not is_lan_ip(bind):
        raise ValueError('Bind address must be a literal private/loopback/link-local IP address.')
    if not (1 <= int(port) <= 65535):
        raise ValueError('Invalid port.')
    quality = min(95, max(25, int(quality)))
    share = Path(share).expanduser().resolve()
    share.mkdir(mode=0o700, parents=True, exist_ok=True)

    fingerprint = ensure_certificate()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(CERT_PATH), str(KEY_PATH))

    from types import SimpleNamespace
    args = SimpleNamespace(fps=float(fps), max_width=int(max_width), quality=quality)

    listener = socket.socket(socket.AF_INET6 if ':' in bind else socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind, int(port)))
    listener.listen(8)
    listener.settimeout(0.5)

    log(f'Listening on {bind}:{port}')
    log(f'Share directory: {share}')
    log(f'TLS SHA-256: {fingerprint}')
    log('Runtime: no DNS, cloud, relay, telemetry, or outbound connections')
    log('Clients: RFC1918/ULA/link-local/loopback only')
    if ready_callback is not None:
        ready_callback(fingerprint)

    try:
        while stop_event is None or not stop_event.is_set():
            try:
                raw, addr = listener.accept()
            except socket.timeout:
                continue
            threading.Thread(
                target=handle_client,
                args=(raw, addr, context, password, share, args),
                daemon=True,
            ).start()
    finally:
        listener.close()
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
    ap.add_argument('--quality', type=int, default=72)
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
        serve(args.bind, args.port, args.share, args.fps, args.max_width, args.quality, password)
    except KeyboardInterrupt:
        print('\nStopping.')


if __name__ == '__main__':
    main()
