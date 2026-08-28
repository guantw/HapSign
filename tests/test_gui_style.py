"""桌面样式的高 DPI 与设置页回归测试。"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from hapsign import gui
from hapsign.settings import AppSettings


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle(gui.HapSignStyle("Fusion"))
    app.setStyleSheet(gui.WINDOW_STYLE)
    app.setFont(gui._application_font())
    return app


def test_styles_use_integer_logical_pixels(qt_app) -> None:
    assert "font-size:" in gui.WINDOW_STYLE
    assert "pt;" not in gui.WINDOW_STYLE
    assert gui._application_font().pixelSize() == 14
    assert gui._fixed_width_font().pixelSize() == 13


def test_settings_controls_have_consistent_custom_style(qt_app, tmp_path) -> None:
    dialog = gui.SettingsDialog(
        AppSettings(),
        tmp_path / "logs" / "hapsign.log",
    )
    dialog.ensurePolished()

    assert dialog.browser_combo.currentData() == "system_controlled"
    assert dialog.browser_combo.sizeHint().height() >= 42
    assert dialog.storage_combo.sizeHint().height() >= 42
    assert dialog.log_level_combo.sizeHint().height() >= 42
    assert dialog.sensitive_check.sizeHint().height() >= 24

    buttons = dialog.findChild(QDialogButtonBox)
    save = buttons.button(QDialogButtonBox.StandardButton.Save)
    cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    assert save.objectName() == "primaryButton"
    assert cancel.objectName() == "secondaryButton"
