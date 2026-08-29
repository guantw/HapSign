# Tested environments

HapSign 不维护外部工具版本管理器。每个 Release 固定记录构建依赖和内置工具链版本；
外部组合只有列在本页且通过 prerelease 实机门禁后才属于已测试范围，其他版本可以尝试，
但不保证兼容。

## Locked Portable and GUI toolchain

- OpenHarmony public SDK: 6.1.0.31, API 23, 6.1-Release
- HDC: 3.2.0c（由锁定 SDK 提供）
- `hap-sign-tool.jar`: SHA-256 锁定于 `toolchain.lock.json`
- Java: Eclipse Temurin 21.0.12+8 的精简 `jlink` runtime
- Windows: x64
- Linux: Ubuntu 22.04 x64 baseline；其他发行版实验支持

这些组件由 `scripts/prepare_toolchain.py` 按大小与 SHA-256 校验，并把实际来源、版本和
哈希写入包内 `resources/toolchain/<platform>/PROVENANCE.txt`。

## External Toolchain validation targets

首个 prerelease 的外部工具链实机门禁目标如下；在验证记录完成前，应标记为
“validation target”，不能宣称已经支持：

- Windows x64: DevEco Studio 6.1.1.125 / OpenHarmony API 24 toolchain
- macOS arm64: DevEco Studio 6.1.1.125 / OpenHarmony API 24 toolchain
- Linux x64: OpenHarmony 6.1.0.31 / API 23 public toolchain and Temurin 21

最终 Release 应在本页补充实际测试日期、OS 版本、设备类型，以及签名、安装、授权与
迁移门禁结果。`doctor --json` 会披露运行时实际采用的路径和来源，但不会把未列出的版本
自动升级为“已保证兼容”。

## Release build hosts

- Windows: GitHub-hosted `windows-2022` x64
- Linux: GitHub-hosted `ubuntu-22.04` x64
- macOS: GitHub-hosted `macos-15` arm64
- Python used for freezing: 3.13

准确的 Python、Qt、Playwright、PyInstaller 等版本记录在每个包的
`licenses/python/DISTRIBUTIONS.txt`，而不是依赖滚动的“最新版本”描述。
