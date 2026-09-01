# HapSign 兼容性变更与迁移

本页集中记录会改变既有脚本、缓存或默认行为的变更。每项都有稳定编号；相同信息也会
出现在 `hapsign doctor --json` 的 `breaking_changes` 中。针对具体 HAP 运行
`hapsign inspect --hap app.hap --json` 时，当前确实适用的项目会出现在
`migration_warnings` 中。

自动化程序应按编号处理，不要匹配可能调整措辞的中文消息。`introduced_in` 在版本发布
前为 `unreleased`，发布时替换为正式版本号。

当前兼容策略和产品结论如下；这些字段也由 `doctor --json` 以 `decision`、
`compatibility_strategy` 和 `compatibility_options` 返回：

| 编号 | 兼容策略 | 明确结论 |
| --- | --- | --- |
| 001 | 配置匹配、显式迁移或备份后刷新 | 接受新的缓存一致性校验 |
| 002 | 可配置恢复普通系统浏览器 | 接受 `auto` 新默认值 |
| 003 | 可配置恢复旧状态与产物目录 | 接受应用目录新默认值 |
| 004 | 仅迁移调用方，不提供混合输出模式 | 保留 stdout 结果与 stderr 日志分离 |
| 005 | 仅迁移调用方，不提供扁平参数兼容层 | 保留显式子命令接口 |

## HAPSIGN-BREAKING-001

旧版 `metadata.json` 没有 `enable_capability`。新版还会核对目标能力模式、包名、64 位
设备 UDID 和材料文件，避免把 `normal` Test Profile 与 `system_basic` Real Profile
混用或跨目标复用。未通过一致性校验时，下一次签名会重新申请材料；生成同名密钥库时
会替换本地 `.p12`，申请证书时可能删除并替换账号下同名的远端调试证书。

先执行只读检查：

```bash
hapsign inspect --hap app.hap --json
```

如果后续签名需要 Real Profile，检查时也必须传入同一个选项：

```bash
hapsign inspect --hap app.hap --enable-capability --json
```

若 `migration_warnings` 包含本编号，先读取其中的 `reasons`、`migratable`、
`cached_capability_mode`、`cached_effective_capability_mode` 和
`expected_capability_mode`：

- `capability_mode_mismatch`：如果要复用现有模式，让 `inspect` 与 `sign/deploy` 一致地
  传入或省略 `--enable-capability`；如果确实要切换模式，使用下面第 1 种刷新方式。
- `missing_capability_mode`：仅当 `migratable: true` 时可选择下面第 2 或第 3 种迁移。
- 包名或 UDID 也不一致时不可迁移，只能选择第 1 种刷新方式。

新版元数据分别保存“调用方请求模式”和“服务端实际生成模式”。请求 Real Profile 但
应用条件不足而回退到 Test Profile 时，相同请求会复用该回退结果，不会每天重复刷新；
如需重新尝试 Real Profile，显式使用 `--refresh-signing`。签名结果 JSON 的
`capability_fallback` 会说明本次是否发生了这种回退。

整改方式：

1. 不需要保留旧材料：先备份 JSON 中 `paths.work_dir` 指向的整个目录，再正常签名，
   接受一次性刷新。
2. 确认旧材料是默认的 `normal` Test Profile：

   ```bash
   hapsign migrate-cache --hap app.hap --state-dir /absolute/signing_files \
     --profile-type normal --json
   ```

3. 确认旧材料是 `system_basic` Real Profile：

   ```bash
   hapsign migrate-cache --hap app.hap --state-dir /absolute/signing_files \
     --profile-type system-basic --json
   ```

迁移命令不登录、不联网、不申请或删除证书，只原子补充能力模式并把已存在的材料路径
规范为绝对路径；首次修改前会在同目录保留
`metadata.json.pre-capability-migration.bak`。如果缓存不是当天创建、材料缺失、已声明
不同模式、包名不匹配或 UDID 无效，命令会失败而不会覆盖。无法确认旧 Profile 类型时
不要猜，使用第一种刷新方式。

## HAPSIGN-BREAKING-002

CLI 登录浏览器默认值由 `system` 改为 `auto`。桌面会话优先使用隔离的临时
Edge/Chrome 上下文；SSH、CI 或无桌面 Linux 会话输出外部浏览器和安全 loopback
转发指引，不再尝试启动用户看不到的浏览器。

