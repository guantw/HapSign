# Changelog

本项目的显著变更记录在此文件中，格式参考 Keep a Changelog，版本遵循语义化版本。

## Unreleased

### Breaking changes

- `HAPSIGN-BREAKING-001`：旧签名缓存缺少或切换能力模式、包名不匹配或设备 UDID
  无效时会执行一次性材料刷新；能力模式可通过一致使用 `--enable-capability` 匹配，
  只有其余一致性校验通过时，才可显式运行
  `migrate-cache --profile-type normal|system-basic` 保留缓存。
- `HAPSIGN-BREAKING-002`：CLI 默认浏览器从普通系统 Profile 改为自适应 `auto`；
  桌面会话仍优先使用隔离的受控浏览器，SSH、CI 或无桌面 Linux 会话改为外部浏览器交接。
  可用 `--browser` 或 `HAPSIGN_BROWSER` 配置。
- `HAPSIGN-BREAKING-003`：CLI 默认状态/产物从 PR #5 的 `~/.hapsign`（更早版本为
  进程工作目录）改到应用目录；可用 `--state-dir`、`--output-dir`、
  `HAPSIGN_SIGNING_DIR` 和 `HAPSIGN_SIGNED_HAPS_DIR` 恢复原路径；`inspect` 会检测
  可复用的 PR #5 旧状态，并在发现旧签名材料时阻止 agent 静默刷新材料。
- `HAPSIGN-BREAKING-004`：面向人的 CLI 输出不再作为脚本协议；自动化应使用 `--json`。
- `HAPSIGN-BREAKING-005`：CLI 改为显式子命令接口；旧的扁平参数调用需要按迁移表整改。
  完整影响、检测方式与整改命令见 [迁移指南](docs/MIGRATIONS.md)。

### Added

- CLI 新增 `auto`/`external` 浏览器模式、可配置 loopback 回调端口和认证超时；SSH、
  CI 或无桌面 Linux 会话会输出安全端口转发指引，`--events json` 提供结构化中间事件且不改变
  最终单行 JSON stdout 协议。
- 新增面向 Agent 的 `auth`、`devices list`、`sign`、`install`、`deploy` CLI
  子命令；支持单行 JSON stdout、stderr 日志、明确退出码与输入校验。
- CLI 支持显式 HDC `--serial`、真机/模拟器候选标记、签名与安装分离，以及安装后
  `bm dump` 校验；Token 可跨目标设备复用，Profile 缓存按 UDID 隔离。
- Linux 源码命令行、CI、DevEco/JAVA_HOME/PATH 工具发现，以及锁定并校验的
  OpenHarmony 6.1 + Temurin 21 Linux x64 便携工具链准备流程。
- Agent 友好的 `doctor`、`inspect`、`migrate-cache`、`--json`、显式 UDID 和
  精确输出路径；仅签名可复用缓存 Profile 而不连接设备，便携包同时提供独立的
  `hapsign-cli[.exe]` 控制台程序。
- 机器可读的兼容性变更目录、按 HAP 检测的迁移警告，以及带备份的旧缓存显式迁移命令。
- 仓库级 `.agents/skills/hapsign-signing` 全平台 Codex 技能，包含受控浏览器授权、
  能力判定、签名后复检、输出保护和脱敏回调诊断流程。
- Linux HDC server 归属探测在缺少 `lsof` 时回退到 `/proc`，进程启动时间读取不受
  `ps` 输出语言影响。
- macOS 支持：按平台解析 DevEco JBR / hap-sign-tool / hdc 路径，可用 `hapsign` 命令行签名安装。
- 可安装的 `hapsign` 命令和标准 Python 项目元数据。
- Ruff、pytest、覆盖率、pre-commit 和 Windows CI 配置。
- 贡献指南、安全策略、行为准则和统一编辑器配置。
- CLI、缓存、HTTP 响应和权限提取的单元测试。
- 自动检测已签名 HAP：存在 Hap Signing Block 时跳过登录/申请/签名，直接安装。
- 签名块检测对齐 `developtools_hapsigner`（两阶段 EOCD、ZIP64 Locator、blockCount/尺寸边界）。
- PySide6 桌面界面，支持文件选择、HAP 拖放、后台执行和运行记录。
- 桌面版增加主动设备检测，并在签名安装前强制确认 HDC 设备可用。
- 运行记录使用统一的轻量滚动条样式，文件卡片支持移除误选 HAP。
- Windows 便携版声明 Per-Monitor V2 DPI 感知，并使用系统字体和小数缩放。
- 桌面版签名材料改为保存在程序目录的 `signing_files/`，便于随目录迁移。
- Windows 下 Java、keytool 和 HDC 使用无控制台窗口方式启动，消除闪窗。
- HDC server 采用“本次启动、本次关闭”策略，避免任务结束后遗留后台进程，
  同时不终止 DevEco 等工具已有的 HDC 服务。
- PyInstaller 便携版构建配置，以及跨平台用户数据和工具链发现。
- 恢复 Playwright 受控浏览器作为默认登录环境，系统默认浏览器保留为备用模式。
- 新增 Playwright 受控系统 Edge/Chrome 模式并作为精简包默认值；兼容包仍可
  携带内置 Chromium，普通系统默认浏览器只作为非受控备用。
- 桌面任务使用按实际阶段推进的百分比进度条，并支持主动取消。
- 执行中的登录、网络请求、Java/keytool、签名工具和 HDC 均响应取消信号；
  关闭窗口时可确认中断，清理完成后自动退出。
- 新增程序目录 JSON 配置、滚动文件日志、日志级别和敏感诊断开关。
- 桌面设置可在程序目录、用户 AppData Local 和自定义签名目录之间切换，并可
  一键打开签名目录或日志目录。
