# HapSign Agent 签名协议

本文描述 agent 如何在 Windows、Linux 和 macOS 上半自动完成 HAP 调试签名。账号
密码、验证码和二次验证始终由用户在浏览器中完成；agent 不需要也不应读取这些信息。
源码安装后的命令名是 `hapsign`；便携版使用 `hapsign-cli.exe`（Windows）或
`./hapsign-cli`（Linux/macOS），其余参数和结果完全相同。

## 认证规格边界与验证范围

首次认证以及缓存失效后的重新认证，不支持完全无浏览器的纯命令行闭环。Linux CLI
可以运行在 SSH、CI 或无桌面环境中，但用户必须能在另一台有现代浏览器的电脑上打开
一次性登录地址，并通过同端口 loopback 转发把回调安全送回 CLI。“Linux 主机不需要
桌面”不表示整个认证过程不需要浏览器。已有且仍被服务端接受的 Token 缓存可以直接
复用，此时本次调用不需要打开浏览器。

源码 CLI 的用户可见真实登录已在以下环境验证：

- Windows 11 专业版 64 位：`auto` 启动隔离 Edge，完成 loopback 回调、Token 换取、
  DPAPI 缓存和缓存复用
- Ubuntu 24.04.4 LTS x86_64（WSL2）：`auto` 调用 Windows 默认浏览器，完成跨 WSL
  loopback 回调、Token 换取、`0o600` 缓存和缓存复用

该结果不覆盖原生 Linux/远程 SSH、macOS、GUI、USB 或完整签名安装链路；未列入不
等同于不支持，但发布前应在目标环境执行相应回归。

## 稳定调用顺序

### 1. 诊断运行环境

```bash
hapsign doctor --json
```

标准输出是单个 JSON 文档。`capabilities.signing.ok` 表示 Java、keytool 和
hap-sign-tool 可用，`capabilities.device.ok` 表示 HDC 可用；顶层 `ok` 只有两组
都可用时才为 `true`。仅签名且已有缓存 Profile 或显式提供 UDID 时，可以不要求
device capability。

先读取 `paths` 和 `breaking_changes`。`paths.state_dir`、`paths.output_dir` 是本次
实际使用的绝对目录；兼容性变更用稳定 `id` 标识，整改方式见
[迁移指南](MIGRATIONS.md)。

工具发现顺序为：当前平台便携资源、`DEVECO_HOME`/常见 DevEco Studio 目录、
`JAVA_HOME`/`PATH`，最后应用 `HAPSIGN_JAVA`、`HAPSIGN_KEYTOOL`、
`HAPSIGN_HAP_SIGN_TOOL` 和 `HAPSIGN_HDC` 覆盖。

### 2. 只读检查输入

```bash
hapsign inspect --hap app.hap --json
```

如果任务需要 Real Profile/system_basic，检查时就传入 `--enable-capability`，并在后续
`sign`/`deploy` 中保持一致。这样 `migration_warnings` 才能按实际目标模式判断缓存。

成功结果包含绝对 `hap` 路径、`bundle_name`、布尔值 `signed`、`paths` 和
`migration_warnings`，不会登录、连接设备、创建签名材料或修改 HAP。若警告中
`destructive: true` 且 `requires_user_decision: true`，agent 必须先向用户说明影响和
整改选项，不能直接继续签名。

对于 `HAPSIGN-BREAKING-001`，先读取 `reasons`。能力模式不一致时可以让检查和签名
使用与缓存一致的 `--enable-capability` 配置；确实要切换模式时，用户可以备份
`paths.work_dir` 后接受一次性刷新。只有警告同时返回 `migratable: true`，并且用户
确认旧 Profile 类型后，才可执行：

```bash
hapsign migrate-cache --hap app.hap --state-dir /absolute/signing_files \
  --profile-type normal --json
```

Real Profile 使用 `system-basic`。不能从旧元数据可靠推断类型，不得替用户猜测。

### 3. 发起仅签名流程

```bash
hapsign sign \
  --hap app.hap \
  --output artifacts/app-signed.hap \
  --browser auto \
  --json
```

普通日志只写入标准错误，最终标准输出仍是单个 JSON 文档。没有可用 Token 缓存时，
命令会打开浏览器；已有 Token 不按日期主动失效，仅在携带 Token 的 API 请求被服务端
拒绝时尝试刷新。agent 应提示用户在浏览器中完成必要的授权，然后继续等待进程退出。
成功结果的 `signed_hap` 是可交付产物的绝对路径。

Agent 默认使用 `auto`。普通桌面会话会调用受控 Edge/Chrome；SSH、CI 或无桌面 Linux
会话会输出 `auth_required` 交接信息，而不是尝试启动不可见的浏览器。`system_controlled`
可强制使用隔离的临时浏览器上下文；`system` 会复用用户普通默认浏览器 Profile。

