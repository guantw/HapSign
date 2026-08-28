# hapsign

通过华为账号自动生成 HarmonyOS 调试签名，对未签名的 hap 包签名并安装到设备。

> [!IMPORTANT]
> 本项目是非官方工具，与华为无隶属或背书关系。它依赖可能变化的在线接口，仅用于合法的
> 本机开发和调试。使用者应自行确认账号权限、数据安全以及相关服务条款。程序的数据流
> 和本地保存行为见 [PRIVACY.md](PRIVACY.md)，第三方许可边界见
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 工作原理

```
检测 HAP 是否已签名
  ├─ 已签名 → hdc install 直接安装
  └─ 未签名 →
       Playwright 控制系统 Edge/Chrome 打开登录页（用户手动登录）
         → 拿 tempToken → 换 accessToken
         → 调华为云签名 API 生成 .cer / .p7b
         → hap-sign-tool 签名 hap
         → hdc install 安装到设备
```

## 前置条件

1. **签名/设备工具链**：便携版已内置；源码运行可使用已准备的公开工具链或
   DevEco Studio
2. **Python 3.11+**（推荐使用 conda 或 venv 隔离环境）
3. **HarmonyOS 设备**已通过 USB 连接并开启 USB 调试模式
4. **华为开发者账号**（需要已完成实名认证）

命令行支持 **Windows** 与 **macOS**；当前正式便携版为 Windows。

## 直接使用 Windows 便携版（推荐）

普通使用者不需要安装 Python、DevEco Studio、Java 或 HDC。到 GitHub Releases
下载 `HapSign-portable-windows.zip` 及旁边的 `.zip.sha256` 校验文件，先在
PowerShell 中核对下载完整性：

```powershell
Get-FileHash .\HapSign-portable-windows.zip -Algorithm SHA256
Get-Content .\HapSign-portable-windows.zip.sha256
```

确认哈希一致后，将 ZIP 解压到当前用户可写的目录（不要放入 `Program Files`），
双击 `HapSign.exe` 即可。精简包会复用系统 Edge/Chrome；如果发布页同时提供
`HapSign-portable-windows-compat.zip`，它包含内置 Chromium，适合没有可用系统
浏览器的电脑。

首次使用时连接已开启 USB 调试的 HarmonyOS 设备，并在设备上确认调试授权；在窗口中
点击“检测设备”，然后拖入或选择 `.hap`，点击“开始签名并安装”。未签名 HAP 会打开
受控浏览器完成华为账号登录和验证码/二次验证，之后自动申请材料、签名并安装；已签名
HAP 会跳过登录和签名直接安装。签名材料、日志和可选的签名后 HAP 的位置见
[便携版说明](PORTABLE.md)。

## 从源码安装

```bash
# 克隆仓库
git clone https://github.com/guantw/HapSign.git
cd HapSign

# 安装项目（提供 hapsign 命令）
python -m pip install .

```

源码桌面版默认由 Playwright 控制本机 Edge（其次 Chrome），仍会预授予登录页访问
本地回调服务的权限，因此不同于直接调用系统默认浏览器的旧方案。Windows 10/11
通常已经包含 Edge，不需要额外下载 Chromium。

如需使用内置 Chromium 兼容模式：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
python -m playwright install --no-shell chromium
```

桌面设置提供“受控系统浏览器”“内置 Chromium”和“非受控系统默认浏览器”三种
模式；环境变量 `HAPSIGN_BROWSER` 可使用 `system_controlled`、`playwright`
或 `system` 覆盖代码默认值。

### 可选：配置 DevEco Studio 回退路径

正式便携版不需要 DevEco Studio。源码运行时程序会优先使用已准备的公开工具链，只有
在排查特定 DevEco 版本兼容性时才需要本机 DevEco 回退。程序会查找系统常见安装目录
和 `D:\Program Files\Huawei\DevEco Studio`；如果安装在其他位置，设置环境变量：

- Windows: `D:\Program Files\Huawei\DevEco Studio`
- macOS: `/Applications/DevEco-Studio.app/Contents`

若安装在其他位置，设置环境变量：

```bash
# macOS / Linux shell
export DEVECO_HOME="/Applications/DevEco-Studio.app/Contents"

# Windows CMD
set DEVECO_HOME=E:\DevEco Studio

