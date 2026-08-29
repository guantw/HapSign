# Security Policy

## 报告安全问题

请不要在公开 Issue 中提交 token、证书、私钥、设备 UDID、登录回调内容或可复现的
账号信息。维护者应在仓库 Settings → Code security 中启用 GitHub Private
vulnerability reporting；启用后请使用仓库 Security 页的“Report a vulnerability”
私密提交。若该入口尚不可用，只提交一个不包含敏感细节的 Issue，请求维护者建立
私密沟通，未经确认不要公开漏洞细节。

## 本地敏感数据

桌面、便携版和 CLI 默认会按应用配置在程序目录的 `signing_files/` 中保存 token
缓存、调试证书、Profile 和 `.p12` 密钥库；CLI 可用 `--state-dir` 或
`HAPSIGN_SIGNING_DIR` 覆盖。此默认值不依赖启动命令时的工作目录。这些文件已被
`.gitignore` 排除，但仍是本机敏感
数据。Windows 上 token 缓存通过当前用户作用域的 DPAPI
（CryptProtectData）静态加密后落盘，其他平台退化为受限权限（仅当前用户可读）
的明文存储，并在首次保存时打印告警；请勿把缓存目录放入云同步目录，在共享电脑
上使用后应删除对应目录。移动或分享便携目录前应先移除 `signing_files/`；桌面设置
可改为用户 AppData Local 或自定义目录；同样应按敏感数据目录保护。
程序目录的 `signed_haps/` 可能包含用户应用代码，移动或分享便携目录前也应检查；
可在设置中关闭保留签名 HAP。

默认密钥库密码只用于本机调试材料，不应被视为安全密码。可以在运行前设置
`HAPSIGN_KEYSTORE_PASSWORD` 环境变量覆盖它：

```powershell
$env:HAPSIGN_KEYSTORE_PASSWORD = "使用你自己的强密码"
```

敏感诊断开关默认关闭，此时程序不会记录 token、完整回调请求、CSRF code 或完整
登录 URL。只有用户主动开启该开关并选择 DEBUG 级别后，才会记录 token、用户标识及
完整 API 请求/响应；密钥库密码无论如何都不会记录。分享 `logs/hapsign.log*` 前请
确认开关状态并检查内容。若日志已经包含凭据，请停止分享、删除日志、清除 token
缓存并重新登录。

## 支持范围

安全修复只保证在最新发布版本和当前主分支提供。项目依赖第三方在线接口，接口变化、
账号策略和服务条款不在项目的安全支持范围内。

程序访问的域名、发送的数据和本地保留策略见 [PRIVACY.md](PRIVACY.md)。
