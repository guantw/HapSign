# HapSign 便携版打包指南

HapSign 使用 PyInstaller 的 `onedir` 模式生成桌面程序目录，再把当前平台的
Java、keytool、`hap-sign-tool.jar` 和 HDC 一并复制后压缩成 ZIP。目标电脑解压
即可运行，不要求预装 Python 或 DevEco Studio。

## 1. 构建模型

- Windows、macOS、Linux 产物必须分别在对应操作系统构建，不能交叉生成。
- 当前经过完整构建和启动验证的是 64 位 Windows 版本。
- 仓库不保存大型 SDK/JDK 二进制；`toolchain.lock.json` 固定公共上游地址、版本、
  大小和 SHA-256，准备结果放在被 Git 忽略的 `build/`。
- 默认由 Playwright 控制系统 Edge/Chrome，因此继续拥有本地网络权限和回调控制，
  但不携带 Chromium；可另外生成包含内置 Chromium 的兼容包。
- PyInstaller 使用 `onedir` 而不是单文件模式，启动更快，也便于检查和替换
  Java/HDC 等资源。

## 2. 构建机要求

完整便携包需要：

1. Python 3.11 或更高版本。
2. Windows 正式构建需要网络下载锁定的 OpenHarmony 公共 SDK（约 2.5 GB）和
   Temurin JDK（约 205 MB），或由构建者提供已下载的相同文件。
3. 首次准备建议至少 6 GB 可用磁盘空间；工具链生成后可删除研究/下载副本，
   `build/toolchain-prepared/windows` 约 66 MiB。

DevEco Studio 不是正式便携构建的依赖。它仍可用于源码运行和兼容排障；只有显式
传入 `--allow-deveco-toolchain` 时，构建脚本才会从本机发现并复制它的工具。

## 3. 安装构建依赖

建议使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[gui,bundle]"
```

开发检查还需要：

```powershell
python -m pip install -e ".[dev]"
```

## 4. 准备公开工具链

在仓库根目录执行：

```powershell
python scripts/prepare_toolchain.py
```

准备脚本会：

1. 校验 `toolchain.lock.json`；
2. 下载并校验 OpenHarmony 6.1 公共 SDK 和 Eclipse Temurin 21；
3. 只提取 Windows HDC、配套 libusb、hap-sign-tool 及完整 NOTICE；
4. 用 `jlink` 生成只含签名器和 keytool 所需模块的 Java runtime；
5. 附带 libusb 1.0.28 对应的 OpenHarmony 源码快照；
6. 实际运行 Java、EC keytool、hap-sign-tool 和 HDC 版本自检。

已有官方归档时可以避免重复下载；文件仍会严格按锁文件验证：

```powershell
python scripts/prepare_toolchain.py `
  --sdk-archive D:\cache\ohos-sdk-windows_linux-public.tar.gz `
  --jdk-archive D:\cache\OpenJDK21U-jdk_x64_windows_hotspot_21.0.12_8.zip
```

默认输出是 `build/toolchain-prepared/windows/`。要重建现有输出，显式添加
`--force`。

## 5. 生成完整便携包

在仓库根目录执行：

```powershell
python scripts/build_portable.py
```

脚本会依次：

1. 检查构建机系统 Edge/Chrome 可由 Playwright 正常控制。
2. 使用 `bundle/hapsign.spec` 构建 `dist/HapSign/`；默认不收集 Chromium。
3. 复制已校验的公开工具链及其许可、来源和对应源码。
4. 复制便携版使用说明和本打包指南。
5. 运行冻结应用自检，并清除自检生成的日志、配置和运行数据。
6. 生成 `dist/HapSign-portable-<platform>.zip`。

Windows 正式构建使用 Temurin `jlink` runtime 并复用系统 Edge/Chrome。每次构建
都会实际启动冻结版受控系统浏览器，并运行 Java、hap-sign-tool 和 keytool 密钥
生成自检；任一失败便终止构建。

仅在排查新版 DevEco 兼容性时可使用本机回退：

```powershell
python scripts/build_portable.py --allow-deveco-toolchain
python scripts/build_portable.py --allow-deveco-toolchain --keep-full-jbr
```

这两种产物都不是锁定的公开构建，不得直接上传 Release。

生成包含内置 Chromium 的兼容包：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
python -m playwright install --no-shell chromium
python scripts/build_portable.py --keep-bundled-browser
```

