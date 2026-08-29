# Open-source binary release gate

The MIT source license does not by itself grant redistribution rights for third-party binaries.
Every public HapSign Release must satisfy the source, provenance, functional and safety gates below.

## Source gate

- lint, formatting and unit tests pass on the tagged commit;
- tag exactly matches `hapsign.__version__` after PEP 440/release spelling conversion;
- repository and history contain no HAP, Token, full UDID, certificate, Profile, private key,
  keystore, browser credential, local config or user log;
- license, privacy, security, contribution and third-party notice documents are current;
- changelog names breaking changes and migration IDs.

## Binary/provenance gate

- build only through `.github/workflows/release.yml` from an existing tag;
- receive exactly the six archives defined in `PACKAGING.md`;
- Portable/GUI toolchains come only from the public URLs and SHA-256 values in
  `toolchain.lock.json`; no DevEco installation is copied into an official package;
- package roots retain Python distribution licenses, Temurin legal notices, OpenHarmony NOTICE and
  the matching libusb source snapshot;
- each package contains correct `BUILD_INFO.json` and no config, Token, signing material, HAP or log;
- final `SHA256SUMS`, `release-manifest.json` and GitHub provenance attestation cover every binary
  archive and Prompt;
- run malware scanning appropriate to the repository before stable promotion.

## Deliberate unsigned policy

Official Windows and macOS artifacts do not use a trusted publisher identity and are not notarized.
This is a product decision, not an omitted optional step. Apple Silicon requires every arm64 Mach-O
to carry at least an ad-hoc signature, so PyInstaller automatically creates identity-less signatures
for the macOS CLI. They are required load metadata, not a Developer ID or publisher signature.
Release notes, package metadata, manifest and Prompts must disclose the distinction.

- never configure `signtool`, Apple Developer ID, a signing secret or notarization in the official
  workflow; accept only PyInstaller's mandatory macOS arm64 ad-hoc processing;
- never instruct users or Agents to disable/bypass SmartScreen, Gatekeeper, antivirus or organization
  controls;
- users requiring OS code signing build from source and sign under their own identity and policy;
- GitHub artifact attestation plus SHA-256 supplies build provenance/integrity, but is not presented as
  an OS publisher identity.

## Functional prerelease gate

CI proves packaging and deterministic interfaces; it cannot prove the interactive service/device
path. Before marking a prerelease stable, record the following against the exact downloaded assets:

- Windows GUI: clean-machine extraction, embedded Chromium login, CAPTCHA/2FA/consent, device
  discovery, unsigned-HAP debug signing, install and post-install inspection;
- Windows/Linux Portable: `build-info`, complete `doctor`, system-controlled browser login, HDC
  discovery, sign, deploy, signed-HAP install and post-install inspection;
- Windows/Linux/macOS External Toolchain: automatic tool discovery, actual path/source disclosure,
  missing-dependency guidance, tested DevEco/OpenHarmony combination, sign/install flow;
- zero devices, multiple devices, unauthorized device, missing browser and offline behavior;
- Prompt automatic download, deliberate download failure/manual fallback, SHA mismatch rejection and
  edition/platform mismatch rejection;
- `HAPSIGN-BREAKING-003` legacy-state detection without silent migration; all destructive warnings
  require a human choice;
- no stdout/log/Prompt output contains Token, private key, Profile content or full UDID.

Update `TESTED_ENVIRONMENTS.md` with the actual OS/tool/device versions and date. A “validation
target” is not promoted to tested until these results exist.

## Publish and rollback

Use `vMAJOR.MINOR.PATCH-rc.N` for the first public attempts. The workflow publishes such tags as
GitHub prereleases and never overwrites an existing Release. Promote by creating a new stable tag only
after all manual gates pass.

If a defect is found, mark the affected prerelease notes clearly and publish a new tag. Do not replace
assets in place: immutable names, hashes and attestations are part of the trust model.