# Windows PowerShell
$env:DEVECO_HOME = "E:\DevEco Studio"
```

调试密钥库默认使用兼容 DevEco 调试流程的固定密码。如需覆盖，请设置：

```bat
:: Windows CMD
set HAPSIGN_KEYSTORE_PASSWORD=使用你自己的强密码
```

```powershell
# Windows PowerShell
$env:HAPSIGN_KEYSTORE_PASSWORD = "使用你自己的强密码"
```

```bash
# macOS / Linux shell
export HAPSIGN_KEYSTORE_PASSWORD="使用你自己的强密码"
```

### 配置 Python 路径（Windows bat 脚本用）

`sign_install.bat` 默认使用系统 PATH 中的 `python`。如果使用 conda/venv，设置环境变量：

```bat
:: Windows CMD
set HAPSIGN_PYTHON=C:\path\to\your\python.exe

:: PowerShell
$env:HAPSIGN_PYTHON = "C:\path\to\your\python.exe"
```

拖拽脚本还需要明确的 HDC 目标序列号，可设 `HAPSIGN_SERIAL`，也可把序列号作为
第二个参数传入。运行 `hapsign devices list` 可以查看候选设备。

## 使用

### 方式一：桌面应用（推荐）

安装 GUI 依赖后运行：

```bash
python -m pip install -e ".[gui]"
hapsign-app
```

桌面版支持点击选择或拖入 `.hap` 文件，也可以点击右侧文件卡片的“×”移除误选
文件。可随时点击“检测设备”确认 HDC 连接状态，并在后台完成登录、签名和安装。
进度条会按设备检测、账号授权、证书申请、签名和安装等实际阶段推进。执行期间
可以点击“取消”；关闭窗口时会询问是否中断，完成子进程和 HDC 清理后再退出，
取消后的同一窗口可以直接重新开始完整流程。
开始流程时还会自动执行一次设备可用性检查，未连接、未授权或同时连接多台设备
时不会继续。运行记录和错误会直接显示在窗口中。

标题栏的“设置”可选择登录浏览器、签名文件保存位置和日志级别，并能直接打开
签名目录或日志目录。诊断日志默认写到程序目录的 `logs/hapsign.log`，单个文件
最多 4 MiB，保留 3 份轮转备份。敏感诊断默认关闭；主动开启并选择 DEBUG 后，
日志可能包含 token、用户标识和完整 API 请求/响应，但始终不会记录密钥库密码。
“保留最新一个签名后的 HAP”默认开启：最终 HAP 固定写到程序目录的
`signed_haps/`，新文件成功生成后只清理 HapSign 清单记录的旧产物，不会删除
目录中未记录的用户 HAP；当前输入文件也会受到保护。关闭后程序使用临时文件安装，
任务结束即删除。

如果 HDC server 原本未运行，程序会在本次任务结束时关闭自己拉起的后台服务；
如果 DevEco Studio 或其他工具已经启动 HDC server，则会保留该既有服务。

### 方式二：bat 拖拽

先设置 `HAPSIGN_SERIAL`，再将 `.hap` 文件拖到 `sign_install.bat` 上；或从 CMD
显式传入 HAP 和设备序列号：

```bat
set HAPSIGN_SERIAL=5XQ0225613000233
sign_install.bat path\to\app-unsigned.hap 5XQ0225613000233
```

### 方式三：命令行（Windows / macOS）

```bash
hapsign devices list --connected-only --json
hapsign auth status --json
hapsign auth --json
hapsign sign --hap path/to/app-unsigned.hap --serial <serial> --json
hapsign install --hap path/to/app-signed.hap --serial <serial> --json
hapsign deploy --hap path/to/app-unsigned.hap --serial <serial> --json
```

`sign` 只签名并返回签名 HAP 的绝对路径；`install` 只接受已有 Hap Signing Block
的 HAP；`deploy` 端到端签名并安装，输入已经签名时会直接安装。包名默认从 HAP
里的 `module.json` 提取。源码目录中可用 `python3 main.py <command> ...`。

所有执行命令都支持 `--json`。此模式下 stdout 只输出单行 JSON，日志写到 stderr，
且不会输出 Token、密码或 UDID。Agent 应先从 `devices list` 中选择
`connected=true` 的目标，优先选择 `physical_candidate=true` 的 USB 真机，再把其
`serial` 原样传给后续命令。`serial` 是 HDC 连接标识，不是签名 Profile 中的 UDID。

`auth` 可以单独调用并持久化当天 Token。同一份 Token 缓存不绑定目标设备，在同一
台运行 HapSign 的电脑上可继续给不同 HarmonyOS 手机、平板或 PC 目标签名；每台
目标设备的 Profile 仍绑定自己的 UDID，切换设备会重新申请 Profile。Token 不会在
多台运行 HapSign 的电脑之间自动同步，也不建议手工复制缓存。`auth status` 只检查
本地当日缓存，因此 JSON 中 `online_verified` 固定为 `false`。

### 构建便携版

便携版是一个可直接解压运行的目录，不要求目标电脑安装 Python 或 DevEco Studio。
Windows 正式包使用锁定并校验的 OpenHarmony 6.1 公共工具链和 Eclipse Temurin
21。首次构建先准备工具链，再打包：

```bash
python -m pip install -e ".[gui,bundle]"
python scripts/prepare_toolchain.py
python scripts/build_portable.py
```

`prepare_toolchain.py` 会校验 `toolchain.lock.json` 中的大小和 SHA-256，只从
OpenHarmony 公共 SDK 提取 HDC、libusb、hap-sign-tool 和 NOTICE，再用 Temurin
JDK 的 `jlink` 生成精简 Java 运行时。公共 SDK 下载约 2.5 GB，但只在构建缓存中
保留；最终工具链约 66 MiB。已有下载可用 `--sdk-archive` 和 `--jdk-archive`
传入，仍会执行相同校验。

默认产物复用系统 Edge/Chrome，不包含 Chromium。要生成包含内置 Chromium 的兼容
包，PowerShell 中执行：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
python -m playwright install --no-shell chromium
python scripts/build_portable.py --keep-bundled-browser
```

