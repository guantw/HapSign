# Changelog

本项目的显著变更记录在此文件中，格式参考 Keep a Changelog，版本遵循语义化版本。

## Unreleased

### Added

- 可安装的 `hapsign` 命令和标准 Python 项目元数据。
- Ruff、pytest、覆盖率、pre-commit 和 Windows CI 配置。
- 贡献指南、安全策略、行为准则和统一编辑器配置。
- CLI、缓存、HTTP 响应和权限提取的单元测试。

### Security

- 登录回调服务仅监听 loopback 地址。
- 登录日志不再包含 token、请求体、CSRF code、用户 ID、设备 UDID 或完整登录 URL。
- 限制登录回调请求体大小，并尽力收紧 token 缓存文件权限。
