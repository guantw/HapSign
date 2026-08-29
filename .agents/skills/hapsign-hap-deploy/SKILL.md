---
name: hapsign-hap-deploy
description: Install or deploy authorized HarmonyOS HAPs on an explicitly selected connected device through the hapsign CLI. Use for device deployment; use hapsign-signing for sign-only or signing-troubleshooting workflows.
---

# HapSign HAP Deploy

Use the installed `hapsign` CLI as the only implementation layer. It owns Huawei
authentication, token caching, device-bound Profiles, signing, HDC installation,
and post-install bundle checks. Do not recreate those steps with DevEco, raw HDC,
or skill-local scripts.

## Safe deployment workflow

Use the CLI's read-only checks before authentication or deployment:

```bash
hapsign doctor --json
hapsign inspect --hap <hap> --json
hapsign devices list --connected-only --json
```

When deployment needs a Real Profile/system_basic, pass `--enable-capability`
to both `inspect` and `deploy` so cache compatibility is evaluated consistently.

- Require `capabilities.device.ok`; also require `capabilities.signing.ok` for
  an unsigned HAP.
- If `migration_warnings` contains an item with `destructive=true` and
  `requires_user_decision=true`, explain its impact and remediation and stop
  for the user's choice. Do not silently refresh or migrate signing materials.
- Honor a serial supplied by the user. Otherwise select the sole
  `physical_candidate=true` target; ask the user if zero or multiple physical
  candidates remain. Do not select `likely_emulator=true` unless requested.

Then use JSON and the explicit HDC serial. Installing an already signed HAP does
not require Huawei authentication:

```bash
hapsign install --hap <signed.hap> --serial <serial> --json
hapsign deploy --hap <hap> --serial <serial> --json
```

- Use `install` for an already signed HAP and `deploy` for end-to-end signing
  and installation. `deploy` also accepts signed HAPs.
- For an unsigned HAP, `deploy` can perform authentication itself. If no current
  cache exists and the request does not already authorize account authentication,
  tell the user that browser authorization will be required before running it.
  Use a separate `auth status`/`auth` step only when the user wants authentication
  prepared independently; never authenticate for a signed-only `install`.

Treat success as exit code `0` plus JSON `ok=true`. Parse stdout as JSON and treat
stderr as diagnostic logs. On failure, report `error.type`, `error.message`, and
the exit code; do not fall back to manual signing or raw HDC installation. Never
print or copy tokens, passwords, signing keys, Profiles, or device UDIDs.
When `--enable-capability` was requested, report `capability_fallback=true` as a
successful debug install whose effective Profile is still normal, not as a
successful system_basic deployment.