输出文件为 `dist/HapSign-portable-<platform>.zip`。Windows、macOS 和 Linux
产物需要在各自平台分别构建。便携版使用说明见 [PORTABLE.md](PORTABLE.md)，
完整构建步骤见 [docs/PACKAGING.md](docs/PACKAGING.md)。

> [!NOTE]
> 当前锁定的 OpenHarmony 公共工具链是 `6.1.0.31 / API 23`（HDC 3.2.0c），
> 并非 DevEco 6.1.1.125 中较新的 API 24 版本。现有签名产物验证、实际重签名和
> 设备识别已经通过；正式发布仍应按
> [开源发布门禁](docs/OPEN_SOURCE_RELEASE.md) 完成真实设备安装回归。
> `--allow-deveco-toolchain` 只用于排障回退，其产物不得冒充锁定的公开构建。

### Agent CLI 接口

```
hapsign auth [login|status] [--refresh] [--state-dir DIR] [--json]
hapsign devices [list] [--connected-only] [--json]
hapsign sign --hap HAP --serial SERIAL [签名选项] [--json]
hapsign install --hap SIGNED_HAP --serial SERIAL [--bundle-name NAME] [--json]
hapsign deploy --hap HAP --serial SERIAL [签名选项] [--json]

sign / deploy 签名选项:
  --bundle-name NAME   覆盖 HAP 中的包名
  --country CODE       华为账号国家码，默认 CN
  --device-type TYPE   签名平台注册的设备类型码，默认 4
  --state-dir DIR      Token 与默认签名材料根目录，默认 ~/.hapsign
  --work-dir DIR       当前 bundle 签名材料目录，默认 <state-dir>/<bundle>
  --output-dir DIR     签名 HAP 输出目录，默认与 work-dir 相同
  --browser MODE       system、system_controlled 或 playwright
  --enable-capability  使用 Real Profile（APL=system_basic）
  --refresh-token      强制浏览器认证，同时刷新签名材料
  --refresh-signing    只重新申请证书/Profile，复用有效 Token
  -v, --verbose        将 DEBUG 日志写到 stderr

设备类型码:
  4  手机/平板/2in1（默认）
  2  穿戴设备
  8  智慧屏
  9  路由器
  1  轻量级穿戴设备

退出码:
  0    命令成功
  1    认证、签名、HDC 或安装运行失败
  2    参数或输入 HAP 无效
  130  用户取消
```

成功 JSON 至少包含 `ok=true` 和 `command`。`sign` / `deploy` 还包含 `input_hap`、
`signed_hap`、`bundle_name`、`serial`、`input_signed` 和 `installed`；`devices list`
包含 `count`、`connected_count` 与 `targets`。失败 JSON 使用
`{"ok":false,"command":"...","error":{"type":"...","message":"..."}}`。
完整、随版本同步的帮助以 `hapsign --help` 和各子命令 `--help` 为准。

### 首次运行

会弹出浏览器窗口，打开华为登录页。手动输入账号密码登录，如果有验证码或二次验证也手动处理。登录成功后浏览器会自动关闭，后续自动完成签名和安装。

