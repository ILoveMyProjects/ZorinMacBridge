# Stage D — Pre-login core lifecycle

## Target

Make the LoginWindow runtime deterministic before further capture/input experimentation:

1. exactly one control/input owner;
2. video is cryptographically bound to that control owner;
3. capture/encode/decode progress is observable as explicit states;
4. LoginWindow -> Aqua is a protocol handoff, not an accidental connection loss.

## Ownership model

The pre-login Desktop role acquires a `PreloginControlLease` and receives a random `session_token`. A replacement Desktop connection shuts down the old control socket and waits for its `LoginWindowInput.release_all()`/lease release before acquiring a new generation. Video authentication must present the current token from the same peer. The existing global native-video lock still guarantees one native capture pipeline at a time.

The C IOHID bridge remains process-global, but open/close/post operations are mutex protected and open/close are reference counted. This is a defensive backstop; Python control ownership is the primary exclusivity mechanism.

## Pipeline state

The native streamer records first successful progress through:

```text
capture-frame
  -> pixel-buffer
  -> encode-submit
  -> h264-written
  -> client: First H.264 frame decoded
```

`CGDisplayStream` no longer counts a raw surface callback as proof that the pre-login video path works. Its 2-second proof window is satisfied only after a frame is accepted by VideoToolbox. Pipeline state packets are serialized by the Python video thread, so native logger and TLS writes do not write concurrently to the same SSL socket.

## Aqua handoff

The LoginWindow helper watches `/dev/console`. When the console owner becomes a real user it:

1. marks the active lease for handoff;
2. interrupts the pre-login video socket;
3. lets the control thread send `SESSION_STATE {state: handoff, target: aqua}`;
4. stops the pre-login listener.

The Aqua GUI retries `EADDRINUSE` for a bounded 8-second window while the LoginWindow process releases the shared port. The Linux GTK client uses a 250 ms first reconnect delay for an explicit handoff; normal failures keep the existing exponential reconnect policy.

## Non-goals

Stage D does not replace the Stage B capability proof. As of 0.15.19, Developer ID/provisioning is optional rather than a required acceptance condition. The physical LoginWindow probe remains authoritative for whether capture/input actually work on the free or optional-signing path.

## Authentication framing

Desktop and Video keep the same `PacketReader` used for `AUTH_OK` after authentication completes. This is required because TLS/TCP may coalesce `AUTH_OK` with the first lifecycle packet or first H.264 access unit in one read. Discarding the auth reader would discard those already-parsed packets. `PacketReader.drain_pending()` transfers the coalesced packets into the normal role loop before the next socket read.