交接事件包含一次性 `verification_uri` 和 `callback_port`。`method=ssh_loopback` 或
`loopback_forwarding` 时，Agent 应提示用户保持原命令运行，并在有浏览器的电脑建立
同端口 loopback 转发：

```bash
ssh -N -L 127.0.0.1:<端口>:127.0.0.1:<端口> <同一SSH目标>
```

容器等运行时应使用其等价的私有端口转发能力。然后在建立转发的电脑打开一次性地址。
`--events json` 会把事件以
`HAPSIGN_EVENT=<json>` 写到 stderr；stdout 仍只在命令结束时输出一个 JSON 文档。
不得把登录地址写入持久日志，也不得把回调服务暴露到 `0.0.0.0`。

首次申请调试 Profile 需要设备 UDID。可以连接一台已授权调试设备让 HDC 自动读取，
也可以由调用方提供已核实的值：

```bash
hapsign sign --hap app.hap --device-udid <64位十六进制UDID> \
  --browser auto --json
```

当日 `metadata.json` 中的 `.p12/.cer/.p7b` 仍有效，且包名、能力模式与已知设备
UDID 匹配时，HapSign 会直接复用这些材料，无需连接设备或重新登录。

### 4. 可选安装

安装必须显式传入 HDC serial。未签名输入使用 `deploy`，已经签名的输入可直接使用
`install`：

```bash
hapsign deploy --hap app.hap --serial <serial> --json
hapsign install --hap app-signed.hap --serial <serial> --json
```

## 结果与退出码

- `0`：操作成功；JSON 的 `ok` 为 `true`。
- `1`：诊断不完整或登录/签名/HDC/安装运行失败；JSON 的 `ok` 为 `false`，
  `error` 给出脱敏后的阶段错误。
- `2`：命令行参数组合或输入 HAP 无效。

签名成功结果包含 `command`、`input_hap`、`bundle_name`、`browser_mode`、`paths`、
`signed_hap`、`installed`、`requested_capability_mode`、`capability_mode` 和
`capability_fallback`。请求 Real Profile 时如果 `capability_fallback: true`，HAP 已
完成调试签名，但实际 Profile 仍是 `normal`；agent 必须明确报告能力未满足，不能把它
描述为 system_basic 成功。读取 `signed_hap` 后应再次执行
`inspect --hap <signed_hap> --json`，只有确认 `signed: true` 才报告签名成功。
`inspect` 和 `doctor` 使用各自的只读结果字段。

## 输出安全约定

- `--output` 必须以 `.hap` 结尾，且不能与输入 HAP 是同一路径。
- 指定输出已存在时默认失败；只有明确传入 `--overwrite-output` 才会原子替换。
- 未指定 `--output` 时写入应用目录的 `signed_haps/`，使用 `<输入名>_signed.hap` 命名。
- `--state-dir` 配置 token/签名缓存根目录；`--output-dir` 配置默认产物目录。
  对应环境变量为 `HAPSIGN_SIGNING_DIR` 和 `HAPSIGN_SIGNED_HAPS_DIR`，命令行参数优先。
- `sign` 输入已经签名时不重复签名；传入 `--output` 时仍会发布到该路径，否则
  `signed_hap` 指向原输入。
- JSON 和默认日志不会包含 token、密钥库密码或完整账号认证载荷。
- `signing_files/` 包含私钥、证书、Profile 和登录缓存，不应上传、提交或放入不受信任
  的共享目录。

## 授权诊断

- 没有任何 `[callback]`：浏览器没有访问 loopback，检查浏览器模式和本地网络权限。
- `auth_required` 的 `method=ssh_loopback` 或 `loopback_forwarding`：确认本地端口和
  远端回调端口完全相同，并且浏览器运行在建立转发的那台电脑上。
- 收到 POST/GET 但没有“授权回调校验成功”：检查脱敏后的 CSRF 或参数错误。
- 已校验成功：浏览器继续转圈或随后出现 `net::ERR_ABORTED` 通常是回调后关闭页面产生；
  应继续检查 token 交换和后续签名阶段。
- Windows DPAPI 缓存解密失败会自动触发重新登录；Linux/macOS token 缓存是
  权限限制为 `0o600` 的明文文件。不要擅自删除签名材料，也不要把缓存放进共享或
  云同步目录。

CLI 默认把缓存和签名产物绑定到应用目录及其配置，不依赖调用者当前工作目录。诊断旧版
GUI/CLI 时仍应先记录实际可执行文件路径、版本、浏览器模式和日志目录，避免读取错实例。

## Linux 提示

Linux 源码运行既可以使用 DevEco Studio，也可以组合系统 JDK、OpenHarmony SDK 和
显式环境变量。先运行 `hapsign doctor --json`，再用 `hapsign devices list --json`
验证 USB
设备访问；如果普通用户看不到设备，应按所用发行版配置对应 udev 规则后重新插拔并
授权设备。HapSign 的 HDC server 检测在没有 `lsof` 时会回退到 `/proc`。
