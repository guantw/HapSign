# HapSign 便携版

解压下载归档后双击 `HapSign.exe`（macOS/Linux 使用对应平台的 `HapSign`）。目录中
另有 `hapsign-cli.exe`/`hapsign-cli`，供脚本和 agent 使用机器可读签名协议。

## 使用方法

1. 用 USB 连接 HarmonyOS 设备，并确认设备已允许调试。
2. 可点击“检测设备”确认设备已连接并授权；也可以直接开始，程序会自动检测。
3. 将 `.hap` 文件拖入窗口，或点击选择文件；误选时点击文件右侧的“×”移除。
4. 点击“开始签名并安装”。
5. 如果 HAP 尚未签名，程序会控制系统 Edge（其次 Chrome）打开华为登录页；
   完成登录后程序会继续。

仅签名或自动化辅助可在终端运行：

```bash
./hapsign-cli doctor --json
./hapsign-cli sign --hap app.hap --output app-signed.hap \
  --browser auto --json
```

CLI 的 `auto` 模式在 SSH、CI 或无桌面 Linux 会话中会显示一次性登录地址和 callback
端口。保持命令运行，在有浏览器的电脑按提示建立同端口 SSH loopback 转发后打开该
地址；容器等运行时使用其等价的私有端口转发能力。
`--events json` 可输出带 `HAPSIGN_EVENT=` 前缀的结构化交接事件；最终 stdout JSON
协议不变。首次认证仍需要现代浏览器，CLI 不会收集账号密码或验证码。

> [!IMPORTANT]
> 首次认证以及缓存失效后的重新认证，不支持完全无浏览器的纯命令行闭环。无桌面
> Linux 可以运行 CLI，但必须能使用另一台有现代浏览器的电脑，并将同一 loopback
> 端口安全转发回 CLI。已有且仍有效的 Token 缓存可直接复用，无需每次打开浏览器。

本登录实现已在 Windows 11 专业版 64 位，以及 Ubuntu 24.04.4 LTS x86_64（WSL2）
完成源码 CLI 真实登录验证。验证覆盖用户可见登录页、loopback 回调、Token 换取和
平台缓存复用；不包含原生 Linux/远程 SSH、macOS、GUI、USB 或完整签名安装链路，
这些场景仍需分别回归。

Windows PowerShell 使用 `.\hapsign-cli.exe`。完整约定见 `AGENT_SIGNING.md`；升级
已有安装前请查看 `MIGRATIONS.md`，或读取 `doctor --json` 的机器可读变更目录。

进度条按当前实际流程阶段推进。任务执行期间可以点击“取消”；如果直接关闭窗口，
程序会询问是否中断当前任务。确认后会先结束登录等待或外部工具、清理本次启动的
HDC 服务，再退出。取消不会复用未完成任务的运行状态，可直接重新开始。

签名后的 HAP、证书、Profile、密钥库和登录令牌默认保存在程序目录下：

```text
HapSign/
├── HapSign.exe
├── signing_files/
    ├── .token_cache.json
    └── <bundle_name>/
└── signed_haps/                 # 最新一个签名 HAP
```

请把便携版解压到当前用户可写的目录，不要放进 `Program Files` 等受保护位置。
移动整个 `HapSign` 目录时，签名材料和缓存会一起移动。

标题栏的“设置”可把签名目录改为当前用户的 `AppData Local` 或自定义目录，也可
直接打开签名目录和日志目录。配置文件是程序目录下的 `hapsign-config.json`；
日志默认写入 `logs/hapsign.log`，无法写入程序目录时会回退到用户本地数据目录。
敏感日志默认关闭；只有主动开启且日志级别为 DEBUG 时才记录 token、用户标识及
完整 API 请求/响应。签名库密码始终不会写入日志。

“保留最新一个签名后的 HAP”默认开启。程序会在新 HAP 完整签名成功后写入
`signed_haps/`，只删除 HapSign 清单记录的旧产物，因此不会误删目录中的用户 HAP，
当前输入文件也不会被清理，签名失败也不会破坏上一份。关闭该开关后，签名 HAP 只
作为安装临时文件，任务结束后自动清理。

输入已签名 HAP 时会直接安装，不产生新的签名材料。
Java、keytool 和 HDC 等外部命令会在后台执行，不会弹出命令行窗口。
任务结束时只关闭由本次任务启动的 HDC server；原本由 DevEco 等工具启动的
既有 HDC server 不会被终止。

## 构建

构建机需要 Python 3.11+。目标电脑不需要安装 Python 或 DevEco Studio；默认
精简包要求目标系统已安装 Edge 或 Chrome。正式 Windows/Linux x64 构建先在目标
平台准备锁定的 OpenHarmony/Temurin 工具链：

```bash
python -m pip install -e ".[gui,bundle]"
python scripts/prepare_toolchain.py
python scripts/build_portable.py
```

要生成不依赖系统浏览器二进制的兼容包：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
python -m playwright install --no-shell chromium
python scripts/build_portable.py --keep-bundled-browser
```

Windows/macOS 构建结果位于 `dist/HapSign-portable-<platform>.zip`；Linux 使用
`dist/HapSign-portable-linux.tar.gz` 以保留可执行位。同目录会生成对应的
`.sha256` 校验文件。兼容包沿用相同平台格式，并在文件名中增加 `-compat`。
准备脚本使用 `jlink` 生成精简 Temurin 运行时，构建脚本会自动执行 Java、
keytool、hap-sign-tool、HDC 和冻结程序自检。

仅调试 GUI、不复制外部工具链时可以运行：

```bash
python scripts/build_portable.py --skip-toolchain
```

该无工具链 GUI/CLI 包不能在没有外部工具链的电脑上完成签名和安装。完整的构建环境、
资源发现顺序、目录结构、验证方法和发布清单见 `docs/PACKAGING.md`；生成的便携
目录中也会包含一份 `BUILDING.md`。

PyInstaller 产物与当前操作系统绑定，因此 Windows、macOS、Linux 需要分别构建。
锁文件当前包含 Windows x64 和 Linux x64 的公共 SDK、Temurin 和核心文件哈希；
发布包也包含生成时的
`PROVENANCE.txt`、完整 OpenHarmony NOTICE、Temurin legal 目录，以及
`libusb_shared.dll`/`libusb_shared.so` 对应的 OpenHarmony 源码快照。若使用
`--allow-deveco-toolchain` 回退，本次产物只用于本机排障，不得公开发布。

发布包根目录会包含 HapSign 的 `LICENSE`、`PRIVACY.md`、
`THIRD_PARTY_NOTICES.md` 和 `BUILDING.md`，冻结依赖随附的许可文件位于
`licenses/python/`。Temurin legal、OpenHarmony NOTICE 和 libusb 对应源码也必须
保留。