### Real Profile（system_basic 权限）

默认创建的 Test Profile APL 为 `normal`。大多数需要 ACL 预授权的权限（如 `CUSTOM_SANDBOX`、`READ_WRITE_USER_FILE` 等）在 Test Profile 下即可授予。

如果应用需要 `system_basic` 级别的 APL，加 `--enable-capability` 参数走 Real Provision 路径：

```bash
hapsign deploy --hap app.hap --serial <serial> --enable-capability
```

此模式通过 `add.real.provision` API 创建 Real Profile（provisionType=1），对应 DevEco Studio 6.1+ 的 `enableCapability` 路径。需要应用已在 AGC（AppGallery Connect）注册且当前账号有访问权限，否则自动回退到 Test Profile。

### 签名文件和缓存位置

桌面版和便携版默认把签名材料保存在程序目录旁，解压目录可以整体移动：

```text
HapSign/
├── HapSign.exe
├── signing_files/
    ├── .token_cache.json
    └── <bundle_name>/
└── signed_haps/                 # 最新一个签名 HAP（可在设置中关闭）
```

源码 CLI 默认保存在用户主目录的 `~/.hapsign/<bundle_name>/`；Windows 对应
`%USERPROFILE%\.hapsign\<bundle_name>\`。该默认值不依赖启动命令时的工作目录。
传入 `--state-dir`、`--work-dir` 和 `--output-dir` 可以分别指定 Token/默认材料
根目录、当前 bundle 材料目录和签名 HAP 输出目录。
程序目录必须可写，不建议把便携版解压到 `Program Files` 等受保护目录。
桌面版“设置”中还可以改为用户 `AppData Local` 或任意自定义目录。

成功完成一次未签名 HAP 的签名后，目录内容如下：

```
<签名目录>/com.example.myapp/
├── auto_debug_com.example.myapp.p12   # 密钥库
├── auto_debug_com.example.myapp.csr   # CSR
├── auto_debug_com.example.myapp.cer   # 调试证书
├── auto_debug_com.example.myapp.p7b   # 调试 Profile
├── metadata.json                        # 缓存元数据
└── entry-default-unsigned_signed.hap   # 签名后的 hap
```

如果输入 HAP 本身已经签名，程序会直接安装原文件，不会生成上述签名材料。

## 缓存策略

同一天内不会重复登录；签名文件只在 bundle 和目标 UDID 都相同时复用：

- **Token 缓存**：`~/.hapsign/.token_cache.json`，当天可跨目标设备复用
- **签名文件缓存**：`~/.hapsign/{bundle_name}/metadata.json`，当天仅为匹配的
  bundle 和设备 UDID 复用
- 跨天自动失效，重新走完整流程
- Token 失效时自动刷新，刷新失败才回退到重新登录

Windows 使用当前用户 DPAPI 加密 Token；macOS/Linux 以权限 `0600` 的明文保存。
签名材料与缓存都不要上传、分享或放入云同步目录。共享电脑使用完毕后应删除
`~/.hapsign/`。详细说明见 [SECURITY.md](SECURITY.md)。

## 限制

- 登录验证码 / 二次验证需要用户在浏览器中手动处理
- 拖拽安装脚本仅支持 Windows（`sign_install.bat`）；macOS 请使用 `hapsign` 命令行
- 当前提供并完整验证的是 Windows 便携版；macOS/Linux 已预留运行时路径，
  仍需分别增加锁定的公开工具包并在对应系统构建、实机验证
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
├── gui.py                # PySide6 桌面界面
├── models.py             # 数据模型
├── pipeline.py           # 全流程编排（缓存、登录、签名、安装）
├── runtime.py            # 用户数据目录与跨平台工具链发现
├── login/
│   └── browser_login.py  # 受控系统浏览器、内置 Chromium 与普通浏览器备用后端
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
    ├── hap_inspect.py    # 检测 HAP 是否已签名
    ├── hap_signer.py     # hap-sign-tool 签名 hap
    └── installer.py     # hdc install / 获取 UDID
```

## License

HapSign 自身源代码使用 [MIT License](LICENSE)。第三方依赖和便携包工具链保持各自
许可，不因本项目采用 MIT 而改变，详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。隐私与本地数据说明见
[PRIVACY.md](PRIVACY.md)，参与项目需遵守
[Code of Conduct](CODE_OF_CONDUCT.md)。

Powered by [BitFun](https://github.com/GCWing/BitFun)
