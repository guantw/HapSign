# 开源发布门禁

本文区分“公开源代码”和“公开预编译便携包”。仓库采用 MIT License，并不自动赋予
发布者重新分发第三方二进制的权利。

## 源码仓库

公开前应确认：

- `LICENSE`、`README.md`、`THIRD_PARTY_NOTICES.md`、`PRIVACY.md`、
  `SECURITY.md`、`CONTRIBUTING.md` 和 `CODE_OF_CONDUCT.md` 存在；
- README 明确非官方性质、在线接口兼容性风险、数据流和第三方许可边界；
- 对所有“参考/移植自开源实现”的代码完成来源审计，记录上游仓库、commit 和许可；
  如果存在 GPL 或其他与整体 MIT 声明不兼容的复制代码，先解决许可和标注，不能仅在
  README 写一句致谢；
- Git 历史及当前工作区不含 HAP、token、UDID、证书、Profile、私钥、日志、
  本机配置或反编译产物；
- CI 能在无账号、无设备、无 DevEco 环境下完成 lint、format 和单元测试；
- 发布版本有 changelog、版本号、tag 和可复现的构建命令；
- 仓库设置已启用 GitHub Private vulnerability reporting，或 SECURITY 文件提供
  一个真实可用的私密安全联系方式。

## 预编译包

每个公开二进制包还必须满足：

- 根目录包含 HapSign `LICENSE`、隐私说明、第三方声明和构建说明；
- `licenses/python/` 包含冻结依赖随附的许可和 NOTICE；
- Temurin runtime 的 `legal/` 未被裁掉，OpenHarmony SDK 的 `NOTICE.txt` 已保留；
- `licenses/libusb-source/` 包含与 `libusb_shared` 对应的完整源码快照；
- 记录 Python、Qt、Playwright、Temurin、HDC、hap-sign-tool 和可选 Chromium 的准确
  版本、来源及 SHA-256；
- 工具链来自 `toolchain.lock.json` 锁定的公共上游，实际产物哈希与
  `PROVENANCE.txt` 一致；
- Qt 以可替换的共享库形式分发，并按选择的 LGPL-3.0 路径提供相应通知和许可文本；
- 兼容包保留 Chromium/Playwright 的第三方许可文件；
- 解压目录不含 `hapsign-config.json`、`logs/`、`signing_files/`、`signed_haps/`
  或任何构建者/测试者数据；
- 对最终 ZIP 运行恶意软件扫描、签名（若有代码签名证书）和 SHA-256 校验。

Windows 正式构建必须先运行 `python scripts/prepare_toolchain.py`，再运行
`python scripts/build_portable.py`。准备脚本锁定并校验 OpenHarmony 公共 SDK 与
Eclipse Temurin，只提取运行所需文件，并附带 libusb 对应源代码。

使用 `--allow-deveco-toolchain` 生成的是本机兼容/排障包，不通过公开发布门禁；
使用 `--skip-toolchain` 生成的是 GUI-only 包，也不能作为完整便携版发布。

## GitHub 发布建议

1. 整理并审查未提交改动。
2. 执行 `python -m ruff format --check .`、`python -m ruff check .` 和
   `python -m pytest --cov`。
3. 运行敏感文件扫描和 `git diff --check`。
4. 更新 `hapsign.__version__` 与 `CHANGELOG.md`。
5. 创建签名 tag。
6. 只上传已通过本页许可门禁的产物，并在 Release notes 中列出 SHA-256、支持平台、
   已知限制和第三方工具链来源。
