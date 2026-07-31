"""SDK 路径解析测试。"""

import os

from hapsign.config import default_deveco_home, resolve_sdk_paths


def test_default_deveco_home_by_platform() -> None:
    assert default_deveco_home("darwin") == "/Applications/DevEco-Studio.app/Contents"
    assert default_deveco_home("win32") == r"D:\Program Files\Huawei\DevEco Studio"
    assert default_deveco_home("linux") == "/opt/DevEco-Studio"


def test_resolve_sdk_paths_darwin() -> None:
    home = "/Applications/DevEco-Studio.app/Contents"
    java, hap_sign, hdc, keytool = resolve_sdk_paths(home, "darwin")

    assert java == os.path.join(home, "jbr", "Contents", "Home", "bin", "java")
    assert keytool == os.path.join(home, "jbr", "Contents", "Home", "bin", "keytool")
    assert hdc.endswith(os.path.join("toolchains", "hdc"))
    assert hap_sign.endswith(os.path.join("lib", "hap-sign-tool.jar"))
    assert not java.endswith(".exe")
    assert not hdc.endswith(".exe")


def test_resolve_sdk_paths_win32() -> None:
    home = r"D:\Program Files\Huawei\DevEco Studio"
    java, hap_sign, hdc, keytool = resolve_sdk_paths(home, "win32")

    assert java.endswith("java.exe")
    assert keytool.endswith("keytool.exe")
    assert hdc.endswith("hdc.exe")
    assert hap_sign.endswith("hap-sign-tool.jar")
    assert os.path.join("jbr", "bin") in java.replace("/", "\\") or "jbr" in java
