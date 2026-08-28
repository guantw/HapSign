"""HapSign 桌面应用。

桌面层只负责交互与后台调度，签名逻辑继续复用 :class:`SignPipeline`。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# Qt 6 默认支持高 DPI；在导入 Qt 前显式启用并保留小数缩放比例，
# 避免 Windows 在 125%/150%/4K 屏幕上退回位图拉伸。
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import (
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from hapsign import __version__
from hapsign.cancellation import OperationCancelled
from hapsign.cli import detect_bundle_name
from hapsign.diagnostics import configure_file_logging
from hapsign.login.browser_login import playwright_browser_smoke_test
from hapsign.pipeline import SignPipeline
from hapsign.runtime import (
    discover_toolchain,
    platform_tag,
)
from hapsign.settings import (
    AppSettings,
    config_file_path,
    load_settings,
    save_settings,
    signed_haps_dir,
    signing_files_dir,
)
from hapsign.signing.hap_inspect import is_hap_signed
from hapsign.signing.installer import Installer

LOGGER = logging.getLogger(__name__)

WINDOW_STYLE = """
QWidget {
    color: #18212f;
    font-size: 14px;
}
QWidget#root {
    background: #f4f6fa;
}
QFrame#card {
    background: #ffffff;
    border: 1px solid #e6eaf0;
    border-radius: 18px;
}
QFrame#dropZone {
    background: #fafbfe;
    border: 2px dashed #cbd3df;
    border-radius: 16px;
}
QFrame#dropZone:hover, QFrame#dropZone[active="true"] {
    background: #f3f6ff;
    border-color: #5a6ff0;
}
QLabel#title {
    color: #111827;
    font-size: 28px;
    font-weight: 700;
}
QLabel#subtitle {
    color: #657084;
    font-size: 14px;
}
QLabel#dropTitle {
    color: #18212f;
    font-size: 18px;
    font-weight: 650;
}
QLabel#muted, QLabel#fileMeta, QLabel#logTitle {
    color: #778195;
    font-size: 13px;
}
QLabel#fileName {
    color: #172033;
    font-size: 16px;
    font-weight: 650;
}
QLabel#status {
    color: #536078;
    font-size: 14px;
}
QLabel#chip {
    color: #526075;
    background: #e9edf5;
    border-radius: 11px;
    padding: 3px 9px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#signedChip {
    color: #237353;
    background: #e6f6ee;
    border-radius: 11px;
    padding: 3px 9px;
    font-size: 12px;
    font-weight: 650;
}
QLabel#unsignedChip {
    color: #8a5b14;
    background: #fff2d9;
    border-radius: 11px;
    padding: 3px 9px;
    font-size: 12px;
    font-weight: 650;
}
QPushButton {
    min-height: 42px;
    border-radius: 11px;
    padding: 0 18px;
    font-weight: 650;
}
QPushButton#primaryButton {
    color: #ffffff;
    background: #4459dc;
    border: 1px solid #4459dc;
}
QPushButton#primaryButton:hover {
    background: #384cc9;
    border-color: #384cc9;
}
QPushButton#primaryButton:pressed {
    background: #3043b7;
}
QPushButton#primaryButton:disabled {
    color: #a8afbc;
    background: #e6e9ef;
    border-color: #e6e9ef;
}
QPushButton#secondaryButton {
    color: #334155;
    background: #ffffff;
    border: 1px solid #d9dee8;
}
QPushButton#secondaryButton:hover {
    background: #f7f8fb;
    border-color: #c8cfdb;
}
QPushButton#cancelButton {
    color: #a33c48;
    background: #fff7f8;
    border: 1px solid #efcbd0;
}
QPushButton#cancelButton:hover {
    color: #8f2f3b;
    background: #fdecef;
    border-color: #e9aeb7;
}
QPushButton#removeButton {
    color: #7b8495;
    background: transparent;
    border: none;
    border-radius: 16px;
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    padding: 0;
    font-size: 24px;
    font-weight: 400;
}
QPushButton#removeButton:hover {
    color: #b33f4a;
    background: #fdecef;
}
QPushButton#removeButton:pressed {
    background: #f9dce1;
}
QProgressBar {
    min-height: 18px;
    max-height: 18px;
    border: none;
    border-radius: 9px;
    background: #e8ebf2;
    color: #ffffff;
    font-size: 12px;
    font-weight: 650;
    text-align: center;
}
QProgressBar::chunk {
    border-radius: 9px;
    background: #5367e8;
}
QPlainTextEdit {
    color: #445066;
    background: #f7f8fb;
    border: 1px solid #e7eaf0;
    border-radius: 11px;
    padding: 10px;
    font-size: 13px;
    selection-background-color: #cdd5ff;
}
QDialog {
    background: #f7f8fb;
}
QDialog QLabel#settingsTitle {
    color: #111827;
    font-size: 22px;
    font-weight: 700;
}
QDialog QLabel#settingsSubtitle {
    color: #778195;
    font-size: 13px;
}
QFrame#settingsSection {
    background: #ffffff;
    border: 1px solid #e5e9f0;
    border-radius: 14px;
}
QLabel#sectionTitle {
    color: #263247;
    font-size: 14px;
    font-weight: 700;
}
QLabel#warning {
    color: #966114;
    background: #fff8e8;
    border: 1px solid #f1dfb4;
    border-radius: 9px;
    padding: 7px 10px;
    font-size: 13px;
}
QComboBox, QLineEdit {
    min-height: 40px;
    color: #243047;
    background: #ffffff;
    border: 1px solid #d9dee8;
    border-radius: 10px;
    padding: 0 13px;
    selection-background-color: #dfe4ff;
}
QComboBox:hover, QLineEdit:hover {
    border-color: #aeb9ca;
}
QComboBox:focus, QLineEdit:focus {
    border: 1px solid #6072e8;
}
QComboBox {
    padding-right: 48px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 42px;
    background: #f7f8fc;
    border: none;
    border-left: 1px solid #e2e6ee;
    border-top-right-radius: 10px;
    border-bottom-right-radius: 10px;
}
QComboBox::drop-down:hover {
    background: #eef1fb;
}
QComboBox::down-arrow {
    image: none;
    width: 16px;
    height: 16px;
}
QComboBox QAbstractItemView {
    color: #243047;
    background: #ffffff;
    border: 1px solid #d8dee9;
    border-radius: 10px;
    padding: 6px;
    outline: none;
    selection-color: #263baf;
    selection-background-color: #edf0ff;
}
QComboBox QAbstractItemView::item {
    min-height: 38px;
    padding: 0 12px;
    border-radius: 7px;
}
QComboBox QAbstractItemView::item:selected {
    color: #263baf;
    background: #edf0ff;
    border: none;
}
QLineEdit:disabled {
    color: #98a2b3;
    background: #f3f5f8;
    border-color: #e4e8ef;
}
QCheckBox {
    min-height: 24px;
    spacing: 10px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
}
QDialogButtonBox QPushButton {
    min-width: 92px;
}
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 5px 2px;
}
QScrollBar::handle:vertical {
    background: #c7cedb;
    min-height: 32px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #9da8bb;
}
QScrollBar::handle:vertical:pressed {
    background: #7e8aa0;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px 5px;
}
QScrollBar::handle:horizontal {
    background: #c7cedb;
    min-width: 32px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: #9da8bb;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: transparent;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
"""


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _make_app_icon(size: int = 128) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(8, 8, size - 16, size - 16)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#6477f2"))
    gradient.setColorAt(1.0, QColor("#3548c7"))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(rect, 28, 28)

    path = QPainterPath()
    path.moveTo(size * 0.31, size * 0.36)
    path.lineTo(size * 0.50, size * 0.25)
    path.lineTo(size * 0.69, size * 0.36)
    path.lineTo(size * 0.69, size * 0.64)
    path.lineTo(size * 0.50, size * 0.75)
    path.lineTo(size * 0.31, size * 0.64)
    path.closeSubpath()
    painter.setBrush(QColor("#ffffff"))
    painter.drawPath(path)

    painter.setPen(QPen(QColor("#5367e8"), max(2, size // 32)))
    painter.drawLine(
        QPointF(size * 0.39, size * 0.47),
        QPointF(size * 0.61, size * 0.47),
    )
    painter.drawLine(
        QPointF(size * 0.39, size * 0.56),
        QPointF(size * 0.56, size * 0.56),
    )
    painter.end()
    return QIcon(pixmap)


class HapSignStyle(QProxyStyle):
    """在 Fusion 基础上绘制与界面一致的箭头和复选框。"""

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element == QStyle.PrimitiveElement.PE_IndicatorArrowDown:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            center = option.rect.center()
            pen = QPen(QColor("#667085"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(center.x() - 4.5, center.y() - 2)
            path.lineTo(center.x(), center.y() + 2.5)
            path.lineTo(center.x() + 4.5, center.y() - 2)
            painter.drawPath(path)
            painter.restore()
            return

        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(option.rect).adjusted(1, 1, -1, -1)
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            checked = bool(option.state & QStyle.StateFlag.State_On)
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
            if checked:
                fill = QColor("#4459dc" if enabled else "#aab3dd")
                border = QColor("#4459dc" if enabled else "#aab3dd")
            else:
                fill = QColor("#f8f9fc" if enabled else "#f0f2f5")
                border = QColor("#aeb8c8" if hovered else "#c7ced9")
            painter.setBrush(fill)
            painter.setPen(QPen(border, 1.2))
            painter.drawRoundedRect(rect, 4, 4)
            if checked:
                pen = QPen(QColor("#ffffff"), 1.8)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                check = QPainterPath()
                check.moveTo(rect.left() + 4.0, rect.center().y())
                check.lineTo(rect.left() + 7.2, rect.bottom() - 4.2)
                check.lineTo(rect.right() - 3.5, rect.top() + 4.1)
                painter.drawPath(check)
            painter.restore()
            return

        super().drawPrimitive(element, option, painter, widget)


def _application_font() -> QFont:
    """选择高质量中文 UI 字体，并让笔画尽量贴合物理像素网格。"""
    available = set(QFontDatabase.families())
    preferred = (
        ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
        if platform_tag() == "windows"
        else ("PingFang SC", "Noto Sans CJK SC", "Noto Sans")
    )
    family = next(
        (candidate for candidate in preferred if candidate in available),
        QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family(),
    )
    font = QFont(family)
    # 整数逻辑像素避免 10pt -> 13.333px 在 200% DPI 下形成分数物理像素。
    font.setPixelSize(14)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
    )
    return font


def _fixed_width_font() -> QFont:
    available = set(QFontDatabase.families())
    family = next(
        (
            candidate
            for candidate in ("Cascadia Mono", "Consolas", "Microsoft YaHei UI")
            if candidate in available
        ),
        QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family(),
    )
    font = QFont(family)
    font.setPixelSize(13)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
    )
    return font


class ComboItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        clean_option = QStyleOptionViewItem(option)
        clean_option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, clean_option, index)


class StyledComboBox(QComboBox):
    """使用程序自己的抗锯齿箭头，避免平台原生方框样式混入。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setItemDelegate(ComboItemDelegate(self))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() - 21.0, self.height() / 2.0)
        pen = QPen(QColor("#667085"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(center.x() - 4.5, center.y() - 2.0)
        path.lineTo(center.x(), center.y() + 2.5)
        path.lineTo(center.x() + 4.5, center.y() - 2.0)
        painter.drawPath(path)


class DropZone(QFrame):
    """支持点击与 HAP 文件拖放的选择区域。"""

    choose_requested = Signal()
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(190)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 25, 28, 25)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        badge = QLabel("HAP")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(58, 58)
        badge.setStyleSheet(
            "color: #4459dc; background: #e9edff; border-radius: 16px;"
            "font-size: 14px; font-weight: 750;"
        )
        title = QLabel("拖入 HAP 文件")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("或点击这里从电脑中选择")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(hint)

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.choose_requested.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if any(url.toLocalFile().lower().endswith(".hap") for url in urls):
            event.acceptProposedAction()
            self._set_active(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_active(False)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".hap"):
                self.file_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


class PipelineLogHandler(logging.Handler):
    """将后台线程日志转发到 Qt 信号。"""

    def __init__(self, callback) -> None:
        super().__init__(logging.INFO)
        self.callback = callback
        self.last_error = ""

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.levelno >= logging.ERROR:
            self.last_error = message
        self.callback(message)


class PipelineWorker(QObject):
    """在后台线程运行签名安装流程。"""

    log_message = Signal(str)
    progress_changed = Signal(int, str)
    finished = Signal(bool, bool, str)

    def __init__(
        self,
        hap_path: str,
        state_dir: Path,
        browser_mode: str,
        keep_signed_hap: bool,
    ) -> None:
        super().__init__()
        self.hap_path = hap_path
        self.state_dir = state_dir
        self.browser_mode = browser_mode
        self.keep_signed_hap = keep_signed_hap
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        """可从 GUI 线程直接调用；Event 是线程安全的。"""
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        root_logger = logging.getLogger()
        handler = PipelineLogHandler(self.log_message.emit)
        root_logger.addHandler(handler)

        try:
            bundle_name = detect_bundle_name(self.hap_path)
            signed = is_hap_signed(self.hap_path)
            toolchain = discover_toolchain()
            missing = toolchain.missing(require_signing=not signed)
            if missing:
                details = "\n".join(f"• {item}" for item in missing)
                raise RuntimeError(f"便携工具链不完整：\n{details}")

            self.log_message.emit(f"应用包：{bundle_name}")
            self.log_message.emit(f"工具来源：{toolchain.source}")
            LOGGER.info(
                "开始桌面流程：hap=%s state_dir=%s browser=%s",
                self.hap_path,
                self.state_dir,
                self.browser_mode,
            )
            pipeline = SignPipeline(
                hap_path=self.hap_path,
                bundle_name=bundle_name,
                state_dir=str(self.state_dir),
                browser_mode=self.browser_mode,
                signed_output_dir=str(signed_haps_dir()),
                keep_signed_hap=self.keep_signed_hap,
                cancel_event=self.cancel_event,
                progress_callback=self.progress_changed.emit,
            )
            if pipeline.run():
                self.finished.emit(True, False, "HAP 已成功安装到设备")
            elif self.cancel_event.is_set():
                self.finished.emit(False, True, "任务已取消")
            else:
                message = handler.last_error or "签名或安装没有完成，请查看运行记录"
                self.finished.emit(False, False, message)
        except OperationCancelled:
            self.log_message.emit("任务已取消，清理工作已完成")
            self.finished.emit(False, True, "任务已取消")
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, True, "任务已取消")
            else:
                LOGGER.exception("桌面流程失败")
                self.finished.emit(False, False, str(exc))
        finally:
            root_logger.removeHandler(handler)


