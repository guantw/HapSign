"""按便携构建模式收集 Playwright 数据。"""

import os

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("playwright")
if os.environ.get("HAPSIGN_BUNDLE_CHROMIUM", "0") != "1":
    datas = [item for item in datas if ".local-browsers" not in item[0]]
