# Contributing

感谢你为 hapsign 做贡献。提交改动前，请先确认不会把账号 token、证书、私钥、
Profile、设备 UDID 或签名后的 HAP 加入仓库。

## 开发环境

项目要求 Windows 和 Python 3.11 或更高版本。创建并激活虚拟环境后安装开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

需要手动验证完整登录流程时，再安装 Playwright 浏览器：

```powershell
playwright install chromium
```

## 提交前检查

```powershell
python -m ruff format .
python -m ruff check .
python -m pytest --cov
```

也可以安装 `pre-commit` 后运行 `pre-commit install`。仓库中的本地 hooks 使用当前
Python 环境已安装的 Ruff，不会在执行时下载额外工具。

## Pull request

- 每个 PR 聚焦一个问题，并说明行为变化和验证方式。
- 新增或修复逻辑时补充不依赖真实账号、网络和设备的测试。
- 不在测试中调用真实华为服务；使用 pytest 的 monkeypatch 或 mock。
- 若改动用户可见行为，在 `CHANGELOG.md` 的 Unreleased 小节记录。
- API 来自非公开实现时，应在说明中标注兼容性风险，不提交第三方反编译产物。
