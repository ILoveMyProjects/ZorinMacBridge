# macOS signing model

ZorinMacBridge 0.15.11 introduces the Stage C production signing model for the macOS server.

## Production release identity

A production `ZorinMacBridge Server.app` is expected to be:

- signed with **Developer ID Application**;
- built with Hardened Runtime and a secure timestamp;
- bundled with `Contents/embedded.provisionprofile`;
- authorized by that profile for both restricted entitlements required by the pre-login design:
  - `com.apple.developer.persistent-content-capture`
  - `com.apple.developer.hid.virtual.device`
- signed with the same Apple Developer team and App ID as the embedded profile.

The stable bundle identifier remains:

`com.ilovemyprojects.zorinmacbridge.server`

The release workflow fails closed when the certificate/profile secrets are absent, the profile is expired or device-limited, the App ID does not match, either restricted entitlement is missing, or the selected Developer ID certificate is not present in the profile.

## Apple approval is a prerequisite

Both Stage C capabilities are restricted. The repository cannot create or grant them itself. The Apple Developer account must first be approved for the requested capability and a matching Developer ID provisioning profile must be created for the ZorinMacBridge App ID.

For GitHub Actions, configure these repository secrets:

- `MACOS_DEVELOPER_ID_P12_BASE64` — base64-encoded Developer ID Application certificate + private key (`.p12`)
- `MACOS_DEVELOPER_ID_P12_PASSWORD` — password protecting that `.p12`
- `MACOS_DEVELOPER_ID_PROFILE_BASE64` — base64-encoded Developer ID `.provisionprofile` for `com.ilovemyprojects.zorinmacbridge.server`

Do **not** commit the certificate, private key, provisioning profile, or their base64 values to the repository.

## Release build flow

The macOS release job:

1. imports the Developer ID Application identity into a temporary CI keychain;
2. decodes the provisioning profile;
3. validates the profile before build;
4. tells PyInstaller to sign nested Mach-O code with the Developer ID identity;
5. embeds the profile as `Contents/embedded.provisionprofile`;
6. derives the final code-signing entitlements from the profile;
7. signs the outer application with Hardened Runtime and a secure timestamp;
8. verifies the final app with `codesign` and `mac_release_signing.py`;
9. removes the temporary CI keychain.

The final signed entitlement set is intentionally limited to:

- `com.apple.application-identifier`
- `com.apple.developer.team-identifier`
- `com.apple.developer.persistent-content-capture = true`
- `com.apple.developer.hid.virtual.device = true`

The application identifier and team identifier come from the Apple-issued profile rather than being hard-coded in the repository.

## Installation and updates

A Stage C production release is **never re-signed locally after download**.

The installer and updater verify the Developer ID signature, provisioning profile, App ID and restricted entitlements, then copy the verified bundle without changing its code signature. Updates from an already provisioned release also require Developer Team continuity and the installed designated requirement to accept the new release.

This is important: local ad-hoc re-signing would remove the Apple-authorized restricted-entitlement chain and can make the process unusable or rejected by macOS.

## Legacy local DR

`mac_local_signing.py` remains only as a legacy/source-development fallback for older installs and historical migration tooling. It must not be used to sign a Stage C production release.

`bootstrap_installed_app_identity()` explicitly detects a valid Developer-ID/provisioned release and returns without modifying it.

The old `scripts/sign-macos-transport.sh` is retained only for legacy regression/self-test coverage. The Stage C release workflow does not use it to produce the shipping macOS app.

## Verification commands

On a built Mac release:

```bash
codesign --verify --deep --strict --verbose=2 '/Applications/ZorinMacBridge Server.app'
codesign --display --entitlements - --xml '/Applications/ZorinMacBridge Server.app'
security cms -D -i '/Applications/ZorinMacBridge Server.app/Contents/embedded.provisionprofile'
```

Or use the app verifier:

```bash
'/Applications/ZorinMacBridge Server.app/Contents/MacOS/ZorinMacBridge Server' \
  --verify-release-app '/Applications/ZorinMacBridge Server.app'
```

A successful Stage C build must report both restricted entitlements in the code signature **and** in the embedded provisioning profile.

## Scope boundary

Stage C establishes signing/provisioning correctness. It does not prove that LoginWindow capture or virtual-keyboard injection works on a physical Mac. That proof remains Stage B acceptance and is required before the production pre-login runtime is redesigned around these capabilities.
