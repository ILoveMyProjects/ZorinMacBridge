# macOS code signing

ZorinMacBridge does **not** require a paid Apple Developer account, Developer ID certificate, notarization service, or paid provisioning profile to build or run the default release path.

## Default free release path

GitHub Actions builds the macOS arm64 app and DMG on `macos-26`. When no Apple signing secrets are configured, the workflow:

1. builds the native arm64 dylibs and PyInstaller app;
2. signs the transport app ad-hoc with an explicit bundle-identifier designated requirement using `scripts/sign-macos-transport.sh`;
3. verifies the ad-hoc signature;
4. packages `ZorinMacBridge-Server_macOS-arm64.dmg`.

No certificate, keychain identity, Apple Developer account, or paid service is required for this path.

On installation/update, the downloaded transport app is copied to a staging directory and re-signed **locally on that Mac** with a stable explicit ad-hoc designated requirement. The stable local identity is implemented by `mac_local_signing.py` and uses a random per-Mac token stored locally. No external account is involved. Reusing the same local identity across updates avoids changing the designated requirement on every build.

## Optional Developer ID path

Developer ID support remains available only as an optional enhancement for maintainers who already have the required Apple credentials. It is never required for CI, DMG creation, installation, updates, or READY.

If all three secrets are present:

- `MACOS_DEVELOPER_ID_P12_BASE64`
- `MACOS_DEVELOPER_ID_P12_PASSWORD`
- `MACOS_DEVELOPER_ID_PROFILE_BASE64`

then the release workflow validates the profile/certificate and uses `scripts/sign-macos-release.sh`. If none are present, the workflow automatically uses the free ad-hoc path. A **partially configured** optional Developer ID setup fails because it is internally inconsistent; removing the partial secrets restores the free path.

The optional profile verifier still knows about:

- `com.apple.developer.persistent-content-capture`
- `com.apple.developer.hid.virtual.device`

but those restricted entitlements are **not** part of the default free release requirement.

## READY/readiness

READY does not depend on Developer ID or provisioning. For unattended pre-login operation, readiness is based on the installed LoginWindow agent and a current Stage-B capability proof for the exact installed build. The probe decides whether capture and at least one input path actually work on the physical Mac.

This is deliberate: a paid signing state is not treated as a proxy for real LoginWindow functionality.

## Security checks retained on the free path

The free path still verifies:

- release SHA-256 checksums;
- expected bundle identifier;
- app version against the GitHub release;
- valid macOS code signature;
- explicit ZorinMacBridge designated requirement for the transport bundle;
- stable per-Mac local designated requirement before installation/update.

The free path does not claim Apple notarization or Developer ID publisher authentication.
