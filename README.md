# hapsign

通过华为账号自动生成 HarmonyOS 调试签名，对未签名的 hap 包签名并安装到设备。

> [!IMPORTANT]
> 本项目是非官方工具，与华为无隶属或背书关系。它依赖可能变化的在线接口，仅用于合法的
> 本机开发和调试。使用者应自行确认账号权限、数据安全以及相关服务条款。

## 工作原理

```
Playwright 打开华为登录页（用户手动登录）
  → 拿 tempToken → 换 accessToken
  → 调华为云签名 API 生成 .cer / .p7b
  → hap-sign-tool 签名 hap
  → hdc install 安装到设备
```

## 前置条件

1. **DevEco Studio** 已安装（需要其中的 JBR Java、hap-sign-tool.jar、hdc.exe）
2. **Python 3.11+**（推荐使用 conda 或 venv 隔离环境）
3. **HarmonyOS 设备**已通过 USB 连接并开启 USB 调试模式
4. **华为开发者账号**（需要已完成实名认证）

## 安装

```bash
# 克隆仓库
git clone https://github.com/guantw/HapSign.git
cd HapSign

# 安装项目（提供 hapsign 命令）
python -m pip install .

# 安装 Playwright 浏览器（首次必须）
playwright install chromium
```

### 配置 DevEco Studio 路径

默认查找路径为 `D:\Program Files\Huawei\DevEco Studio`。如果你的 DevEco Studio 安装在其他位置，设置环境变量：

```bat
:: Windows CMD
set DEVECO_HOME=E:\DevEco Studio

:: PowerShell
$env:DEVECO_HOME = "E:\DevEco Studio"
```

调试密钥库默认使用兼容 DevEco 调试流程的固定密码。如需覆盖，请设置：

```powershell
$env:HAPSIGN_KEYSTORE_PASSWORD = "使用你自己的强密码"
```

### 配置 Python 路径（bat 脚本用）

`sign_install.bat` 默认使用系统 PATH 中的 `python`。如果使用 conda/venv，设置环境变量：

```bat
:: Windows CMD
set HAPSIGN_PYTHON=C:\path\to\your\python.exe

:: PowerShell
$env:HAPSIGN_PYTHON = "C:\path\to\your\python.exe"
```

## 使用

### 方式一：拖拽（推荐）

将 `.hap` 文件直接拖到 `sign_install.bat` 上，自动完成签名+安装。

### 方式二：命令行

```bash
hapsign --hap path\to\app-unsigned.hap
```

包名会自动从 hap 内的 `module.json` 提取，无需手动指定。
源码目录中仍可使用 `python main.py --hap ...`。

### 完整参数

```
hapsign --hap <hap路径> [选项]

选项:
  --hap               未签名的 hap 文件路径（必填）
  --bundle-name       应用包名（不传则从 hap 内自动提取）
  --country           国家码，默认 CN
  --device-type       设备类型码，默认 4
  --work-dir          签名文件存储目录，默认 signing_files/{bundle_name}/
  --enable-capability 使用 Real Profile（APL=system_basic），用于需要高权限的应用
  --refresh-token     强制刷新 token 缓存（重新登录，连带刷新签名文件）
  --refresh-signing   强制刷新签名文件缓存（重新申请，不重新登录）
  -v, --verbose       显示调试日志
  --version           显示版本号

设备类型码:
  4  手机/平板（默认）
  2  穿戴设备
  8  智慧屏
  9  路由器
  1  轻量级穿戴设备
```

### 首次运行

会弹出浏览器窗口，打开华为登录页。手动输入账号密码登录，如果有验证码或二次验证也手动处理。登录成功后浏览器会自动关闭，后续自动完成签名和安装。

### Real Profile（system_basic 权限）

默认创建的 Test Profile APL 为 `normal`。大多数需要 ACL 预授权的权限（如 `CUSTOM_SANDBOX`、`READ_WRITE_USER_FILE` 等）在 Test Profile 下即可授予。

如果应用需要 `system_basic` 级别的 APL，加 `--enable-capability` 参数走 Real Provision 路径：

```bash
hapsign --hap app.hap --enable-capability
```

此模式通过 `add.real.provision` API 创建 Real Profile（provisionType=1），对应 DevEco Studio 6.1+ 的 `enableCapability` 路径。需要应用已在 AGC（AppGallery Connect）注册且当前账号有访问权限，否则自动回退到 Test Profile。

### 签名后的文件

签名后的 hap 和签名材料保存在 `signing_files/{bundle_name}/` 下：

```
signing_files/com.example.myapp/
├── auto_debug_com.example.myapp.p12   # 密钥库
├── auto_debug_com.example.myapp.csr   # CSR
├── auto_debug_com.example.myapp.cer   # 调试证书
├── auto_debug_com.example.myapp.p7b   # 调试 Profile
├── metadata.json                        # 缓存元数据
└── entry-default-unsigned_signed.hap   # 签名后的 hap
```

## 缓存策略

同一天内不会重复登录或重复申请签名文件：

- **Token 缓存**：`signing_files/.token_cache.json`，当天复用，不重新登录
- **签名文件缓存**：`signing_files/{bundle_name}/metadata.json`，当天复用，不重新申请证书/设备/Profile
- 跨天自动失效，重新走完整流程
- Token 失效时自动刷新，刷新失败才回退到重新登录

这些文件包含明文敏感信息，不要上传、分享或放入云同步目录。共享电脑使用完毕后应删除
`signing_files/`。详细说明见 [SECURITY.md](SECURITY.md)。

## 限制

- 登录验证码 / 二次验证需要用户在浏览器中手动处理
- 仅支持 Windows（SDK 路径、bat 脚本均为 Windows 环境）
- 签名流程依赖华为云 API，需要有网络连接和华为开发者账号

## 开发与贡献

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff format .
python -m ruff check .
python -m pytest --cov
```

测试不需要真实账号、网络、DevEco Studio 或 HarmonyOS 设备。贡献流程见
[CONTRIBUTING.md](CONTRIBUTING.md)，安全问题请按 [SECURITY.md](SECURITY.md) 报告。

## 项目结构

```
hapsign/
├── cli.py                # 命令行参数和入口
├── config.py             # 配置常量（域名、SDK 路径、API 端点、密钥参数）
├── models.py             # 数据模型
├── pipeline.py           # 全流程编排（缓存、登录、签名、安装）
├── login/
│   └── browser_login.py  # Playwright 浏览器登录
├── token/
│   └── token_exchange.py # tempToken → JWT → accessToken
├── api/
│   ├── client.py         # HTTP 客户端（认证 header 封装）
│   ├── cert_api.py       # 证书 API (cert/add, cert/list, cert/delete)
│   ├── device_api.py     # 设备 API (device/add, device/list)
│   ├── provision_api.py  # Profile API (test/real provision add, delete)
│   └── capability_api.py # 应用信息 API (app brief info)
└── signing/
    ├── keytool_util.py   # keytool 生成 EC 密钥对 + CSR
    ├── hap_signer.py     # hap-sign-tool 签名 hap
    └── installer.py     # hdc install / 获取 UDID
```

## License

本项目使用 [MIT License](LICENSE)。参与项目需遵守 [Code of Conduct](CODE_OF_CONDUCT.md)。

Powered by [BitFun](https://github.com/GCWing/BitFun)
