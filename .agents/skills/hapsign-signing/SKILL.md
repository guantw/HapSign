---
name: hapsign-signing
description: Inspect, debug-sign, and optionally install authorized HarmonyOS .hap packages with HapSign on Windows, Linux, or macOS using machine-readable JSON and user-assisted Huawei authorization. Use for local HAP debug signing and troubleshooting, not production AppGallery signing or generic OS code signing.
---

# HapSign HAP signing

Use HapSign only for authorized local HarmonyOS development and debugging. It
creates Huawei debug signing materials; it does not replace AppGallery
production-release signing.

## Locate and identify the CLI

Prefer, in order:

1. The path in `HAPSIGN_CLI`.
2. The complete portable package in the current HapSign repository:
   `dist/HapSign/hapsign-cli.exe` on Windows or
   `dist/HapSign/hapsign-cli` on Linux/macOS.
3. A `hapsign` source-install command on `PATH`.
4. A complete portable folder supplied by the user.

Do not use a copied standalone executable without its sibling `resources/`.
Resolve the CLI, input HAP, and output HAP to absolute paths. For portable
builds, run with the CLI parent directory as the working directory so older
builds also keep caches beside the application. If no usable CLI exists, report
the paths checked and ask where HapSign is installed.

Before processing a HAP, run:

```text
<cli> --version
<cli> doctor --json
```

In PowerShell invoke a quoted executable with `&`. On POSIX, invoke the
executable directly.

Interpret doctor capabilities separately:

- `capabilities.signing.ok` is required to sign an unsigned HAP.
- `capabilities.device.ok` is required for installation and for obtaining a
  first-time Profile UDID from a connected device.
- Top-level `ok` may be false while sign-only remains possible with valid
  cached signing materials or a trusted explicit UDID.
- Read `paths.state_dir` and `paths.output_dir`; do not infer storage from the
  process working directory. Report relevant entries from `breaking_changes`
  when upgrading an existing installation.

## Safe agent workflow

1. Inspect the input without logging in, connecting a device, or changing it:

   ```text
   <cli> inspect --hap <absolute-input.hap> --json
   ```

   If the requested signing mode needs a Real Profile/system_basic, include
   `--enable-capability` in both this inspection and the later `sign` or
   `deploy` command. Cache compatibility is evaluated against that mode.

   Read `migration_warnings` before continuing. If a warning has
   `destructive: true` and `requires_user_decision: true`, explain its impact
   and remediation, then wait for the user's choice. Never silently accept a
   destructive migration.

   For `HAPSIGN-BREAKING-001`, inspect `reasons`. A sole
   `capability_mode_mismatch` can be resolved by consistently matching the
   cached mode, or by backing up `paths.work_dir` and accepting a refresh when
   the user intends to switch modes. Offer metadata migration only when the
   warning reports `migratable: true` and the user confirms the legacy Profile
   type; then run one of:

   ```text
   <cli> migrate-cache --hap <absolute-input.hap> \
     --state-dir <paths.state_dir> --profile-type normal --json
   <cli> migrate-cache --hap <absolute-input.hap> \
     --state-dir <paths.state_dir> --profile-type system-basic --json
   ```

   Do not guess the legacy Profile type. The migration command only updates
   metadata and legacy relative material paths, keeps a backup, and must return
   `command: migrate-cache` and `ok: true` before reuse. A stale or incomplete
   cache cannot be migrated; back it up and allow refresh instead.

2. For a signing request, default to sign-only. Choose a new absolute `.hap`
   output path and verify that it does not already exist:

   ```text
   <cli> sign --hap <absolute-input.hap> \
     --output <absolute-output.hap> --browser system_controlled --json
   ```

   `system_controlled` uses an isolated Edge/Chrome context without the user's
   cookies or saved passwords and grants the authorization page local callback
   access. Use `system` only when the user explicitly prefers their normal
   browser profile or controlled launch is unavailable; disclose that cached
   SSO state, extensions, and local-network permissions can change the flow.
   Use `playwright` only when bundled Chromium is explicitly preferred.

   If persistent locations matter, pass absolute `--state-dir` and either an
   exact `--output` or `--output-dir`. CLI flags override
   `HAPSIGN_SIGNING_DIR` / `HAPSIGN_SIGNED_HAPS_DIR`, which override the
   application defaults. Never place signing state in shared or cloud-synced
   storage.

3. Never add `--overwrite-output` unless the user explicitly authorizes
   replacing that exact file. Add `--device-udid` only for a trusted,
   user-authorized 64-character UDID. Add `--enable-capability` only when the
   user requests a Real Profile/system_basic capability or the task clearly
   requires it.

4. If authorization opens, tell the user to complete login, CAPTCHA, consent,
   and two-factor verification manually, then wait for the process. Never
   request, read, store, type, or automate credentials, CAPTCHA, one-time codes,
   consent clicks, or tokens.

5. Treat a nonzero exit code or JSON `ok: false` as failure. On success, read
   `signed_hap` from JSON instead of guessing a path, then verify it:

   ```text
   <cli> inspect --hap <signed_hap> --json
   ```

   Report success only when the verification returns `signed: true`. For an
   already-signed input, accept the `signed_hap` path returned by the CLI even
   though no cryptographic signing step ran; do not infer it from the input.
   When a Real Profile was requested, also inspect `capability_fallback` and
   `capability_mode`. A fallback result is a valid debug signature but did not
   satisfy system_basic; disclose that limitation instead of reporting the
   requested capability as successful.

6. Install only when the user explicitly requests device installation. Use
   `deploy --hap <input> --serial <serial>` for signing plus installation, or
   `install --hap <signed_hap> --serial <serial>` for an already signed HAP.
   Require a connected authorized device and report the returned `installed`
   value. Signing permission alone does not authorize installation.

## Authorization diagnostics

Keep logs at a nonsensitive level and use stage markers:

- No `[callback]` entry: the browser did not reach the loopback callback;
  check browser mode and local-network access.
- Callback POST/GET without `授权回调校验成功`: inspect the redacted CSRF or
  parameter error.
- Callback validation succeeded: browser spinning or subsequent
  `net::ERR_ABORTED` requests are usually page shutdown after the callback;
  investigate token exchange or later pipeline stages instead.
- A Windows DPAPI cache decryption failure should trigger a fresh user login.
  Linux/macOS token caches are plaintext files restricted to mode `0o600`; do
  not place them in shared or cloud-synced storage. Do not delete signing state
  unless the user explicitly requests cleanup.

When diagnosing a GUI run, first identify the actual executable and its log
directory; do not assume source CLI, portable CLI, and GUI share a directory in
older builds.

## Sensitive data and boundaries

- `signing_files/` contains private keys, certificates, Profiles, and login
  cache. Never print, commit, upload, or place it in untrusted/shared storage.
- Keep sensitive logging disabled. Do not expose full UDIDs, account identifiers,
  tokens, passwords, private-key material, or complete authentication payloads.
- Network access and a verified Huawei developer account are required when
  cached authorization/signing materials are unavailable.
- This skill is for HAP debug signing. Route EXE/MSI signing, certificate
  issuance, and production release signing elsewhere.
