"""领域层异常。"""

from __future__ import annotations


class DomainError(Exception):
    """领域规则被违反时抛出的异常基类。"""


class InvalidPlatePositionError(DomainError, ValueError):
    """管号、排号或孔号超出 8×10 孔板范围。"""


class IllegalTransitionError(DomainError):
    """当前状态不接受该事件，即状态转换表中没有这条转换。

    Attributes:
        state: 发生非法转换时的状态值。
        event: 被拒绝的事件值。
    """

    def __init__(self, state: str, event: str) -> None:
        """记录被拒绝的状态和事件。

        Args:
            state: 当前状态值。
            event: 被拒绝的事件值。
        """
        super().__init__(f"状态 {state!r} 不接受事件 {event!r}")
        self.state = state
        self.event = event


class TransitionGuardError(DomainError):
    """转换本身合法，但业务前置条件不满足，例如仍有待复核的试管时签署。"""