需要旧行为时显式配置：

```bash
hapsign sign --hap app.hap --browser system
```

或设置 `HAPSIGN_BROWSER=system`。命令行参数优先于环境变量。复用普通浏览器 Profile
可能让登录页表现不同，也可能受扩展或本地网络权限设置影响。

## HAPSIGN-BREAKING-003

最新 master 的 PR #5 默认把 CLI 状态和签名产物放在
`~/.hapsign/<bundle_name>/`；更早的源码 CLI 则使用进程当前工作目录下的相对
`signing_files/`。新版默认使用应用目录（冻结版本为可执行文件所在目录，源码运行时为
仓库根目录），并把默认产物放在应用目录的 `signed_haps/`。旧目录不会自动搬迁。
如果新目录尚无对应状态，而 PR #5 的 `~/.hapsign` 中仍有 Token 或当日可用材料，
`inspect` 会返回本编号。仅发现 Token 时这是非破坏性迁移提示；发现可复用签名材料时
才会标记 `destructive: true` 和 `requires_user_decision: true`。忽略后者继续签名可能
重新生成本地密钥，并删除、替换账号下的同名远端调试证书。

配置优先级为“命令行参数 > 专用环境变量 > 现有应用配置/应用目录默认值”：

| 用途 | 命令行 | 环境变量 | 含义 |
| --- | --- | --- | --- |
| 全局签名状态 | `--state-dir` | `HAPSIGN_SIGNING_DIR` | token 与各 bundle 的签名缓存根目录 |
| 单个 bundle 工作目录 | `--work-dir` | 无 | 只覆盖当前 HAP 的材料目录 |
| 默认产物目录 | `--output-dir` | `HAPSIGN_SIGNED_HAPS_DIR` | 未传 `--output` 时使用 |
| 精确产物路径 | `--output` | 无 | 优先使用单个文件路径；可与 `--output-dir` 同时传 |

`HAPSIGN_DATA_DIR` 保持兼容：它仍表示应用数据根目录，签名状态位于其
`signing_files/` 子目录。`doctor --json` 的 `paths` 会给出本次解析后的绝对路径。

若要恢复 PR #5 的用户主目录方案，显式传入原状态和产物路径即可：

```bash
hapsign sign --hap app.hap --state-dir ~/.hapsign \
  --output-dir ~/.hapsign/<bundle_name> --json
```

若要继续复用更早版本的工作目录，也可显式传入旧路径：

```bash
hapsign sign --hap app.hap --state-dir /old/cwd/signing_files \
  --output-dir /old/cwd/signed_haps --json
```

## HAPSIGN-BREAKING-004

该行为由 master 的 PR #5（合入提交 `37c4c3`）引入。

CLI 普通输出与日志流已分离，且面向人的文本不作为稳定协议。依赖旧标准输出日志文本的
脚本应改用 `--json`，只按退出码、`ok`、`command`、`signed_hap`、`installed`、
`paths` 和稳定编号字段判断。普通日志写入标准错误。产品结论是保留分离，不提供把日志
重新混入 stdout 的兼容开关。

## HAPSIGN-BREAKING-005

该行为由 master 的 PR #5（合入提交 `37c4c3`）引入。

CLI 改为显式子命令接口。旧调用不会被静默猜测或兼容转发，以免 agent 把本应只读的
检查误执行为签名或安装。产品结论是保留子命令，不提供旧扁平参数兼容层。常见映射如下：

| 旧调用 | 新调用 |
| --- | --- |
| `hapsign --doctor --json` | `hapsign doctor --json` |
| `hapsign --hap app.hap --inspect --json` | `hapsign inspect --hap app.hap --json` |
| `hapsign --hap app.hap --sign-only --json` | `hapsign sign --hap app.hap --json` |
| `hapsign --hap app.hap --json` | `hapsign deploy --hap app.hap --serial <serial> --json` |

缓存迁移使用 `migrate-cache`，已签名 HAP 的安装使用 `install`。`deploy` 和 `install`
必须显式传入非空 `--serial`；`sign` 可以传 `--serial`、可信的 `--device-udid`，或在
不连接设备时复用包含有效 UDID 的兼容缓存材料。
