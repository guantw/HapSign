# HapSign CLI Portable

此 edition 是面向 Agent/脚本的集成 CLI，内置 Python、Java runtime、keytool、
`hap-sign-tool.jar`、HDC 和全部 Python 依赖。它支持 Windows x64 与以 Ubuntu 22.04
x64 为兼容基线的 Linux x64；其他 Linux 发行版属于实验支持。macOS 不发布 Portable，
macOS arm64 请使用 HapSign CLI External Toolchain，macOS x64 明确不支持。

Portable 不内置 Chromium。首次账号授权需要可交互的系统 Edge/Chrome；Linux 还可能
需要发行版/组织已经配置好的 USB 访问规则。这两项属于宿主系统能力，Prompt 不会自动
安装系统依赖、提升权限或修改 PATH。

解压后先验证：

```bash
./hapsign-cli --version
./hapsign-cli build-info --json
./hapsign-cli doctor --json
```

Windows PowerShell 使用 `.\hapsign-cli.exe`。`build-info` 应报告
`edition: portable`、`bundled_python: true`、`bundled_toolchain: true`、
`publisher_signed: false` 和 `notarized: false`；`doctor` 的 signing/device capability
都应可用。

常见调用：

```bash
./hapsign-cli inspect --hap /absolute/app.hap --json
./hapsign-cli devices list --connected-only --json
./hapsign-cli sign --hap /absolute/app.hap --serial <serial> --json
./hapsign-cli deploy --hap /absolute/app.hap --serial <serial> --json
./hapsign-cli install --hap /absolute/app-signed.hap --serial <serial> --json
```

只处理用户有权调试的 HAP。已签名包使用 `install`，未签名包使用 `sign`/`deploy`；
登录、验证码、2FA、账号选择与授权同意始终由用户完成。Agent 规范见
`AGENT_SIGNING.md`。

官方 Windows 二进制有意不做发布者软件签名。运行前验证 `SHA256SUMS`、manifest 与 GitHub
artifact attestation，不要绕过系统安全机制。有发布者签名要求时请从源码构建并自行签名。
