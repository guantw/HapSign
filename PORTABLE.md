# HapSign release editions

原先把 GUI、CLI 和工具链放在同一便携目录的发布方式已经停止。当前 Release 是三个独立
产品系列：

- HapSign GUI：Windows x64，内置 Python、Chromium 和锁定工具链。
- HapSign CLI External Toolchain：Windows x64、Linux x64、macOS arm64；内置 Python，
  使用宿主机 DevEco/OpenHarmony 工具链。
- HapSign CLI Portable：Windows x64、Linux x64；内置 Python 和锁定工具链，使用受控
  系统浏览器。

各归档完整解压后运行，不能只复制单个 exe；CLI 使用 PyInstaller onedir，以保留可审计
依赖并降低 onefile 临时解包问题。GUI 与 CLI 默认共享用户级状态，不把 Token、密钥、
Profile、日志或签名产物保存在发布目录中。

官方二进制不使用可信发布者身份或 notarization；macOS arm64 只有系统强制的无身份
ad-hoc 签名。运行前验证 `SHA256SUMS`、`release-manifest.json` 与 GitHub artifact
attestation；有发布者签名要求时从源码自行构建、签名。详细说明见
`docs/GUI_DISTRIBUTION.md`、`docs/CLI_EXTERNAL_TOOLCHAIN.md`、
`docs/CLI_PORTABLE.md` 和 `docs/PACKAGING.md`。
