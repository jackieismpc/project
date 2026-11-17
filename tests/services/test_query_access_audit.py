"""Explicit history searches are audited without auditing dashboard reads."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from gelinspect.domain.entities import User
from gelinspect.domain.enums import AuditAction, ExperimentType, Role
from gelinspect.domain.permissions import Permission
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.queries import QueryService


class _Auth:
    user = User("u-1", "viewer", "Viewer", Role.ADMIN)

    def authorize(self, session: object, permission: Permission) -> User:
        assert permission is Permission.EXPERIMENT_VIEW
        if session is None:
            raise PermissionDeniedError("请先登录")
        return self.user


class _Storage:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.experiments = self
        self.audit = self

    def query(self, _query: object, page: PageRequest) -> object:
        return SimpleNamespace(items=["one"], total=1, request=page)

    def append(self, event: object) -> object:
        self.events.append(event)
        return event


def test_search_records_filters_result_count_and_page() -> None:
    storage = _Storage()
    service = QueryService(
        cast(Any, _Auth()), cast(Any, storage), clock=lambda: datetime(2026, 11, 17, tzinfo=UTC)
    )
    query = ExperimentQuery(
        experiment_type=ExperimentType.GENERAL,
        state=ExperimentState.READY,
        batch_no="LOT-1",
    )

    result = service.search_experiments(
        cast(Any, object()), query, PageRequest(offset=25, limit=25)
    )

    assert len(result.items) == 1
    event = storage.events[0]
    assert event.action is AuditAction.EXPERIMENT_QUERIED
    assert "结果=成功" in event.detail and "LOT-1" in event.detail
    assert "偏移=25" in event.detail and "返回=1" in event.detail


def test_search_denial_is_recorded_with_attempt_context() -> None:
    storage = _Storage()
    service = QueryService(cast(Any, _Auth()), cast(Any, storage))

    with pytest.raises(PermissionDeniedError):
        service.search_experiments(None, ExperimentQuery(batch_no="LOT-2"), PageRequest())

    assert len(storage.events) == 1
    assert storage.events[0].action is AuditAction.EXPERIMENT_QUERIED
    assert "结果=拒绝" in storage.events[0].detail and "LOT-2" in storage.events[0].detail
