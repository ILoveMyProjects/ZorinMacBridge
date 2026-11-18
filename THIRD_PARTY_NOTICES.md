# Third-party notices

## Karabiner-DriverKit-VirtualHIDDevice

ZorinMacBridge macOS release bundles the signed installer package from
`pqrs-org/Karabiner-DriverKit-VirtualHIDDevice` so the LoginWindow helper can
create a driver-backed virtual keyboard and pointing device.

- Upstream: https://github.com/pqrs-org/Karabiner-DriverKit-VirtualHIDDevice
- Bundled version: 8.8.0
- Upstream repository license: see the upstream `LICENSE.md` and per-file notices.
- The client protocol definitions used by ZorinMacBridge are compatible with
  upstream client protocol version 7. Relevant upstream C++ headers carry the
  Boost Software License 1.0 notice.

The third-party package remains a separately signed component. ZorinMacBridge
does not modify its DriverKit extension binary.
