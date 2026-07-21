"""hapsign CLI 启动脚本（供 sign_install.bat 与源码目录直接调用）。

安装包后会提供 ``hapsign`` 命令；在源码目录下可用 ``python main.py`` 等价调用。
"""

from hapsign.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
