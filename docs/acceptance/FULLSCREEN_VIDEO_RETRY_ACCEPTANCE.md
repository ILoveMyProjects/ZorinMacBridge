# 0.14.17 FullscreenVideoState acceptance

1. Connect Desktop and confirm keyboard/mouse control works.
2. Force or wait for a video-only failure/retry (for example `native streamer pipe stalled for 15s`) while the control session remains connected.
3. Confirm status shows `Connected — video retrying: ...` rather than turning the whole Desktop state into a disconnected session.
4. Confirm **Full Screen** remains enabled and can enter full-screen using the last decoded frame while video retries.
5. Confirm **Exit Full Screen**, `Esc`, and `Alt+Esc` still exit full-screen.
6. Confirm control/input continue working during the video retry and the H.264 channel reconnects independently when frames resume.
7. Confirm a real control-channel disconnect still disables **Full Screen**.
