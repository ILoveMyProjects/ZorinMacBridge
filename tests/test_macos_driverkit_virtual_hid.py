#!/usr/bin/env python3
from pathlib import Path
import socket, struct, sys, tempfile, threading, time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from zmb_macos_virtual_hid import *

def recv_exact(sock,n):
    out=bytearray()
    while len(out)<n:
        p=sock.recv(n-len(out))
        if not p: raise EOFError
        out.extend(p)
    return bytes(out)

def recv_frame(sock):
    n=struct.unpack('>I',recv_exact(sock,4))[0]
    b=recv_exact(sock,n)
    return b[0],b[1:]

def parse_request(rest):
    rid=struct.unpack('>Q',rest[:8])[0]; payload=rest[8:]
    return rid,struct.unpack('<H',payload[:2])[0],payload[2],payload[3:]

def send_status_request(sock,rid=77,payload=bytes((1,1,2,1,4,1,5,1))):
    body=bytes((4,))+struct.pack('>Q',rid)+payload
    sock.sendall(struct.pack('>I',len(body))+body)

def run():
    with tempfile.TemporaryDirectory() as td:
        path=str(Path(td)/'vhid.sock')
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.bind(path); server.listen(1)
        seen=[]
        def serve():
            conn,_=server.accept()
            try:
                t,r=recv_frame(conn); assert t==4
                _,proto,cmd,data=parse_request(r); assert proto==7 and cmd==0
                assert len(data)==24 and struct.unpack('<QQQ',data)==(0x16C0,0x27DB,0)
                seen.append(('init-kbd',))
                t,r=recv_frame(conn); assert t==4
                _,proto,cmd,data=parse_request(r); assert proto==7 and cmd==3 and data==b''
                seen.append(('init-mouse',))
                send_status_request(conn)
                t,r=recv_frame(conn); assert t==5 and struct.unpack('>Q',r[:8])[0]==77 and r[8:]==b''
                while len(seen)<4:
                    t,r=recv_frame(conn)
                    if t==0: continue
                    assert t==4
                    _,proto,cmd,data=parse_request(r); assert proto==7
                    if cmd==6: seen.append(('kbd',data))
                    elif cmd==11: seen.append(('mouse',data))
            finally: conn.close()
        th=threading.Thread(target=serve,daemon=True); th.start()
        c=DriverKitVirtualHIDClient(socket_path=path,heartbeat_seconds=30,require_root=False)
        assert c.open(wait_seconds=1)==0 and c.ready
        status=c.status(); assert status['driver_activated'] is True and status['driver_connected'] is True
        assert c.post_keyboard(0x02,[0x04,0x05])==0
        assert c.post_pointing(1,12,-7,1,-1)==0
        deadline=time.monotonic()+1
        while len(seen)<4 and time.monotonic()<deadline: time.sleep(.01)
        c.close(); server.close(); th.join(timeout=1)
        k=next(x[1] for x in seen if x[0]=='kbd')
        assert len(k)==67 and k[:3]==bytes((1,2,0))
        keys=[struct.unpack('<H',k[i:i+2])[0] for i in range(3,67,2)]
        assert keys[:2]==[4,5] and all(v==0 for v in keys[2:])
        m=next(x[1] for x in seen if x[0]=='mouse')
        assert len(m)==8 and struct.unpack('<Ibbbb',m)==(1,12,-7,1,-1)

    # Directly after boot the daemon socket can be available before DriverKit
    # finishes publishing keyboard/pointing. A bounded open timeout must NOT
    # tear down that connection; later status delivery promotes it to ready.
    with tempfile.TemporaryDirectory() as td:
        path=str(Path(td)/'vhid-delayed.sock')
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.bind(path); server.listen(1)
        ready_to_publish=threading.Event()
        def serve_delayed():
            conn,_=server.accept()
            try:
                for expected in (0,3):
                    t,r=recv_frame(conn); assert t==4
                    _,proto,cmd,_data=parse_request(r); assert proto==7 and cmd==expected
                send_status_request(conn,88,bytes((1,1,2,1,4,0,5,0)))
                t,r=recv_frame(conn); assert t==5 and struct.unpack('>Q',r[:8])[0]==88
                ready_to_publish.wait(1)
                send_status_request(conn,89,bytes((4,1,5,1)))
                t,r=recv_frame(conn); assert t==5 and struct.unpack('>Q',r[:8])[0]==89
                time.sleep(.05)
            finally:
                conn.close()
        th=threading.Thread(target=serve_delayed,daemon=True); th.start()
        c=DriverKitVirtualHIDClient(socket_path=path,heartbeat_seconds=30,require_root=False)
        assert c.open(wait_seconds=.05)==RC_NOT_READY
        assert c.connected and not c.ready
        status=c.status(); assert status['driver_activated'] is True and status['driver_connected'] is True
        ready_to_publish.set()
        deadline=time.monotonic()+1
        while not c.ready and time.monotonic()<deadline: time.sleep(.01)
        assert c.ready, c.status()
        c.close(); server.close(); th.join(timeout=1)

    assert list(chunk_relative(300,-260))==[(127,-127),(127,-127),(46,-6)]
    assert list(chunk_relative(0,0))==[(0,0)]
if __name__=='__main__': run(); print('macOS DriverKit virtual HID protocol tests: OK')