Windows 的主要输出结构：

```text
dist/
├── HapSign/
│   ├── HapSign.exe
│   ├── _internal/                         # Python、Qt 和应用依赖
│   │   └── playwright/                     # 浏览器控制驱动
│   ├── resources/toolchain/windows/
│   │   ├── runtime/                       # Temurin jlink runtime
│   │   ├── lib/hap-sign-tool.jar
│   │   ├── bin/hdc.exe
│   │   ├── NOTICE.txt
│   │   ├── PROVENANCE.txt
│   │   ├── toolchain.lock.json
│   │   └── licenses/libusb-source/
│   ├── README.md
│   └── BUILDING.md
└── HapSign-portable-windows.zip
```

## 6. GUI-only 快速构建

仅验证界面和 PyInstaller 配置时可以跳过约数百 MiB 的工具链复制：

```powershell
python scripts/build_portable.py --skip-toolchain
```

GUI-only 包仍能启动，但目标电脑若没有可发现的外部工具链，就不能检测设备、
签名或安装。不要把它当作正式便携版发布。

## 7. 构建后验证

先运行自动检查：

```powershell
python -m ruff check hapsign tests scripts
python -m pytest --cov
```

验证窗口程序能够启动并自动退出：

```powershell
.\dist\HapSign\HapSign.exe --smoke-test
.\dist\HapSign\HapSign.exe --system-browser-smoke-test
```

兼容包还应执行 `HapSign.exe --browser-smoke-test`。

验证内置工具：

```powershell
.\dist\HapSign\resources\toolchain\windows\runtime\bin\java.exe -version
.\dist\HapSign\resources\toolchain\windows\runtime\bin\keytool.exe -help
.\dist\HapSign\resources\toolchain\windows\bin\hdc.exe -v
```

构建脚本会在 ZIP 旁自动生成标准 SHA-256 sidecar，例如
`HapSign-portable-windows.zip.sha256`。仍可手动复核：

```powershell
Get-FileHash -Algorithm SHA256 .\dist\HapSign-portable-windows.zip
```

GUI 改动还应至少人工检查一次：

- 100%、150%、200% 缩放下文字和控件没有位图模糊。
- 拖入、重新选择和移除 HAP 正常。
- 选择 HAP 后文件卡片始终位于拖放区右侧，不会覆盖拖放区。
- 受控系统 Edge/Chrome 能打开登录页，授权回调后程序继续并跳转成功页。
- 进度条按流程阶段递增，不显示无限循环动画。
- 登录等待、签名或安装过程中点击“取消”能回到可重试状态。
- 任务运行时关闭窗口会询问是否中断；确认后清理并正常退出。
- 日志滚动条、长文件名和错误提示显示正常。
- “检测设备”及安装前自动检查都能识别未授权、无设备和正常设备。
- Java、keytool、HDC 执行期间没有命令行窗口闪现。
- 无预先运行的 HDC server 时，任务结束后不残留 `hdc` 进程。
- DevEco 已经运行 HDC server 时，任务结束后该既有进程仍然保留。

## 8. 签名文件位置

桌面/便携版默认把运行数据放在可执行文件旁：

```text
HapSign/
├── HapSign.exe
├── signing_files/
    ├── .token_cache.json
    └── <bundle_name>/
└── signed_haps/                 # 最新一个签名 HAP，与材料目录设置无关
```

因此便携目录必须可写。源码 GUI 默认使用项目根目录，源码 CLI 默认使用当前工作
目录下的 `signing_files/<bundle_name>/`。可以用 `HAPSIGN_DATA_DIR` 覆盖桌面版
数据根目录，CLI 则可使用 `--work-dir`。已签名 HAP 会跳过签名流程，因此不会
产生新的 `.p12`、`.cer`、`.p7b` 或签名后 HAP。

