# HapSign

HapSign 为用户有权调试的 HarmonyOS HAP 自动申请调试材料、签名，并可推送到明确选择的
设备。它不是华为官方产品，也不提供 AppGallery 生产签名。

> [!IMPORTANT]
> 账号登录、验证码、2FA、账号选择和授权同意始终由用户完成。使用者应自行确认账号权限、
> HAP 授权范围、服务条款和数据安全。隐私边界见 [PRIVACY.md](PRIVACY.md)，第三方许可
> 见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## Release products

| Product | Platform | Python | Toolchain | Browser |
| --- | --- | --- | --- | --- |
| HapSign GUI | Windows x64 | bundled | bundled | bundled Chromium |
| HapSign CLI External Toolchain | Windows x64 | bundled | external | controlled system browser |
| HapSign CLI External Toolchain | Linux x64 | bundled | external | controlled system browser |
| HapSign CLI External Toolchain | macOS arm64 | bundled | external | controlled system browser |
| HapSign CLI Portable | Windows x64 | bundled | bundled | controlled system browser |
| HapSign CLI Portable | Linux x64 | bundled | bundled | controlled system browser |

Linux Portable 的兼容基线是 Ubuntu 22.04 x64，其他发行版为实验支持。macOS 只支持
arm64，明确不支持 x64；macOS 不发布 Portable。

两个 CLI edition 都内置 Python，因此不会要求用户安装 Python、创建 venv 或污染现有
Python 环境。External Toolchain 不内置 Java、keytool、`hap-sign-tool.jar` 与 HDC，
会自动发现 DevEco Studio、`DEVECO_HOME`、`JAVA_HOME`、PATH 或 `HAPSIGN_*` 显式
路径。Portable 内置由公共上游锁定并校验的工具链。准确范围见
[Tested environments](docs/TESTED_ENVIRONMENTS.md)。

官方二进制不使用 Windows/macOS 可信发布者身份，也不做 notarization。macOS arm64
因系统要求会带 PyInstaller 自动生成的无身份 ad-hoc 签名；这不是开发者签名。请使用 Release 中的
`SHA256SUMS`、`release-manifest.json` 和 GitHub artifact attestation 验证下载，不要
绕过 SmartScreen、Gatekeeper、杀毒软件或组织安全策略。有可信发布者签名要求的用户应
从源码构建并自行签名。

## Human and Agent delivery

人类用户下载 `HapSign-GUI-<version>-windows-x64.zip`，完整解压后运行
`HapSign.exe`。GUI 内置 Chromium 与完整工具链，目标电脑不需要 Python、DevEco、Java
或 HDC。说明见 [HapSign GUI](docs/GUI_DISTRIBUTION.md)。

Agent 用户下载对应 Release 的 Prompt：

- `HapSign-Prompt-ExternalToolchain-<version>.md`
- `HapSign-Prompt-Portable-<version>.md`

Prompt 会识别平台、下载正确 CLI、验证 SHA-256/manifest/build metadata，然后通过 CLI
执行。自动下载失败时，它必须给出完整资产名与 Release 地址，让用户手动下载后从校验步骤
继续。Prompt 不安装系统依赖、不修改 PATH、不提升权限，也不会把失败流程降级成裸 HDC、
Java 或签名 JAR。

## CLI quick start

源码安装后的命令是 `hapsign`；预构建目录使用 Windows 的 `hapsign-cli.exe` 或
Linux/macOS 的 `./hapsign-cli`：

```bash
hapsign --version
hapsign build-info --json
hapsign doctor --json
hapsign inspect --hap /absolute/app.hap --json
hapsign devices list --connected-only --json
hapsign sign --hap /absolute/app.hap --serial <serial> --json
hapsign deploy --hap /absolute/app.hap --serial <serial> --json
hapsign install --hap /absolute/app-signed.hap --serial <serial> --json
```

`sign` 只签名；`deploy` 对未签名包签名并安装；`install` 只接受已有 Hap Signing Block
的包。`deploy`/`install` 必须传明确且非空的 HDC serial。机器调用应使用 `--json`；
stdout 只有结果，日志在 stderr。完整协议与安全要求见
[Agent signing protocol](docs/AGENT_SIGNING.md)。

CLI 默认通过 Playwright 控制系统 Edge/Chrome 的隔离上下文，不读取用户默认浏览器
Profile。首次授权需要可交互图形浏览器；登录、验证码、2FA 和同意必须由用户完成。

## Shared user state

GUI 和所有 CLI edition 默认共享同一用户级状态：

- Windows: `%LOCALAPPDATA%\HapSign`
- macOS: `~/Library/Application Support/HapSign`
- Linux: `${XDG_STATE_HOME:-~/.local/state}/hapsign`

其中包含配置、日志、Token 缓存、按 bundle 隔离的调试签名材料和默认签名产物。可用
`HAPSIGN_DATA_DIR` 覆盖整个根目录，也可用 `HAPSIGN_SIGNING_DIR` 和
`HAPSIGN_SIGNED_HAPS_DIR` 单独覆盖。旧版程序目录/`~/.hapsign` 不会被静默搬迁；先
读取 `doctor --json`、`inspect --json`，再按[迁移指南](docs/MIGRATIONS.md)决定。

## Source installation

需要 Python 3.11+：

```bash
git clone https://github.com/guantw/HapSign.git
cd HapSign
python -m pip install .
hapsign doctor --json
```

桌面源码版：

```bash
python -m pip install -e ".[gui]"
hapsign-app
```

External Toolchain 的路径覆盖：

```bash
export JAVA_HOME=/opt/jdk-21
export HAPSIGN_HAP_SIGN_TOOL=/opt/ohos-sdk/toolchains/lib/hap-sign-tool.jar
export HAPSIGN_HDC=/opt/ohos-sdk/toolchains/hdc
hapsign doctor --json
```

Windows PowerShell 使用 `$env:NAME = "value"`。也可设置 `DEVECO_HOME`，或用
`HAPSIGN_JAVA`/`HAPSIGN_KEYTOOL` 精确覆盖。程序会在 `doctor` 中披露实际路径与来源；
未列入测试清单的版本不保证兼容。

## Build and publish

普通 CI 在 Windows、Ubuntu 和 macOS 运行 lint/test。Release 工作流仅从已有 tag 构建，
版本必须与 tag 一致；`-rc.N` tag 自动发布为 GitHub prerelease。

本地构建当前平台支持的全部产品：

```bash
python -m pip install -e ".[gui,bundle]"   # Windows GUI host
python scripts/prepare_toolchain.py         # Windows/Linux Portable and GUI
python -m playwright install --no-shell chromium  # Windows GUI
python scripts/build_release.py
```

Linux/macOS 只需 `.[bundle]`；macOS 只构建 External Toolchain。输出位于
`dist/release-assets/`。GitHub 汇总 job 要求恰好收到 6 个二进制归档，随后生成两个版本化
Prompt、`SHA256SUMS` 和 `release-manifest.json`，创建 provenance attestation，最后一次性
发布 Release。详细说明见 [Packaging](docs/PACKAGING.md) 和
[Open-source release gate](docs/OPEN_SOURCE_RELEASE.md)。

## Development

```bash
python -m pip install -e ".[dev]"
python -m ruff format --check .
python -m ruff check .
python -m pytest
```

安全问题按 [SECURITY.md](SECURITY.md) 私下报告。
