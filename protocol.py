#!/usr/bin/env python3
from __future__ import annotations

import json
import struct
from collections import deque
from dataclasses import dataclass

HEADER = struct.Struct('!BI')  # type:u8, payload_len:u32
MAX_PACKET = 64 * 1024 * 1024

AUTH = 1
AUTH_OK = 2
AUTH_FAIL = 3
FRAME = 10
MOUSE_MOVE = 20
MOUSE_BUTTON = 21
SCROLL = 22
KEY = 23
TEXT = 24
CLIPBOARD_SET = 25
CLIPBOARD_GET = 26
CLIPBOARD_DATA = 27
LIST_REQ = 30
LIST_RESP = 31
UPLOAD_BEGIN = 32
UPLOAD_CHUNK = 33
UPLOAD_END = 34
DOWNLOAD_REQ = 35
DOWNLOAD_BEGIN = 36
DOWNLOAD_CHUNK = 37
DOWNLOAD_END = 38
MKDIR_REQ = 39
MKDIR_OK = 40
ERROR = 255


def pack_packet(kind: int, payload: bytes = b'') -> bytes:
    if not (0 <= kind <= 255):
        raise ValueError('invalid packet type')
    if len(payload) > MAX_PACKET:
        raise ValueError('packet too large')
    return HEADER.pack(kind, len(payload)) + payload


def pack_json(kind: int, obj) -> bytes:
    return pack_packet(kind, json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def unpack_json(payload: bytes):
    return json.loads(payload.decode('utf-8'))


@dataclass
class PacketReader:
    buffer: bytearray

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.pending = deque()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        if data:
            self.buffer.extend(data)
        out: list[tuple[int, bytes]] = []
        while True:
            if len(self.buffer) < HEADER.size:
                break
            kind, size = HEADER.unpack(self.buffer[:HEADER.size])
            if size > MAX_PACKET:
                raise ValueError(f'packet too large: {size}')
            total = HEADER.size + size
            if len(self.buffer) < total:
                break
            payload = bytes(self.buffer[HEADER.size:total])
            del self.buffer[:total]
            out.append((kind, payload))
        return out


def recv_one_blocking(sock, reader: PacketReader | None = None) -> tuple[int, bytes]:
    reader = reader or PacketReader()
    if reader.pending:
        return reader.pending.popleft()
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError('connection closed')
        packets = reader.feed(chunk)
        if packets:
            reader.pending.extend(packets[1:])
            return packets[0]