GUI 设置也可选择用户 `AppData Local` 或自定义签名目录。程序目录下的
`hapsign-config.json` 保存这些设置，`logs/hapsign.log` 保存滚动诊断日志；
程序目录不可写时日志回退到用户本地数据目录。敏感诊断默认关闭，开启后配合
DEBUG 级别可记录 token、用户标识和完整 API 请求/响应，但密钥库密码永不记录。
最终签名 HAP 固定使用程序目录的 `signed_haps/`，默认只保留最新一个 HapSign
清单记录的产物，不会清理未记录的用户 HAP，当前输入文件也会受到保护；设置关闭
保留后，任务使用系统临时目录并在结束时清理。

## 9. 跨平台注意事项

- `runtime.py` 已集中处理平台目录和可执行文件名；`toolchain.lock.json` 当前只
  锁定并实测 Windows x64。
- 新平台应增加独立锁定项、对应平台 Temurin 归档、OpenHarmony 工具包与真实设备
  测试，不能复用 Windows 二进制。
- macOS 发布通常还需要应用包、代码签名和 notarization；当前脚本只生成目录
  和 ZIP。
- Linux 需要在目标发行版或兼容的较旧发行版构建，并验证 Qt、USB 权限和 HDC。
- 不要把一个平台的 Java runtime 或 HDC 复制进另一个平台的产物。

## 10. 发布前清单

- 工作区不包含 token、`.p12`、`.cer`、`.p7b`、UDID 或用户 HAP。
- 发布 ZIP 不包含构建自检生成的 `logs/`、配置或运行数据目录。
- 包根目录包含 `LICENSE`、`PRIVACY.md`、`THIRD_PARTY_NOTICES.md` 和
  `BUILDING.md`，`licenses/python/` 中存在冻结依赖的随附许可。
- 工具链目录包含 `PROVENANCE.txt`、锁文件、OpenHarmony NOTICE、Temurin legal
  和 `licenses/libusb-source/`。
- 全部测试和 Ruff 检查通过。
- 正式包不是 `--skip-toolchain` 产物。
- EXE 冒烟、Java、keytool 和 HDC 版本命令通过。
- 至少用一台真实设备验证检测、签名和安装。
- 确认 Temurin、HDC、签名工具和 libusb 的再分发条件。
- 按 `docs/OPEN_SOURCE_RELEASE.md` 记录第三方组件的准确版本、来源和 SHA-256；
- 正式包不是 `--allow-deveco-toolchain` 回退产物。
- 记录 ZIP 大小和 SHA-256。

## 11. 常见问题

### 提示工具链缺失

先运行 `python scripts/prepare_toolchain.py`。若已有下载，使用
`--sdk-archive`/`--jdk-archive` 指向文件；校验失败时不要绕过锁文件。

### 包很大

主要空间来自 Playwright 浏览器控制驱动、Qt 和 Python 冻结运行时。不要直接删除
DLL；应使用
经过验证的精简方案，并重新执行完整登录、签名和安装测试。

替换前的 Windows 精简构建未压缩目录约 390 MiB、ZIP 约 187 MiB。公开工具链
将 Java 从约 170 MiB JBR 换成约 48 MiB Temurin runtime，完整工具链约 66 MiB；
最终 ZIP 大小以本次构建结果为准。替换后的主要组成大致为：

- Playwright 浏览器控制驱动：约 103 MiB
- PySide6/Qt：约 73 MiB
- Temurin `jlink` runtime：约 48 MiB
- hap-sign-tool：约 12 MiB
- HDC 及配套文件：约 5.5 MiB
- OpenHarmony NOTICE、libusb 对应源码及来源记录：约 1 MiB
- Python 运行时、requests 和应用代码等其余部分：约 28 MiB

这些数字会随 Playwright、Qt、Temurin 和 OpenHarmony 版本变化。精简包通过复用
系统 Edge/Chrome，兼容包则额外携带 Chromium。两种模式都由
Playwright 控制浏览器并授予本地回调权限，非受控系统默认浏览器仅作为备用。

### 4K 屏幕字体模糊

Windows 包包含 Per-Monitor V2 manifest，GUI 也在创建 `QApplication` 前启用
Qt 高 DPI 小数缩放。界面字体使用整数逻辑像素、Microsoft YaHei UI 和完整
hinting，避免 200% 缩放下出现分数物理像素。若仍模糊，检查 `HapSign.exe`
兼容性设置中是否被手动启用了“替代高 DPI 缩放行为”，并关闭该覆盖。
