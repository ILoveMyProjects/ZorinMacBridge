# macOS code signing

> **0.15.45 input update:** LoginWindow keyboard/mouse readiness is independent of the older in-app CoreHID capability gate. The release bundles a pinned, separately signed DriverKit virtual-HID package; ZMB talks to its root-only daemon using client protocol v7. The older managed-signing sections below remain as compatibility/history for release identity and capture diagnostics.

ZorinMacBridge has two macOS capability tiers. Starting with 0.15.26, official tagged releases always preserve a working Aqua artifact: the workflow upgrades it to managed Developer ID/provisioning when those credentials validate, and otherwise publishes the Aqua/ad-hoc fallback instead of failing the whole release.

## 1. Normal Aqua Desktop — local/manual free path remains supported

Normal logged-in Desktop/Files/System/Terminal operation does **not** technically require Developer ID. The stable per-Mac local ad-hoc identity in `mac_local_signing.py` remains available for Aqua-only installs. Starting with 0.15.26, tagged GitHub releases try managed Developer ID signing first and fall back to the working Aqua/ad-hoc artifact if managed signing is unavailable or invalid; that fallback does not claim reboot-to-LoginWindow capability.

## 2. Reboot-to-LoginWindow remote login — managed capability path

Starting with 0.15.24, pre-login READY requires the installed application to pass `mac_release_signing.verify_release_app()`. That verifier requires all of the following together:

- a non-ad-hoc **Developer ID Application** signature;
- an embedded `Contents/embedded.provisionprofile`;
- an all-Macs Developer ID distribution profile (`ProvisionsAllDevices=true`, no device list);
- matching TeamIdentifier/application identifier;
- a non-expired profile authorizing the selected signing certificate;
- `com.apple.developer.persistent-content-capture = true`;
- `com.apple.developer.hid.virtual.device = true`;
- the same entitlement values present in the final signed application.

The managed pre-login release tier uses all three maintainer secrets when they are available and valid:

- `MACOS_DEVELOPER_ID_P12_BASE64`
- `MACOS_DEVELOPER_ID_P12_PASSWORD`
- `MACOS_DEVELOPER_ID_PROFILE_BASE64`

A missing, partial, or invalid managed signing setup does not destroy the whole release: the workflow publishes the Aqua/ad-hoc tier instead. Only a successfully validated Developer ID + provisioning build can become pre-login VERIFIED.

## Installer and updater behavior

The macOS installer verifies the downloaded app before copying it into `/Applications`.

- Managed Developer ID build: preserve the publisher signature and embedded provisioning profile unchanged.
- Free build: apply the stable per-Mac local ad-hoc identity for Aqua operation.
- Existing managed pre-login installation + incoming free/ad-hoc build: **reject the downgrade**. Removing the managed entitlements silently would make reboot-login fail after an otherwise successful update.

The automatic updater follows the same rule. Migration from Aqua-only ad-hoc to the managed Developer ID build is allowed; the reverse transition is refused.

The root unattended installer configures everything root can configure locally: the P0 helpers, LoginWindow LaunchAgent, ownership/permissions, SSH prerequisites, update service and persistent state. It also reports `PRELOGIN_MANAGED_CAPABILITIES=ready|blocked`. Root access does not synthesize Apple-managed entitlements; those must already be present in the signed/provisioned application.

## Production LoginWindow runtime (0.15.27+)

The managed capability tier is now the actual production backend, not just a readiness gate:

- video: ScreenCaptureKit `SCStream` with bounded content discovery, bounded stream startup and a first-frame watchdog;
- input: CoreHID `HIDVirtualDevice` virtual keyboard + absolute pointer;
- legacy IOHIDSystem/Quartz: bundled production fallback starting with 0.15.30 when CoreHID cannot be created; the capability probe still records it separately from managed CoreHID proof.

The physical probe exercises an actual SCStream first frame and the same CoreHID input source before the build can be marked VERIFIED.

## Physical capability proof is still mandatory

Managed signing is a prerequisite, not proof that the target Mac accepts the feature at LoginWindow. The Stage-B probe still runs in the real LoginWindow session under the exact installed app identity. Pre-login becomes VERIFIED only when the proof is current for the installed version + CDHash and reports:

- `capture_result=PASS`;
- `input_corehid_result=PASS`;
- `overall=PASS`.

Legacy IOHID/Quartz remains diagnostic comparison data only. Starting with 0.15.24, a legacy-input PASS cannot satisfy the production pre-login readiness contract.

## Security / integrity invariants

Both release tiers retain checksum, bundle-id, version and code-signature verification. The managed tier additionally validates the provisioning profile, certificate authorization, TeamIdentifier and both signed entitlements. The installer/updater never re-signs a verified managed release locally because doing so would destroy the Apple-issued capability identity.
