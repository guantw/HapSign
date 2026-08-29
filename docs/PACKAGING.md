# Release packaging

## Product matrix

| Product | Windows x64 | Linux x64 | macOS arm64 | Contents |
| --- | --- | --- | --- | --- |
| HapSign GUI | yes | no | no | Python + Qt + Chromium + locked toolchain |
| HapSign CLI External Toolchain | yes | yes | yes | Python, no toolchain/browser binary |
| HapSign CLI Portable | yes | yes | no | Python + locked toolchain, no Chromium |

macOS x64 is rejected by `scripts/build_release.py`. Linux binaries are built on Ubuntu 22.04 x64;
other distributions are experimental. Every CLI is PyInstaller onedir, not onefile. Users must keep
the extracted directory intact.

Official artifacts use no trusted publisher identity. `BUILD_INFO.json`, `release-manifest.json` and
Release notes all record this. The build has no Windows Authenticode, Apple Developer ID signing,
notarization or security-control bypass stage. PyInstaller necessarily applies an identity-less ad-hoc
signature to macOS arm64 Mach-O files; no signing certificate or secret is configured.

## Build inputs

- Python 3.13 on the Release runners; project supports source execution on 3.11–3.13.
- PyInstaller and the edition-specific Python dependencies.
- Windows GUI: Playwright Chromium installed with `PLAYWRIGHT_BROWSERS_PATH=0`.
- Windows/Linux GUI or Portable: `scripts/prepare_toolchain.py` output generated exclusively from
  `toolchain.lock.json`.

Public Release jobs never set `--allow-deveco-toolchain`. The legacy
`scripts/build_portable.py --allow-deveco-toolchain` path remains a local troubleshooting tool and
its output cannot pass the new release assembly gate.

## Local commands

Windows x64 builds all three host products:

```powershell
python -m pip install -e ".[gui,bundle]"
python scripts/prepare_toolchain.py
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
python -m playwright install --no-shell chromium
python scripts/build_release.py
```

Ubuntu 22.04 x64 builds both CLI products:

```bash
python -m pip install -e ".[bundle]"
python scripts/prepare_toolchain.py
python scripts/build_release.py
```

macOS arm64 builds External Toolchain only:

```bash
python -m pip install -e ".[bundle]"
python scripts/build_release.py
```

`--product gui|external_toolchain|portable` can select a supported host product. An unsupported
platform/product or architecture is a hard failure.

## Output names

For `v0.2.0-rc.2`, expected binary assets are:

```text
HapSign-GUI-v0.2.0-rc.2-windows-x64.zip
HapSign-CLI-ExternalToolchain-v0.2.0-rc.2-windows-x64.zip
HapSign-CLI-Portable-v0.2.0-rc.2-windows-x64.zip
HapSign-CLI-ExternalToolchain-v0.2.0-rc.2-linux-x64.tar.gz
HapSign-CLI-Portable-v0.2.0-rc.2-linux-x64.tar.gz
HapSign-CLI-ExternalToolchain-v0.2.0-rc.2-macos-arm64.zip
```

Linux uses tar.gz to preserve executable bits. The publish job then renders:

```text
HapSign-Prompt-ExternalToolchain-v0.2.0-rc.2.md
HapSign-Prompt-Portable-v0.2.0-rc.2.md
release-manifest.json
SHA256SUMS
```

The assembler fails on a missing or duplicate binary asset. It does not silently publish a partial
matrix.

## Package roots

All roots contain `BUILD_INFO.json`, edition README, project license, privacy notice, third-party
notices, migration/agent docs, tested environments, build instructions, and frozen Python licenses.
GUI and Portable additionally contain:

```text
resources/toolchain/<platform>/
├── runtime/
├── bin/hdc[.exe]
├── lib/hap-sign-tool.jar
├── NOTICE.txt
├── PROVENANCE.txt
└── toolchain.lock.json
```

The prepared toolchain also retains Temurin legal files and the libusb source snapshot required by
the redistribution record. External Toolchain packages must not contain `resources/toolchain`.
CLI packages must not contain `.local-browsers/chromium-*`; GUI retains Chromium but removes
Playwright FFmpeg because the application does not record video.

## Automated gates

Each matrix job:

1. freezes the product on its target host;
2. records precise frozen dependency licenses;
3. writes immutable edition/platform/architecture metadata;
4. smoke-tests executable version and JSON build information;
5. smoke-tests the complete bundled toolchain for GUI/Portable;
6. archives the complete onedir tree and creates a local SHA-256 sidecar.

The publish job validates the full matrix, renders prompts with the exact tag, creates the manifest
and `SHA256SUMS`, generates a GitHub SLSA provenance attestation, then calls `gh release create` once.
Tags containing `-alpha.`, `-beta.` or `-rc.` become prereleases; a stable tag becomes latest.

Before promotion from prerelease, perform the real-account/real-device gates in
`OPEN_SOURCE_RELEASE.md`. CI cannot automate CAPTCHA, 2FA, consent, USB authorization or
destructive migration decisions.
