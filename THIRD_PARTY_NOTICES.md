# Third-party notices

HapSign 自身的源代码使用 [MIT License](LICENSE)。本文件说明源码依赖及便携版
可能包含的第三方组件；第三方组件不因被 HapSign 使用或打包而改用 MIT License。

## Python 与桌面运行时

| 组件 | 用途 | 上游许可 |
| --- | --- | --- |
| Python | 应用运行时 | Python Software Foundation License |
| PySide6 Essentials / Shiboken6 / Qt | 桌面界面 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only，或 Qt 商业许可 |
| Playwright for Python / Playwright driver | 控制登录浏览器 | Apache-2.0 |
| greenlet | Playwright 运行时传递依赖 | MIT |
| pyee | Playwright 运行时传递依赖 | MIT |
| Requests | HTTPS 客户端 | Apache-2.0 |
| urllib3 | HTTPS 连接 | MIT |
| certifi | CA 证书集合 | MPL-2.0 |
| charset-normalizer | 文本编码检测 | MIT |
| idna | 国际化域名处理 | BSD-3-Clause |
| PyInstaller bootloader | 生成可执行文件 | GPL-2.0-or-later，带允许分发生成程序的特殊例外 |

Windows 便携版以独立 DLL 的方式携带未修改的 Qt/PySide6 运行库，选择 LGPL-3.0
路径进行分发。接收者可以用 ABI 兼容的修改版动态库替换
`_internal/PySide6/` 中相应文件；HapSign 不对调试这种修改施加额外限制。Qt 的
版权、商标和许可仍归各权利人所有。

构建脚本会把当前构建环境中上述 Python distribution 随附的 LICENSE、COPYING
和 NOTICE 文件复制到便携包的 `licenses/python/`。Playwright 自带的 driver
许可及第三方声明也保留在 `_internal/playwright/driver/`。兼容包如果包含
Chromium，还必须保留该目录内 Playwright/Chromium 的第三方声明。

## 便携工具链

Windows 正式便携版从 `toolchain.lock.json` 锁定的公开上游准备：

| 组件 | 当前版本/来源 | 许可 |
| --- | --- | --- |
| Java、keytool | Eclipse Temurin 21，经 `jlink` 生成 | GPL-2.0-only WITH Classpath-exception-2.0；各模块可能另有随附许可 |
| HDC | OpenHarmony 公共 SDK 6.1.0.31 | 以 SDK NOTICE 和上游仓库为准，主体为 Apache-2.0 |
| hap-sign-tool | OpenHarmony 公共 SDK 6.1.0.31 | Apache-2.0 及 JAR 内随附第三方许可 |
| libusb_shared | OpenHarmony 公共 SDK 内的 libusb 1.0.28 | LGPL-2.1-or-later |

构建脚本会保留：

- `resources/toolchain/<platform>/runtime/legal/`；
- OpenHarmony 公共 SDK 的完整 `NOTICE.txt`；
- 下载地址、版本及 SHA-256 的 `PROVENANCE.txt` 和 `toolchain.lock.json`；
- `licenses/libusb-source/` 中与 DLL 对应的 OpenHarmony libusb 完整源码快照、
  补丁、构建配置和 LGPL 文本。

HapSign 的 MIT 许可只覆盖本项目自身代码，不覆盖这些独立组件。接收者可以按各组件
许可证替换或重新构建它们。源码快照的固定来源和哈希见
`third_party/libusb/README.md`。

`--allow-deveco-toolchain` 仍可为兼容排障从本机 DevEco 复制工具；此模式的
`PROVENANCE.txt` 会标识本机来源，不属于上述可审计正式构建，不应直接上传公开
Release。`--skip-toolchain` 则完全不包含这些工具。

详细发布门禁见 [docs/OPEN_SOURCE_RELEASE.md](docs/OPEN_SOURCE_RELEASE.md)。

## 参考实现

HAP 签名块格式、HDC 行为和 hap-sign-tool 调用方式参考或对齐了 OpenHarmony 的
`developtools_hapsigner` 与 `developtools_hdc` 项目。上游项目以各自仓库中的
LICENSE 为准，主要采用 Apache-2.0：

- <https://gitee.com/openharmony/developtools_hapsigner>
- <https://gitee.com/openharmony/developtools_hdc>

如果后续从这些或其他开源仓库复制、移植了具体代码，而不只是依据公开格式重新实现，
必须在提交时记录来源文件、commit、修改内容和许可证。不得把不兼容许可证覆盖的代码
直接标成 HapSign 的 MIT 代码。

## 商标与非隶属关系

HarmonyOS、OpenHarmony、HUAWEI、DevEco Studio、Qt、Python、Chromium、
Microsoft Edge 和 Google Chrome 是各自权利人的商标或名称。本项目对这些名称的
使用仅用于描述兼容性和依赖关系，不表示隶属、授权、认可或背书。
