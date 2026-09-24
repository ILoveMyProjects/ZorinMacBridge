# macOS code identity in ZorinMacBridge

ZorinMacBridge does **not** require a Developer ID, a GitHub signing secret, or a certificate created on the Linux workstation for the private/internal workflow used by this project.

## How v0.6+ works

GitHub Actions creates a short-lived **transport signature** for each macOS release artifact. That signature exists only so the downloaded `.app` is structurally signed and older ZorinMacBridge updaters can validate/install the migration release.

Before an app is installed on a Mac, ZorinMacBridge creates one **persistent local code-signing identity on that Mac** and re-signs the staged app with it. The private key remains on that Mac under:

```text
~/Library/Application Support/ZorinMacBridge/CodeSigning/
```

Future in-app updates are downloaded and checksum-verified, copied to a staging directory, re-signed with the same local identity, checked for the expected bundle ID and designated requirement, and only then installed into `/Applications`.

The designated requirement is pinned to both:

```text
com.ilovemyprojects.zorinmacbridge.server
```

and the certificate belonging to that Mac's persistent local identity.

This prevents each GitHub release build from becoming a new privacy identity on that Mac.

## Migration from v0.5.x

v0.6.3 is the first migration release intended for publication after the macOS CI signing fixes. Older updaters can install its transport-signed app. On the first v0.6.3 launch, if the app in `/Applications` does not yet use the Mac's persistent local identity, ZorinMacBridge automatically:

1. creates/reuses the local identity;
2. stages a copy of the installed app;
3. signs the staged copy with the persistent local identity;
4. asks macOS for administrator authorization to replace the app in `/Applications`;
5. restarts the freshly signed app.

Because this changes from the old v0.5.x identity to the new persistent per-Mac identity, macOS can require Screen Recording and mouse/keyboard authorization **once at this migration point**. Future v0.6+ updates on that Mac reuse the same identity.

## Important

Do not delete the `CodeSigning` directory above if you want that Mac to retain the same ZorinMacBridge code identity. Deleting it causes a new identity to be created later, which can require privacy permissions again.

There is no `setup-stable-signing-linux.sh` in v0.6+. No `gh auth login`, GitHub secret upload, SSH-key modification, or Developer ID setup is part of this workflow.

For public third-party distribution, Apple Developer ID signing and notarization are still the conventional deployment mechanism. The per-Mac local identity described here is intended for this project's private/internal machines.