class DeviceCheckWorker(QObject):
    """在后台确认 HDC 工具和设备连接状态。"""

    progress_changed = Signal(int, str)
    finished = Signal(bool, bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            self.progress_changed.emit(15, "正在检查 HDC 工具")
            toolchain = discover_toolchain()
            missing = toolchain.missing(require_signing=False)
            if missing:
                details = "\n".join(f"• {item}" for item in missing)
                raise RuntimeError(f"HDC 工具不可用：\n{details}")
            self.progress_changed.emit(55, "正在查询连接设备")
            with Installer(cancel_event=self.cancel_event) as installer:
                udid = installer.get_udid()
            self.finished.emit(
                True,
                False,
                f"设备连接正常（UDID 尾号 {udid[-6:]}）",
            )
        except OperationCancelled:
            self.finished.emit(False, True, "设备检测已取消")
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, True, "设备检测已取消")
            else:
                self.finished.emit(False, False, str(exc))


def _open_local_directory(parent: QWidget, path: Path, title: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.warning(parent, f"无法打开{title}", str(exc))
        return
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
        QMessageBox.warning(parent, f"无法打开{title}", f"目录位置：\n{path}")


class SettingsDialog(QDialog):
    """桌面版持久化设置。"""

    def __init__(
        self,
        settings: AppSettings,
        log_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._log_path = log_path
        self.setWindowTitle("HapSign 设置")
        self.setMinimumWidth(610)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(11)

        title = QLabel("设置")
        title.setObjectName("settingsTitle")
        layout.addWidget(title)
        basic_section = QFrame()
        basic_section.setObjectName("settingsSection")
        basic_layout = QVBoxLayout(basic_section)
        basic_layout.setContentsMargins(16, 13, 16, 15)
        basic_layout.setSpacing(9)
        basic_title = QLabel("登录与签名材料")
        basic_title.setObjectName("sectionTitle")
        basic_layout.addWidget(basic_title)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.browser_combo = StyledComboBox()
        self.browser_combo.addItem(
            "系统 Edge / Chrome（受控，推荐）",
            "system_controlled",
        )
        self.browser_combo.addItem("内置 Chromium（兼容模式）", "playwright")
        self.browser_combo.addItem("系统默认浏览器（非受控备用）", "system")
        self.browser_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._select_data(self.browser_combo, settings.browser_mode)
        form.addRow("登录浏览器", self.browser_combo)

        self.storage_combo = StyledComboBox()
        self.storage_combo.addItem("程序目录（便携）", "program")
        self.storage_combo.addItem("用户 AppData Local", "appdata")
        self.storage_combo.addItem("自定义目录", "custom")
        self.storage_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._select_data(self.storage_combo, settings.signing_storage)
        self.storage_combo.currentIndexChanged.connect(self._update_custom_state)
        form.addRow("签名文件位置", self.storage_combo)

        custom_row = QWidget()
        custom_layout = QHBoxLayout(custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(8)
        self.custom_path = QLineEdit(settings.custom_signing_dir)
        self.custom_path.setPlaceholderText("选择一个用于保存签名材料的目录")
        self.custom_browse_button = QPushButton("浏览")
        self.custom_browse_button.setObjectName("secondaryButton")
        self.custom_browse_button.clicked.connect(self._choose_custom_directory)
        custom_layout.addWidget(self.custom_path, 1)
        custom_layout.addWidget(self.custom_browse_button)
        form.addRow("自定义目录", custom_row)
        basic_layout.addLayout(form)
        layout.addWidget(basic_section)

        diagnostic_section = QFrame()
        diagnostic_section.setObjectName("settingsSection")
        diagnostic_layout = QVBoxLayout(diagnostic_section)
        diagnostic_layout.setContentsMargins(16, 13, 16, 14)
        diagnostic_layout.setSpacing(8)
        diagnostic_title = QLabel("日志与签名产物")
        diagnostic_title.setObjectName("sectionTitle")
        diagnostic_layout.addWidget(diagnostic_title)
        log_row = QHBoxLayout()
        log_row.setSpacing(18)
        log_label = QLabel("日志级别")
        log_label.setMinimumWidth(104)
        self.log_level_combo = StyledComboBox()
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level_combo.addItem(level, level)
        self._select_data(self.log_level_combo, settings.log_level)
        log_row.addWidget(log_label)
        log_row.addWidget(self.log_level_combo, 1)
        diagnostic_layout.addLayout(log_row)

        self.sensitive_check = QCheckBox(
            "记录敏感诊断信息（可能包含 token、用户标识及完整 API 请求/响应）"
        )
        self.sensitive_check.setChecked(settings.log_sensitive_data)
        diagnostic_layout.addWidget(self.sensitive_check)
        warning = QLabel("仅 DEBUG 记录敏感内容；签名库密码永不记录，排障后请关闭。")
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        diagnostic_layout.addWidget(warning)
        self.keep_signed_check = QCheckBox("保留最新一个签名后的 HAP")
        self.keep_signed_check.setChecked(settings.keep_signed_hap)
        diagnostic_layout.addWidget(self.keep_signed_check)
        signed_hint = QLabel(
            "目录：程序目录 / signed_haps；新文件成功后自动清理旧 HAP。"
        )
        signed_hint.setObjectName("muted")
        signed_hint.setWordWrap(True)
        signed_hint.setToolTip(str(signed_haps_dir()))
        diagnostic_layout.addWidget(signed_hint)
        layout.addWidget(diagnostic_section)

        data_section = QFrame()
        data_section.setObjectName("settingsSection")
        data_layout = QVBoxLayout(data_section)
        data_layout.setContentsMargins(16, 13, 16, 14)
        data_layout.setSpacing(8)
        data_title = QLabel("数据位置")
        data_title.setObjectName("sectionTitle")
        data_layout.addWidget(data_title)
        directory_actions = QHBoxLayout()
        directory_actions.setSpacing(9)
        open_signing = QPushButton("打开签名目录")
        open_signing.setObjectName("secondaryButton")
        open_signing.setToolTip(str(signing_files_dir(settings)))
        open_signing.clicked.connect(self._open_selected_signing_directory)
        open_logs = QPushButton("打开日志目录")
        open_logs.setObjectName("secondaryButton")
        open_logs.setToolTip(str(self._log_path.parent))
        open_logs.clicked.connect(
            lambda: _open_local_directory(self, self._log_path.parent, "日志目录")
        )
        open_signed = QPushButton("打开签名 HAP 目录")
        open_signed.setObjectName("secondaryButton")
        open_signed.setToolTip(str(signed_haps_dir()))
        open_signed.clicked.connect(
            lambda: _open_local_directory(
                self,
                signed_haps_dir(),
                "签名 HAP 目录",
            )
        )
        directory_actions.addWidget(open_signing)
        directory_actions.addWidget(open_signed)
        directory_actions.addWidget(open_logs)
        data_layout.addLayout(directory_actions)
        data_section.setToolTip(f"配置文件：{config_file_path()}")
        layout.addWidget(data_section)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("保存")
        save_button.setObjectName("primaryButton")
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("取消")
        cancel_button.setObjectName("secondaryButton")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_custom_state()

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    @Slot()
    def _update_custom_state(self) -> None:
        enabled = self.storage_combo.currentData() == "custom"
        self.custom_path.setEnabled(enabled)
        self.custom_browse_button.setEnabled(enabled)

    @Slot()
    def _choose_custom_directory(self) -> None:
        initial = self.custom_path.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择签名文件目录",
            initial,
        )
        if selected:
            self.custom_path.setText(selected)

    def result_settings(self) -> AppSettings:
        return AppSettings(
            log_level=str(self.log_level_combo.currentData()),
            signing_storage=str(self.storage_combo.currentData()),
            custom_signing_dir=self.custom_path.text().strip(),
            browser_mode=str(self.browser_combo.currentData()),
            log_sensitive_data=self.sensitive_check.isChecked(),
            keep_signed_hap=self.keep_signed_check.isChecked(),
        )

    @Slot()
    def _open_selected_signing_directory(self) -> None:
        settings = self.result_settings()
        if settings.signing_storage == "custom" and not settings.custom_signing_dir:
            QMessageBox.warning(self, "尚未选择目录", "请先选择一个自定义目录。")
            return
        _open_local_directory(
            self,
            signing_files_dir(settings),
            "签名目录",
        )

    @Slot()
    def _validate_and_accept(self) -> None:
        if (
            self.storage_combo.currentData() == "custom"
            and not self.custom_path.text().strip()
        ):
            QMessageBox.warning(self, "无法保存", "自定义签名目录不能为空。")
            return
        self.accept()


class MainWindow(QMainWindow):
    """HapSign 主窗口。"""

    def __init__(
        self,
        settings: AppSettings | None = None,
        log_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or load_settings()
        self.log_path = log_path or configure_file_logging(self.settings)
        self.selected_path: Path | None = None
        self.worker_thread: QThread | None = None
        self.worker: QObject | None = None
        self._close_after_cancel = False
        self._cancel_requested = False

        self.setWindowTitle("HapSign")
        self.setWindowIcon(_make_app_icon())
        self.resize(820, 690)
        self.setMinimumSize(QSize(720, 620))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(42, 34, 42, 34)
        outer.setSpacing(22)
        outer.addLayout(self._build_header())

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 26, 26, 24)
        card_layout.setSpacing(18)

        file_layout = QHBoxLayout()
        file_layout.setSpacing(14)
        self.drop_zone = DropZone()
        self.drop_zone.choose_requested.connect(self._choose_file)
        self.drop_zone.file_dropped.connect(self._load_file)
        file_layout.addWidget(self.drop_zone, 1)

        self.file_panel = self._build_file_panel()
        self.file_panel.hide()
        file_layout.addWidget(self.file_panel)
        card_layout.addLayout(file_layout)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(10)
        self.choose_button = QPushButton("选择 HAP")
        self.choose_button.setObjectName("secondaryButton")
        self.choose_button.clicked.connect(self._choose_file)
        self.check_button = QPushButton("检测设备")
        self.check_button.setObjectName("secondaryButton")
        self.check_button.clicked.connect(self._check_device)
        self.start_button = QPushButton("开始签名并安装")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.clicked.connect(self._cancel_active_task)
        self.cancel_button.hide()
        action_layout.addWidget(self.choose_button)
        action_layout.addWidget(self.check_button)
        action_layout.addWidget(self.start_button, 1)
        action_layout.addWidget(self.cancel_button)
        card_layout.addLayout(action_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        card_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("选择一个 HAP 文件即可开始")
        self.status_label.setObjectName("status")
        card_layout.addWidget(self.status_label)

        self.log_title = QLabel("运行记录")
        self.log_title.setObjectName("logTitle")
        self.log_title.hide()
        card_layout.addWidget(self.log_title)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(300)
        self.log_view.setMinimumHeight(120)
        self.log_view.setFont(_fixed_width_font())
        self.log_view.hide()
        card_layout.addWidget(self.log_view)
        outer.addWidget(card)
        outer.addItem(
            QSpacerItem(
                0,
                0,
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Expanding,
            )
        )

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(15)

        icon_label = QLabel()
        icon_label.setPixmap(_make_app_icon(92).pixmap(56, 56))
        icon_label.setFixedSize(56, 56)
        header.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        title = QLabel("HapSign")
        title.setObjectName("title")
        subtitle = QLabel("签名并安装 HarmonyOS 应用")
        subtitle.setObjectName("subtitle")
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)
        header.addLayout(text_layout)
        header.addStretch(1)

        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.clicked.connect(self._open_settings)
        header.addWidget(self.settings_button)

        platform_chip = QLabel(f"{platform_tag().title()} · v{__version__}")
        platform_chip.setObjectName("chip")
        platform_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(platform_chip)
        return header

    def _build_file_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("QFrame { background: #f7f8fb; border-radius: 12px; }")
        panel.setFixedWidth(245)
        panel.setMinimumHeight(190)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(8)

        top_layout = QHBoxLayout()
        file_badge = QLabel("HAP")
        file_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        file_badge.setFixedSize(42, 42)
        file_badge.setStyleSheet(
            "color: #4459dc; background: #e7ebff; border-radius: 10px;"
            "font-size: 12px; font-weight: 750;"
        )
        top_layout.addWidget(file_badge)
        top_layout.addStretch(1)
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("removeButton")
        self.remove_button.setToolTip("移除当前 HAP")
        self.remove_button.setAccessibleName("移除当前 HAP")
        self.remove_button.clicked.connect(self._clear_file)
        top_layout.addWidget(self.remove_button)
        layout.addLayout(top_layout)

        self.file_name = QLabel()
        self.file_name.setObjectName("fileName")
        self.file_name.setWordWrap(True)
        self.file_meta = QLabel()
        self.file_meta.setObjectName("fileMeta")
        layout.addWidget(self.file_name)
        layout.addWidget(self.file_meta)
        layout.addStretch(1)

        self.signature_chip = QLabel()
        self.signature_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.signature_chip, 0, Qt.AlignmentFlag.AlignLeft)
        return panel

    @Slot()
    def _open_settings(self) -> None:
        if self.worker_thread is not None:
            return
        dialog = SettingsDialog(self.settings, self.log_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_settings()
        try:
            save_settings(updated)
            self.log_path = configure_file_logging(updated)
        except OSError as exc:
            QMessageBox.warning(self, "无法保存设置", str(exc))
            return
        self.settings = updated
        self.status_label.setStyleSheet("color: #237353; font-weight: 650;")
        self.status_label.setText("设置已保存，将用于下一次任务")

    @Slot()
    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 HAP 文件",
            str(Path.home()),
            "HarmonyOS HAP (*.hap);;所有文件 (*)",
        )
        if path:
            self._load_file(path)

    @Slot(str)
    def _load_file(self, raw_path: str) -> None:
        path = Path(raw_path).expanduser().resolve()
        try:
            if path.suffix.lower() != ".hap":
                raise ValueError("请选择扩展名为 .hap 的文件")
            if not path.is_file():
                raise ValueError("文件不存在或无法读取")
            bundle_name = detect_bundle_name(str(path))
            signed = is_hap_signed(path)
        except Exception as exc:
            QMessageBox.warning(self, "无法载入 HAP", str(exc))
            return

        self.selected_path = path
        self.status_label.setStyleSheet("")
        self.start_button.setText("开始签名并安装")
        self.file_name.setText(path.name)
        self.file_name.setToolTip(str(path))
        self.file_meta.setText(f"{bundle_name}  ·  {_human_size(path.stat().st_size)}")
        self.signature_chip.setText("已签名" if signed else "待签名")
        self.signature_chip.setObjectName("signedChip" if signed else "unsignedChip")
        self.signature_chip.style().unpolish(self.signature_chip)
        self.signature_chip.style().polish(self.signature_chip)
        self.file_panel.show()
        self.start_button.setEnabled(True)
        self.status_label.setText(
            "已签名，将直接安装" if signed else "未签名，将自动完成签名后安装"
        )

    @Slot()
    def _clear_file(self) -> None:
        if self.worker_thread is not None:
            return
        self.selected_path = None
        self.file_name.clear()
        self.file_name.setToolTip("")
        self.file_meta.clear()
        self.signature_chip.clear()
        self.file_panel.hide()
        self.start_button.setText("开始签名并安装")
        self.start_button.setEnabled(False)
        self.progress_bar.hide()
        self.log_title.hide()
        self.log_view.clear()
        self.log_view.hide()
        self.status_label.setStyleSheet("")
        self.status_label.setText("HAP 已移除，可重新选择或拖入文件")

    @Slot()
    def _check_device(self) -> None:
        if self.worker_thread is not None:
            return

        self.status_label.setStyleSheet("")
        self.status_label.setText("正在检测设备连接…")
        self.choose_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(5)
        self.progress_bar.show()
        self.cancel_button.show()
        self._cancel_requested = False

        thread = QThread(self)
        worker = DeviceCheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress_changed.connect(self._set_progress)
        worker.finished.connect(self._finish_device_check)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_finished)
        self.worker_thread = thread
        self.worker = worker
        thread.start()

    @Slot(bool, bool, str)
    def _finish_device_check(
        self, success: bool, cancelled: bool, message: str
    ) -> None:
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.status_label.setText(message)
        if success:
            self.status_label.setStyleSheet("color: #237353; font-weight: 650;")
        elif cancelled:
            self.progress_bar.setValue(0)
            self.status_label.setStyleSheet("color: #657084; font-weight: 650;")
        else:
            self.status_label.setStyleSheet("color: #b54646; font-weight: 650;")
            QMessageBox.warning(self, "未检测到可用设备", message)

    @Slot()
    def _start(self) -> None:
        if self.selected_path is None or self.worker_thread is not None:
            return

        self.status_label.setStyleSheet("")
        self.start_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.cancel_button.show()
        self._cancel_requested = False
        self.log_title.show()
        self.log_view.clear()
        self.log_view.show()
        self.status_label.setText("正在准备，请保持设备连接…")

        thread = QThread(self)
        worker = PipelineWorker(
            str(self.selected_path),
            signing_files_dir(self.settings),
            self.settings.browser_mode,
            self.settings.keep_signed_hap,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log_message.connect(self._append_log)
        worker.progress_changed.connect(self._set_progress)
        worker.finished.connect(self._finish)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_finished)
        self.worker_thread = thread
        self.worker = worker
        thread.start()

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.status_label.setText(message)

    @Slot(int, str)
    def _set_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, value)))
        if message:
            self.status_label.setText(message)

    @Slot()
    def _cancel_active_task(self) -> None:
        if self.worker is None or self._cancel_requested:
            return
        self._cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.status_label.setStyleSheet("color: #657084; font-weight: 650;")
        self.status_label.setText("正在取消并清理，请稍候…")
        request_cancel = getattr(self.worker, "request_cancel", None)
        if request_cancel is not None:
            request_cancel()

    @Slot(bool, bool, str)
    def _finish(self, success: bool, cancelled: bool, message: str) -> None:
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.status_label.setText(message)
        if success:
            self.status_label.setStyleSheet("color: #237353; font-weight: 650;")
            self.start_button.setText("再次安装")
        elif cancelled:
            self.progress_bar.setValue(0)
            self.status_label.setStyleSheet("color: #657084; font-weight: 650;")
            self.start_button.setText("重新开始")
        else:
            self.status_label.setStyleSheet("color: #b54646; font-weight: 650;")
            self.start_button.setText("重试")
            QMessageBox.critical(self, "未能完成安装", message)

    @Slot()
    def _thread_finished(self) -> None:
        self.worker_thread = None
        self.worker = None
        self.cancel_button.hide()
        self.cancel_button.setEnabled(True)
        self._cancel_requested = False
        self.start_button.setEnabled(self.selected_path is not None)
        self.choose_button.setEnabled(True)
        self.check_button.setEnabled(True)
        self.remove_button.setEnabled(self.selected_path is not None)
        self.drop_zone.setEnabled(True)
        self.settings_button.setEnabled(True)
        if self._close_after_cancel:
            self._close_after_cancel = False
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:
        if self.worker_thread is not None:
            if self._close_after_cancel:
                event.ignore()
                return
            answer = QMessageBox.question(
                self,
                "中断并退出？",
                "任务仍在执行。是否中断当前任务、完成清理后退出程序？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._close_after_cancel = True
                self._cancel_active_task()
            event.ignore()
            return
        event.accept()


def main() -> int:
    """启动桌面应用。"""
    settings = load_settings()
    log_path = configure_file_logging(settings)
    browser_smoke_mode = None
    if "--browser-smoke-test" in sys.argv:
        browser_smoke_mode = "playwright"
    elif "--system-browser-smoke-test" in sys.argv:
        browser_smoke_mode = "system_controlled"
    if browser_smoke_mode is not None:
        try:
            playwright_browser_smoke_test(browser_smoke_mode)
        except Exception:
            LOGGER.exception("受控浏览器自检失败：%s", browser_smoke_mode)
            return 1
        LOGGER.info("受控浏览器自检成功：%s", browser_smoke_mode)
        return 0

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("HapSign")
    app.setOrganizationName("HapSign")
    app.setWindowIcon(_make_app_icon())
    app.setStyle(HapSignStyle("Fusion"))
    app.setStyleSheet(WINDOW_STYLE)

    app.setFont(_application_font())

    window = MainWindow(settings, log_path)
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(250, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
