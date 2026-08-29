# HapSign CLI External Toolchain

此 edition 面向已有 HarmonyOS/DevEco 工具环境的用户和 Agent。包内已经包含 Python、
Playwright 控制层及全部 Python 依赖，因此目标电脑不需要安装 Python；它不包含 Java、
keytool、`hap-sign-tool.jar`、HDC 或 Chromium。

支持平台：Windows x64、Ubuntu 22.04 基线的 Linux x64，以及 macOS arm64。macOS x64
明确不支持，其他 Linux 发行版属于实验支持。

运行只读检查：

```bash
./hapsign-cli --version
./hapsign-cli build-info --json
./hapsign-cli doctor --json
```

Windows PowerShell 使用 `.\hapsign-cli.exe`。工具链按 Portable resources、常见
DevEco Studio 位置、`DEVECO_HOME`、`JAVA_HOME`/PATH 的顺序发现，并允许
`HAPSIGN_JAVA`、`HAPSIGN_KEYTOOL`、`HAPSIGN_HAP_SIGN_TOOL` 和 `HAPSIGN_HDC`
逐项覆盖。`doctor --json` 会披露实际路径、来源及 signing/device 两项能力；只有
`TESTED_ENVIRONMENTS.md` 列出的组合属于已测试范围，其他版本不保证兼容。

Agent 的完整安全流程见 `AGENT_SIGNING.md`。常见命令：

```bash
./hapsign-cli inspect --hap /absolute/app.hap --json
./hapsign-cli devices list --connected-only --json
./hapsign-cli sign --hap /absolute/app.hap --serial <serial> --json
./hapsign-cli deploy --hap /absolute/app.hap --serial <serial> --json
./hapsign-cli install --hap /absolute/app-signed.hap --serial <serial> --json
```

CLI 登录默认控制系统 Edge/Chrome，不读取默认浏览器 Profile；环境必须具备可交互图形
浏览器才能完成首次人工授权。Prompt 不会安装系统依赖或修改 PATH。

官方 Windows/macOS 二进制不使用可信发布者身份，也不做 notarization。macOS arm64
只有系统强制、由 PyInstaller 生成的无身份 ad-hoc 签名；它不代表开发者身份。验证
Release 哈希、manifest 与 GitHub artifact attestation，且不要绕过操作系统或组织安全
策略。有发布者签名要求时请从源码构建并自行签名。
