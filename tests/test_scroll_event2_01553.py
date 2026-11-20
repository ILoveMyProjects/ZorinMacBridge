"""0.15.53 regression: Aqua scroll uses the fixed-signature CoreGraphics Event2 API."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mac_server


class FakeQuartz:
    def __init__(self):
        self.created = []

    def CGEventCreateScrollWheelEvent2(self, *args):
        self.created.append(args)
        return 321


cg = FakeQuartz()
inp = mac_server.MacInput.__new__(mac_server.MacInput)
inp.cg = cg
posted = []
inp._post = lambda event, **kwargs: posted.append((event, dict(kwargs)))
inp.scroll(4, -17)
assert len(cg.created) == 1
args = cg.created[0]
assert len(args) == 6
assert args[1].value == mac_server.MacInput.PIXEL_SCROLL_UNIT == 0
assert args[2].value == 2
assert args[3].value == -17
assert args[4].value == 4
assert args[5].value == 0
assert posted == [(321, {'tap': mac_server.MacInput.SESSION_TAP})]

server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
scroll_block = server.split('    def scroll(self, dx: int, dy: int) -> None:', 1)[1].split('    def key(', 1)[0]
assert 'CGEventCreateScrollWheelEvent2' in scroll_block
assert 'CGEventCreateScrollWheelEvent(' not in scroll_block
assert 'SESSION_PIXEL_EVENT2_SUBMITTED_UNVERIFIED' in server
assert 'CGEventCreateScrollWheelEvent2.argtypes' in server
print('0.15.53 fixed-signature Aqua scroll regression: OK')
