"""跨线程任务取消原语。"""

from __future__ import annotations

import threading


class OperationCancelled(Exception):
    """用户主动取消当前操作。"""


def raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """取消信号已设置时立即中断当前流程。"""
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled("操作已取消")
