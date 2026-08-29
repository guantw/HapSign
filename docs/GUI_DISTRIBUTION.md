# HapSign GUI

HapSign GUI 是面向人类用户的 Windows x64 解压即用版本。它内置 Python、Qt、
Playwright Chromium、锁定的 Java runtime、keytool、`hap-sign-tool.jar` 与 HDC；
目标电脑不需要安装 Python 或 DevEco Studio。

解压完整目录后运行 `HapSign.exe`。连接已开启 USB 调试的 HarmonyOS 设备，在设备上
确认授权，选择或拖入有权调试的 `.hap` 文件，然后开始签名并安装。账号登录、验证码、
2FA 与授权同意均由用户在内置浏览器中手动完成。

官方二进制有意不做 Windows 发布者软件签名。请先用 Release 的 `SHA256SUMS`、
`release-manifest.json` 与 GitHub artifact attestation 验证下载；不要绕过 SmartScreen、
杀毒软件或组织安全策略。有发布者签名要求的用户应从源码构建并自行签名。

GUI 与所有 CLI edition 共享用户级状态：Windows 默认位于
`%LOCALAPPDATA%\HapSign`。可使用 `HAPSIGN_DATA_DIR`、`HAPSIGN_SIGNING_DIR` 和
`HAPSIGN_SIGNED_HAPS_DIR` 显式覆盖。旧版程序目录中的材料不会被静默搬迁；迁移警告
请按 `MIGRATIONS.md` 处理。

本工具只生成 HarmonyOS 调试签名，不用于 AppGallery 生产签名。第三方许可、隐私边界、
已测试版本与构建方式分别见包内 `THIRD_PARTY_NOTICES.md`、`PRIVACY.md`、
`TESTED_ENVIRONMENTS.md` 与 `BUILDING.md`。
