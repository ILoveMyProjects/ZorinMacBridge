# macOS code signing

ZorinMacBridge has two distinct macOS capability tiers.

## 1. Normal Aqua Desktop — free path remains supported

Normal logged-in Desktop/Files/System/Terminal operation does **not** require Developer ID. When Apple signing credentials are not configured, GitHub Actions may build the arm64 app/DMG with the existing ad-hoc transport signature. Installation/update then applies the stable per-Mac local ad-hoc designated requirement from `mac_local_signing.py`.

This path remains intentionally supported for normal Aqua operation. It must not be reported as reboot-to-LoginWindow capable.

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

The release workflow uses this path only when all three maintainer secrets are configured:

- `MACOS_DEVELOPER_ID_P12_BASE64`
- `MACOS_DEVELOPER_ID_P12_PASSWORD`
- `MACOS_DEVELOPER_ID_PROFILE_BASE64`

A partially configured Developer ID setup fails rather than producing an ambiguous build. When no Apple credentials are configured, the workflow still produces the free Aqua-only build.

## Installer and updater behavior

The macOS installer verifies the downloaded app before copying it into `/Applications`.

- Managed Developer ID build: preserve the publisher signature and embedded provisioning profile unchanged.
- Free build: apply the stable per-Mac local ad-hoc identity for Aqua operation.
- Existing managed pre-login installation + incoming free/ad-hoc build: **reject the downgrade**. Removing the managed entitlements silently would make reboot-login fail after an otherwise successful update.

The automatic updater follows the same rule. Migration from Aqua-only ad-hoc to the managed Developer ID build is allowed; the reverse transition is refused.

The root unattended installer configures everything root can configure locally: the P0 helpers, LoginWindow LaunchAgent, ownership/permissions, SSH prerequisites, update service and persistent state. It also reports `PRELOGIN_MANAGED_CAPABILITIES=ready|blocked`. Root access does not synthesize Apple-managed entitlements; those must already be present in the signed/provisioned application.

## Physical capability proof is still mandatory

Managed signing is a prerequisite, not proof that the target Mac accepts the feature at LoginWindow. The Stage-B probe still runs in the real LoginWindow session under the exact installed app identity. Pre-login becomes VERIFIED only when the proof is current for the installed version + CDHash and reports:

- `capture_result=PASS`;
- `input_corehid_result=PASS`;
- `overall=PASS`.

Legacy IOHID/Quartz remains diagnostic comparison data only. Starting with 0.15.24, a legacy-input PASS cannot satisfy the production pre-login readiness contract.

## Security / integrity invariants

Both release tiers retain checksum, bundle-id, version and code-signature verification. The managed tier additionally validates the provisioning profile, certificate authorization, TeamIdentifier and both signed entitlements. The installer/updater never re-signs a verified managed release locally because doing so would destroy the Apple-issued capability identity.
