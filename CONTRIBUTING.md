# Contributing

感谢你为 hapsign 做贡献。提交改动前，请先确认不会把账号 token、证书、私钥、
Profile、设备 UDID 或签名后的 HAP 加入仓库。

## 开发环境

项目支持 Windows、Linux 和 macOS，要求 Python 3.11 或更高版本。创建并激活
虚拟环境后安装开发依赖：

```bash
python -m pip install -r requirements-dev.txt
```

需要手动验证完整登录流程时，再安装 Playwright 浏览器：

```bash
playwright install chromium
```

## 提交前检查

```bash
python -m ruff format .
python -m ruff check .
python -m pytest --cov
```

也可以安装 `pre-commit` 后运行 `pre-commit install`。仓库中的本地 hooks 使用当前
Python 环境已安装的 Ruff，不会在执行时下载额外工具。

## 桌面版与打包

修改 GUI、运行时发现或打包配置后，除单元测试外还应构建一次当前平台的
PyInstaller 便携版，并运行打包后的 `--smoke-test`。完整步骤、工具链要求和
发布检查清单见 [docs/PACKAGING.md](docs/PACKAGING.md)。

## Pull request

- 每个 PR 聚焦一个问题，并说明行为变化和验证方式。
- 新增或修复逻辑时补充不依赖真实账号、网络和设备的测试。
- 不在测试中调用真实华为服务；使用 pytest 的 monkeypatch 或 mock。
- 若改动用户可见行为，在 `CHANGELOG.md` 的 Unreleased 小节记录。
- API 来自非公开实现时，应在说明中标注兼容性风险，不提交第三方反编译产物。
- 新增运行时依赖或便携包二进制时，同步更新 `THIRD_PARTY_NOTICES.md`，并提供
  可审计的来源、版本、哈希和再分发许可。
- 完整便携包的公开发布还必须通过 `docs/OPEN_SOURCE_RELEASE.md` 的许可与隐私门禁。
