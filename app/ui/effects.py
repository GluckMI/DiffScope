"""轻量界面动效：受设置中的「减少动效」控制，默认关闭时只做最短淡入。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation

_reduce_motion = False
_running: list[QPropertyAnimation] = []


def set_reduce_motion(enabled: bool) -> None:
    global _reduce_motion
    _reduce_motion = bool(enabled)


def reduce_motion() -> bool:
    return _reduce_motion


def fade_in(widget, duration: int = 160) -> None:
    """对话框出现时的短淡入；开启「减少动效」时完全跳过。"""
    if _reduce_motion or widget is None:
        return
    try:
        animation = QPropertyAnimation(widget, b"windowOpacity")
        animation.setDuration(duration)
        animation.setStartValue(0.86)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: _running.remove(animation)
                                  if animation in _running else None)
        _running.append(animation)
        animation.start()
    except (RuntimeError, AttributeError):
        pass
