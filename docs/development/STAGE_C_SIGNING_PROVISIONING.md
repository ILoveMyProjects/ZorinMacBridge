# Stage C — Developer ID signing and restricted-entitlement provisioning

## TARGET

A production macOS release must preserve an Apple-authorized identity from GitHub Actions through installation and every update. It must not rely on ad-hoc re-signing for capabilities that macOS treats as restricted.

## Required restricted entitlements

- `com.apple.developer.persistent-content-capture`
- `com.apple.developer.hid.virtual.device`

The Apple-issued Developer ID provisioning profile must authorize both for the exact App ID `com.ilovemyprojects.zorinmacbridge.server`.

## Release gate

The release job fails when any of these conditions is true:

- Developer ID P12 secret missing;
- P12 password missing;
- provisioning-profile secret missing;
- profile expired;
- profile is device-limited instead of all-devices Developer ID distribution;
- App ID/team mismatch;
- either restricted entitlement missing;
- selected signing certificate not authorized by the profile;
- final app is ad-hoc signed;
- final app has no embedded profile;
- signed entitlements do not match the profile.

## Install/update invariant

A verified Stage C release is copied without changing its signature. Local ad-hoc signing is forbidden for a provisioned production release.

## STOP CONDITION

Do not claim remote LoginWindow login is solved by Stage C alone. After a successful signed build, Stage B must still be executed on the physical Mac to prove capture and input behavior.
