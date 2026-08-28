---
name: hapsign-hap-deploy
description: Authenticate, sign, install, or deploy unsigned and signed HarmonyOS HAPs on an explicitly selected connected device through the hapsign CLI. Use for HAP deployment; not for building HAPs, emulator-only workflows, or manual DevEco/HDC signing.
---

# HapSign HAP Deploy

Use the installed `hapsign` CLI as the only implementation layer. It owns Huawei
authentication, token caching, device-bound Profiles, signing, HDC installation,
and post-install bundle checks. Do not recreate those steps with DevEco, raw HDC,
or skill-local scripts.

## Commands

Always request JSON and pass an explicit HDC serial:

```bash
hapsign devices list --connected-only --json
hapsign auth status --json
hapsign auth --json
hapsign sign --hap <unsigned.hap> --serial <serial> --json
hapsign install --hap <signed.hap> --serial <serial> --json
hapsign deploy --hap <hap> --serial <serial> --json
```

- Honor a serial supplied by the user. Otherwise select the sole
  `physical_candidate=true` target; ask the user if zero or multiple physical
  candidates remain. Do not select `likely_emulator=true` unless requested.
- Use `sign` for signing only, `install` for an already signed HAP, and `deploy`
  for end-to-end signing and installation. `deploy` also accepts signed HAPs.
- Check `auth status` before an operation that may need login. If no current cache
  exists and the request does not already authorize account authentication, tell
  the user that browser authorization is required before running `auth`.

Treat success as exit code `0` plus JSON `ok=true`. Parse stdout as JSON and treat
stderr as diagnostic logs. On failure, report `error.type`, `error.message`, and
the exit code; do not fall back to manual signing or raw HDC installation. Never
print or copy tokens, passwords, signing keys, Profiles, or device UDIDs.
