# ZorinMacBridge 0.12.0 — P5 Remote Desktop Performance acceptance checklist

Run these checks on the real Zorin + Apple Silicon Mac pair after CI builds 0.12.0. Static/container tests cannot validate ScreenCaptureKit, VideoToolbox, GTK presentation timing, or real LAN congestion.

## 1. Baseline connection

1. Start ZorinMacBridge Server on the Mac and connect from the Zorin client.
2. Confirm Desktop control, Files, System, Xcode, Terminal, and Projects still operate.
3. Confirm **Video quality** defaults to **Adaptive** and the stream telemetry label begins updating.
4. Confirm the label shows an effective tier (`Adaptive/Eco`, `Adaptive/Efficient`, `Adaptive/Balanced`, or `Adaptive/Sharp`), FPS, Mbit/s, drop %, and queue ms.

## 2. Video-only reconfiguration

1. While connected, switch Adaptive → Low → High → Adaptive.
2. Confirm the remote image briefly renegotiates but the main control session does not disconnect.
3. Hold a modifier/mouse button only for a normal click/key sequence and verify no stuck input state appears after a video-only reconnect.
4. Confirm Files/Terminal remain usable while the video channel is being replaced.

## 3. Adaptive behavior

1. Resize the Zorin window substantially smaller and leave it for at least 10 seconds.
2. Confirm Adaptive eventually chooses a tier no larger than the visible viewport needs.
3. Create temporary local load (for example compile/run another workload on Zorin) and watch drop %. If sustained drops exceed the policy threshold, confirm Adaptive steps down without disconnecting control.
4. Remove the load and leave the stream stable for at least 20–30 seconds. Confirm it may step back up but does not oscillate rapidly.

## 4. Latency/backpressure

1. Move windows continuously on the Mac for 30–60 seconds.
2. Confirm motion remains current rather than playing a long backlog after activity stops.
3. Minimize/hide the ZorinMacBridge window for at least 30 seconds, restore it, and confirm the displayed frame catches up immediately instead of replaying old frames.
4. Watch Mac memory/CPU while streaming; confirm memory does not grow continuously during a throttled/slow client scenario.

## 5. Xcode + Simulator load

1. Start an iOS Simulator and an Xcode build/test from the Zorin Projects/Xcode pages.
2. Keep Adaptive desktop video connected simultaneously.
3. Confirm the GUI remains responsive and the stream can reduce quality instead of freezing the whole client.
4. Confirm project build/test output and P0 status operations remain responsive.

## 6. Fixed profiles

Repeat a short desktop session with Low, Balanced, High, and Ultra. Verify each reports a matching effective profile and that switching profiles does not require a full desktop reconnect. Ultra is intentionally demanding and is not expected to be the best choice while Xcode + Simulator are busy.

## 7. Disconnect/reconnect

1. Disconnect manually and reconnect.
2. Confirm no stale video frame, old adaptive decision, or queued input leaks into the new session.
3. Stop the Mac server unexpectedly and confirm existing bounded control-session auto-reconnect behavior still works.

Do not tag v0.12.0 until the real-Mac checks above pass.
