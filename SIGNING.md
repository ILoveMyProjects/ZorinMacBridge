# macOS signing model

ZorinMacBridge v0.6.6 does **not** require Developer ID, a self-signed certificate, a keychain identity, GitHub signing secrets, or signing setup on the Linux workstation.

## Why

macOS tracks privacy grants using the app's designated requirement (DR). The default DR of ad-hoc signed code is build-bound, so it changes after every rebuild. ZorinMacBridge therefore signs with an **explicit DR** instead of accepting the default ad-hoc DR.

Each Mac creates one random 256-bit local marker in:

`~/Library/Application Support/ZorinMacBridge/CodeSigning/local-identity-token`

Before an app is installed, ZorinMacBridge copies the verified release app to staging, adds `ZMBLocalIdentity=<local marker>` to the staged `Info.plist`, and signs the staged bundle with:

`designated => identifier "com.ilovemyprojects.zorinmacbridge.server" and info[ZMBLocalIdentity] = "<local marker>"`

The same marker is reused for future updates on that Mac, so the explicit DR remains the same across versions. No certificate or keychain is involved.

## Release transport

GitHub Actions uses ad-hoc signing only for transport integrity after the bundle is modified during packaging. The release workflow runs a fast macOS preflight which signs a small Mach-O and a minimal app with explicit DRs, verifies them with `codesign`, and rejects any fallback to a `cdhash`-based DR before the long arm64/x86_64 builds start.

The installer/updater verifies the downloaded app and checksum first. It then applies the Mac-local DR to a staged copy before replacing `/Applications/ZorinMacBridge Server.app`.

## Migration

The first v0.6.6 launch from an older build-bound/ad-hoc identity is a one-time identity migration. The app stages and signs a replacement with the stable local DR, asks for administrator authorization only to replace the bundle in `/Applications`, then restarts. Because the old and new DRs are different, macOS can require Screen Recording and Accessibility approval once at that migration boundary. Later v0.6.6+ updates reuse the same DR.

## Security trade-off

This is an internal/private distribution design, not a substitute for Developer ID and notarization. An explicit ad-hoc DR gives stable local identity semantics but does not cryptographically prove a public publisher. Public third-party distribution should use Developer ID and notarization.
