"""0.15.49: scroll and wake regression checks without macOS hardware."""
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zmb_scroll_input import ScrollAccumulator
import mac_server

# GTK smooth deltas must add up; each sub-step must not be rounded away.
s = ScrollAccumulator()
assert [s.feed(0, 0.1) for _ in range(3)] == [(0, 0), (0, 0), (0, 0)]
assert s.feed(0, 0.1) == (0, -1)
assert s.feed(-1, 1) == (3, -3)
assert s.feed(float('nan'), 0) == (0, 0)
s.reset()
assert s.feed(0, 0.1) == (0, 0)
s2 = ScrollAccumulator(scale=3.0)
assert sum(s2.feed(0, 0.2)[1] for _ in range(10)) == -6
assert s2.feed(-10000, 0)[0] == 127  # packet emission is clamped

# Aqua wheel events now match the session-level pixel injection path.
class FakeQuartz:
    def __init__(self):
        self.created = []
        self.locations = []
    def CGEventCreateScrollWheelEvent(self, *args):
        self.created.append(args)
        return 123
    def CGEventSetLocation(self, event, position):
        self.locations.append((event, position.x, position.y))

quartz = FakeQuartz()
mac_input = mac_server.MacInput.__new__(mac_server.MacInput)
mac_input.cg = quartz
posts = []
mac_input._cursor_location = lambda: mac_server.CGPoint(42, 77)
mac_input._post = lambda event, **kw: posts.append((event, kw))
mac_input.scroll(0, 0)
assert posts == []
mac_input.scroll(2, -3)
assert quartz.locations == []
assert posts == [(123, {'tap': mac_server.MacInput.SESSION_TAP})]
assert quartz.created[0][1].value == 0
assert quartz.created[0][3].value == -3
assert quartz.created[0][4].value == 2

# Only a genuinely sleeping physical display causes a Mac-side wake. No
# pixel-black detection and no video/TLS blocking wait.
class FakeFunction:
    def __init__(self, call):
        self.call = call
    def __call__(self, *args):
        return self.call(*args)

class FakeDisplay:
    def __init__(self, asleep):
        self.CGMainDisplayID = FakeFunction(lambda: 7)
        self.CGDisplayIsAsleep = FakeFunction(lambda display: asleep)

popen_calls = []
def fake_popen(*args, **kwargs):
    popen_calls.append((args, kwargs))
    return object()

with patch.object(mac_server.ctypes, 'CDLL', return_value=FakeDisplay(False)), patch.object(mac_server.subprocess, 'Popen', side_effect=fake_popen):
    assert mac_server.wake_sleeping_display_before_capture(log=lambda _: None) is False
assert popen_calls == []
mac_server._LAST_DISPLAY_WAKE_AT = 0.0
with patch.object(mac_server.ctypes, 'CDLL', return_value=FakeDisplay(True)), patch.object(mac_server.subprocess, 'Popen', side_effect=fake_popen):
    assert mac_server.wake_sleeping_display_before_capture(log=lambda _: None) is True
    assert mac_server.wake_sleeping_display_before_capture(log=lambda _: None) is False
assert len(popen_calls) == 1
assert popen_calls[0][0][0] == ['/usr/bin/caffeinate', '-u', '-t', '8']
assert popen_calls[0][1]['start_new_session'] is True

client = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
assert 'EventControllerScrollFlags.DISCRETE' not in client
assert "self._wake_display_for_video_async(reason='aqua-session-connected')" in client
assert 'now - self._last_display_wake_at) < 30.0' in client
assert 'wake_sleeping_display_before_capture(log=log)' in server
assert 'CGDisplayIsAsleep' in server
assert 'SESSION_PIXEL_SUBMITTED_UNVERIFIED' in server
assert 'PIXEL_SCROLL_UNIT = 0' in server
assert 'if not self.prelogin_session:' in client
print('scroll/wake 0.15.49 regression tests: OK')
