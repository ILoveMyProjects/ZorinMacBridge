# Stable macOS signing

ZorinMacBridge uses macOS Screen Recording and Accessibility privacy permissions.
Those permissions are associated with the app's code identity (designated requirement).

## Why ad-hoc releases ask again after an update

The fallback public GitHub Actions build uses ad-hoc signing when no Developer ID
certificate is configured. Apple documents that an ad-hoc designated requirement is
tied to that specific version of the code, so macOS cannot reliably treat the next
build as the same application for privacy permissions.

This means an ad-hoc update may require Screen Recording and Accessibility approval
again. ZorinMacBridge cannot securely bypass macOS TCC.

## Recommended public-release configuration

Configure these GitHub repository secrets:

- `MACOS_CERTIFICATE_P12_BASE64` — base64 of a Developer ID Application `.p12`
- `MACOS_CERTIFICATE_PASSWORD` — password protecting the `.p12`
- `MACOS_SIGNING_IDENTITY` — for example `Developer ID Application: Your Name (TEAMID)`

Optional notarization secrets:

- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_PASSWORD` — app-specific password for notarization

When the Developer ID secrets are present, the release workflow signs the macOS app
with that stable identity. Keep the bundle identifier
`com.ilovemyprojects.zorinmacbridge.server` unchanged between releases.

When the three notarization secrets are also present, the workflow submits each DMG
to Apple's notary service and staples the result.

Do not commit a `.p12`, private key, certificate password, Apple ID password, or
notarization credential to the repository.
