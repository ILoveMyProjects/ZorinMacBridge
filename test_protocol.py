import socket
import tempfile
import threading
from pathlib import Path

from mac_server import file_session, is_lan_ip, safe_share_path
from protocol import (
    DOWNLOAD_BEGIN, DOWNLOAD_CHUNK, DOWNLOAD_END, DOWNLOAD_REQ,
    LIST_REQ, LIST_RESP, MKDIR_OK, MKDIR_REQ, PacketReader, VIDEO_H264,
    UPLOAD_BEGIN, UPLOAD_CHUNK, UPLOAD_END,
    pack_json, pack_packet, recv_one_blocking, unpack_json,
)


def test_reader_and_file_tree():
    with tempfile.TemporaryDirectory() as td:
        share = Path(td)
        server, client = socket.socketpair()
        t = threading.Thread(target=file_session, args=(server, share), daemon=True)
        t.start()
        reader = PacketReader()

        client.sendall(pack_json(MKDIR_REQ, {'path': 'project/src'}))
        kind, _ = recv_one_blocking(client, reader)
        assert kind == MKDIR_OK

        data = (b'hello-zorin-mac-' * 20000) + b'end'
        client.sendall(pack_json(UPLOAD_BEGIN, {'path': 'project/src/test.bin', 'size': len(data)}))
        # Intentionally split into uneven chunks to exercise framing/buffering.
        for off in range(0, len(data), 7777):
            client.sendall(pack_packet(UPLOAD_CHUNK, data[off:off + 7777]))
        client.sendall(pack_packet(UPLOAD_END))
        kind, _ = recv_one_blocking(client, reader)
        assert kind == UPLOAD_END

        client.sendall(pack_json(LIST_REQ, {'path': 'project/src'}))
        kind, payload = recv_one_blocking(client, reader)
        assert kind == LIST_RESP
        listing = unpack_json(payload)
        assert listing['path'] == 'project/src'
        assert listing['items'][0]['name'] == 'test.bin'
        assert listing['items'][0]['kind'] == 'file'
        assert listing['items'][0]['size'] == len(data)

        client.sendall(pack_json(DOWNLOAD_REQ, {'path': 'project/src/test.bin'}))
        kind, payload = recv_one_blocking(client, reader)
        assert kind == DOWNLOAD_BEGIN
        assert unpack_json(payload)['size'] == len(data)
        out = bytearray()
        while True:
            kind, payload = recv_one_blocking(client, reader)
            if kind == DOWNLOAD_CHUNK:
                out.extend(payload)
            elif kind == DOWNLOAD_END:
                break
            else:
                raise AssertionError(kind)
        assert bytes(out) == data

        client.close()
        t.join(timeout=2)
        server.close()



def test_video_packet_framing():
    payload = b"\x00\x00\x00\x01\x67" + b"h264" * 1000
    reader = PacketReader()
    wire = pack_packet(VIDEO_H264, payload)
    out = []
    for off in range(0, len(wire), 137):
        out.extend(reader.feed(wire[off:off + 137]))
    assert out == [(VIDEO_H264, payload)]

def test_path_security():
    with tempfile.TemporaryDirectory() as td:
        share = Path(td)
        assert safe_share_path(share, 'a/b.txt') == share / 'a' / 'b.txt'
        for bad in ('../secret', '/etc/passwd', 'a/../../secret'):
            try:
                safe_share_path(share, bad, allow_root=False)
            except ValueError:
                pass
            else:
                raise AssertionError(f'path traversal accepted: {bad}')


def test_lan_filter():
    for good in ('127.0.0.1', '10.2.3.4', '172.16.1.2', '192.168.50.5', '169.254.1.1', '::1', 'fd00::1', 'fe80::1'):
        assert is_lan_ip(good), good
    for bad in ('8.8.8.8', '1.1.1.1', '2001:4860:4860::8888'):
        assert not is_lan_ip(bad), bad


if __name__ == '__main__':
    test_reader_and_file_tree()
    test_video_packet_framing()
    test_path_security()
    test_lan_filter()
    print('all tests passed')