- 可选择是否保留签名后的 HAP；默认在程序目录 `signed_haps/` 中仅保留最新
  一个，新文件发布成功后才删除旧文件，关闭后使用并清理任务临时文件。
- 便携构建采用带回退开关的保守精简：只排除已知无关的 JCEF/录像组件，并在
  裁剪后强制执行 Chromium、Java、keytool 与 hap-sign-tool 自检。
- 设置页改为分区卡片布局，统一重绘下拉框、弹出菜单、复选框及操作按钮。
- Windows 字体改用整数逻辑像素、Microsoft YaHei UI 和完整 hinting，降低
  150%/200% 高 DPI 下由分数物理像素造成的笔画发虚。
- 补充隐私说明、第三方组件声明和开源发布门禁；便携包构建会复制项目许可、
  冻结依赖随附许可，并为本机复制的工具链生成 SHA-256 来源清单。
- 明确区分 MIT 源码发布与第三方二进制再分发：未确认具体 DevEco/SDK 版本许可前，
  完整便携 ZIP 只用于本地构建验证。
- 正式 Windows 便携构建改用 `toolchain.lock.json` 锁定并校验的 OpenHarmony
  6.1 公共 SDK 与 Eclipse Temurin 21；构建时仅提取 HDC、hap-sign-tool、
  libusb 和 NOTICE，并以 `jlink` 生成精简 Java 运行时，同时随包保留来源、
  哈希、许可材料及 libusb 对应源码。DevEco 工具链只保留为显式排障回退。
- 便携构建在 ZIP 旁自动生成标准 `.sha256` 校验文件，降低发布时手工抄录哈希出错的风险。

### Changed

- Token 缓存不再按创建日期主动失效；签名或 Profile 申请实际被服务端以 Token 失效
  拒绝时才尝试刷新，跨天申请签名材料也会先复用现有 Token。
- CLI 现在必须显式使用 `doctor`、`inspect`、`migrate-cache`、`auth`、`devices`、
  `sign`、`install` 或 `deploy` 子命令。`deploy`/`install` 必须传入非空
  `--serial`；`sign` 可使用 `--serial`、可信的显式 `--device-udid`，或复用兼容的
  缓存材料。旧的 `hapsign --hap ...` 调用方式不再兼容。

### Fixed

- Agent CLI 会拒绝空白 HDC serial，避免退回隐式设备选择；`auth` 仅在 Token
  缓存成功落盘后返回成功；`devices list` 不再把退出码为 0 的 HDC `[Fail]`
  输出误报为空设备列表。
- CLI 不再把缺少 HDC 可执行文件归类为输入错误；`devices`、`install` 等运行时
  HDC 失败现在返回 `operation_failed` 和退出码 1。
- CLI 默认使用隔离的 `system_controlled` 浏览器，并把签名缓存与默认输出绑定到应用
  目录/配置，避免复用普通浏览器 Profile 或因调用工作目录不同而产生多份缓存。
- 修复 CLI 初始化失败时把 `sign-install` 误报成 `sign`；基础安装现在声明默认受控
  浏览器所需的 Playwright 运行时依赖；JSON 协议使用 ASCII 转义，避免 Windows
  本地代码页导致 agent 无法解码中文错误。
- 签名材料缓存会校验包名、能力模式、已知设备 UDID 和材料文件类型，避免跨设备或
  跨 Profile 模式误用；显式输出的默认不覆盖门禁也覆盖并发发布竞争。
- 缓存分别记录请求与实际能力模式，Real Profile 回退到 Test Profile 后可稳定复用；
  CLI JSON 会显式返回实际模式及 `capability_fallback`，避免 agent 误报 system_basic。
- 输入已经签名时，`--output` 仍会按不覆盖策略原子发布到指定路径，不再静默忽略。
- HTTP 客户端正确发送 `User-Agent` / `Accept-Language` 请求头。
- Token 缓存缺少 `jwt_token` 时不再复用，避免后续刷新失败。
- 设备注册将业务层重复错误码视为成功，并保留 HTTP 错误信息中的兼容判定。
- 设备注册兼容服务端新增的 `205389858 (UDID is repeat)`，复用已注册设备，
  不再把“设备已存在”当作安装失败。
- 签名工具默认密码统一引用 `HAPSIGN_KEYSTORE_PASSWORD` / 配置默认值。
- 登录回调兼容根路径、`/callback` 及其他本地路径上的 GET/POST 回调，
  并支持普通表单、multipart 表单、JSON、查询参数和 CORS 预检，修复授权后
  一直等待的问题；用户拒绝授权时也会立即结束等待。
- 登录回调支持 Chromium Private Network Access 预检、缺失 multipart 类型推断
  及嵌套 JSON；运行记录会显示不含 token 的回调方法、类型和字段诊断信息。
- 已选 HAP 卡片固定显示在拖放区右侧，避免窗口布局变化时覆盖拖放区。
- 主动取消任务后将进度条重置为 0%。
- 登录成功响应恢复为已验证实现使用的 DevEco 成功页跳转，并为受控 Chromium
  预授予本地网络访问权限，避免授权完成后回调请求被浏览器策略拦截。

### Security

- CLI、Pipeline 和诊断日志会脱敏异常文本中的完整 64 位设备 UDID；失败 JSON
  stdout 不再泄露 `DeviceAPI.find_device_id()` 等异常携带的设备标识。
- 登录回调服务仅监听 loopback 地址。
- 日志默认不包含 token、完整请求体、CSRF code 或完整登录 URL；只有用户主动开启
  “敏感诊断”且使用 DEBUG 级别时才记录完整网络载荷，密钥库密码始终排除。
- 限制登录回调请求体大小，并尽力收紧 token 缓存文件权限。
