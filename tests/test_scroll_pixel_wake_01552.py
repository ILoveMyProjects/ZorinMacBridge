"""0.15.52 regression: exact Aqua pixel-wheel posting and bounded SCK wake retry."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mac_server


class FakeQuartz:
    def __init__(self):
        self.created = []
        self.locations = []
    def CGEventCreateScrollWheelEvent2(self, *args):
        self.created.append(args)
        return 123
    def CGEventSetLocation(self, *args):
        self.locations.append(args)


cg = FakeQuartz()
inp = mac_server.MacInput.__new__(mac_server.MacInput)
inp.cg = cg
posted = []
inp._post = lambda event, **kwargs: posted.append((event, dict(kwargs)))
inp.scroll(4, -17)
assert len(cg.created) == 1
args = cg.created[0]
assert args[1].value == mac_server.MacInput.PIXEL_SCROLL_UNIT == 0
assert args[2].value == 2
assert args[3].value == -17
assert args[4].value == 4
assert cg.locations == [], 'Aqua wheel event must not override CoreGraphics routing location'
assert posted == [(123, {'tap': mac_server.MacInput.SESSION_TAP})]
assert mac_server.MacInput.SESSION_TAP == 1

server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
assert 'PIXEL_SCROLL_UNIT = 0' in server
assert 'self._post(event, tap=self.SESSION_TAP)' in server
assert 'SESSION_PIXEL_EVENT2_SUBMITTED_UNVERIFIED' in server
scroll_block = server.split('    def scroll(self, dx: int, dy: int) -> None:', 1)[1].split('    def key(', 1)[0]
assert 'CGEventSetLocation' not in scroll_block
assert 'with_flags=True' not in scroll_block
assert 'for attempt in 1...50 where content.displays.isEmpty' in swift
assert 'Task.sleep(nanoseconds: 100_000_000)' in swift
assert 'No display available for ScreenCaptureKit after wake wait' in swift
print('0.15.52 pixel-scroll/wake-race regression: OK')
