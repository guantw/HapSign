# HapSign 便携版

解压 ZIP 后双击 `HapSign.exe`（macOS/Linux 使用对应平台可执行文件）。

## 使用方法

1. 用 USB 连接 HarmonyOS 设备，并确认设备已允许调试。
2. 可点击“检测设备”确认设备已连接并授权；也可以直接开始，程序会自动检测。
3. 将 `.hap` 文件拖入窗口，或点击选择文件；误选时点击文件右侧的“×”移除。
4. 点击“开始签名并安装”。
5. 如果 HAP 尚未签名，程序会控制系统 Edge（其次 Chrome）打开华为登录页；
   完成登录后程序会继续。

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
精简包要求目标 Windows 已安装 Edge 或 Chrome。正式 Windows 构建先准备锁定的
OpenHarmony/Temurin 工具链：

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

构建结果位于 `dist/HapSign-portable-<platform>.zip`，同目录会生成可用于发布校验的
`.zip.sha256` 文件。
兼容包位于 `dist/HapSign-portable-<platform>-compat.zip`。
准备脚本使用 `jlink` 生成精简 Temurin 运行时，构建脚本会自动执行 Java、
keytool、hap-sign-tool、HDC 和冻结程序自检。

仅调试 GUI、不复制外部工具链时可以运行：

```bash
python scripts/build_portable.py --skip-toolchain
```

该 GUI-only 包不能在没有外部工具链的电脑上完成签名和安装。完整的构建环境、
资源发现顺序、目录结构、验证方法和发布清单见 `docs/PACKAGING.md`；生成的便携
目录中也会包含一份 `BUILDING.md`。

PyInstaller 产物与当前操作系统绑定，因此 Windows、macOS、Linux 需要分别构建。
锁文件会记录公共 SDK、Temurin 和核心文件哈希；发布包也包含生成时的
`PROVENANCE.txt`、完整 OpenHarmony NOTICE、Temurin legal 目录，以及
`libusb_shared.dll` 对应的 OpenHarmony 源码快照。若使用
`--allow-deveco-toolchain` 回退，本次产物只用于本机排障，不得公开发布。

发布包根目录会包含 HapSign 的 `LICENSE`、`PRIVACY.md`、
`THIRD_PARTY_NOTICES.md` 和 `BUILDING.md`，冻结依赖随附的许可文件位于
`licenses/python/`。Temurin legal、OpenHarmony NOTICE 和 libusb 对应源码也必须
保留。
