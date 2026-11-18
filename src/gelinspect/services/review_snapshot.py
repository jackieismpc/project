"""为影响报告的人工复核提供并发检查快照。"""

from __future__ import annotations

from gelinspect.domain.entities import Experiment


def review_snapshot(experiment: Experiment) -> tuple[tuple[int, tuple[object, ...]], ...]:
    """按管号排序，返回完整的已保存复核历史。"""
    return tuple(
        (
            tube_no,
            tuple(
                (entry.interpretation, entry.reviewed_by, entry.reviewed_at, entry.reason)
                for entry in result.review_history
            ),
        )
        for tube_no, result in sorted(experiment.results.items())
    )
