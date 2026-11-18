"""试验模板仓储。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from gelinspect.domain.enums import ExperimentType, TubeRole
from gelinspect.domain.templates import ExperimentTemplate, TemplateTubeItem
from gelinspect.infrastructure.db.models import TemplateItemRow, TemplateRow
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    translate_lock_errors,
)
from gelinspect.interfaces.storage import TemplateRepository


def _to_template(row: TemplateRow, items: list[TemplateItemRow]) -> ExperimentTemplate:
    """ORM 行转领域模板。"""
    return ExperimentTemplate(
        template_id=row.template_id,
        name=row.name,
        experiment_type=ExperimentType(row.experiment_type),
        items=tuple(
            TemplateTubeItem(
                tube_no=item.tube_no,
                sample_name=item.sample_name,
                role=TubeRole(item.role),
                dilution_factor=item.dilution_factor,
                spiked_endotoxin=item.spiked_endotoxin,
                lambda_multiple=item.lambda_multiple,
            )
            for item in sorted(items, key=lambda item: item.tube_no)
        ),
        needs_confirmation=row.needs_confirmation,
        created_by=row.created_by,
        created_at=row.created_at,
    )


class SqlTemplateRepository(TemplateRepository):
    """基于 SQLAlchemy 的模板仓储。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    def save(self, template: ExperimentTemplate, *, builtin: bool = False) -> None:
        """新增或覆盖模板。

        Args:
            template: 模板。
            builtin: 是否内置模板。
        """
        with translate_lock_errors(), self._sessions() as session:
            row = session.get(TemplateRow, template.template_id)
            if row is None:
                row = TemplateRow(template_id=template.template_id)
                session.add(row)
            row.name = template.name
            row.experiment_type = template.experiment_type.value
            row.needs_confirmation = template.needs_confirmation
            row.created_by = template.created_by
            row.created_at = template.created_at
            row.builtin = builtin
            session.execute(
                delete(TemplateItemRow).where(TemplateItemRow.template_id == template.template_id)
            )
            session.flush()
            session.add_all(
                TemplateItemRow(
                    template_id=template.template_id,
                    tube_no=item.tube_no,
                    sample_name=item.sample_name,
                    role=item.role.value,
                    dilution_factor=item.dilution_factor,
                    spiked_endotoxin=item.spiked_endotoxin,
                    lambda_multiple=item.lambda_multiple,
                )
                for item in template.items
            )

    def get(self, template_id: str) -> ExperimentTemplate | None:
        """按标识查询模板。

        Args:
            template_id: 模板标识。

        Returns:
            模板，不存在时为 None。
        """
        with self._sessions() as session:
            row = session.get(TemplateRow, template_id)
            if row is None:
                return None
            items = list(
                session.execute(
                    select(TemplateItemRow).where(TemplateItemRow.template_id == template_id)
                ).scalars()
            )
            return _to_template(row, items)

    def list_all(
        self,
        experiment_type: ExperimentType | None = None,
        *,
        include_hidden: bool = False,
        include_deleted: bool = False,
    ) -> list[ExperimentTemplate]:
        """列出模板，可按试验类型过滤，按标识排序。

        Args:
            experiment_type: 试验类型，None 表示全部。
            include_hidden: 是否包含已隐藏模板。
            include_deleted: 是否包含已软删除模板。

        Returns:
            模板列表。
        """
        statement = select(TemplateRow).order_by(TemplateRow.template_id)
        if not include_hidden:
            statement = statement.where(TemplateRow.hidden.is_(False))
        if not include_deleted:
            statement = statement.where(TemplateRow.deleted_at.is_(None))
        if experiment_type is not None:
            statement = statement.where(TemplateRow.experiment_type == experiment_type.value)
        with self._sessions() as session:
            rows = list(session.execute(statement).scalars())
            items = session.execute(select(TemplateItemRow)).scalars()
            by_template: dict[str, list[TemplateItemRow]] = {}
            for item in items:
                by_template.setdefault(item.template_id, []).append(item)
            return [_to_template(row, by_template.get(row.template_id, [])) for row in rows]

    def set_visibility(
        self,
        template_id: str,
        *,
        hidden: bool | None = None,
        deleted: bool = False,
        deleted_at: datetime | None = None,
        deleted_by: str | None = None,
    ) -> None:
        """更新模板的隐藏或软删除标记。"""
        with translate_lock_errors(), self._sessions() as session:
            row = session.get(TemplateRow, template_id)
            if row is None:
                return
            if hidden is not None:
                row.hidden = hidden
            if deleted:
                row.deleted_at = deleted_at
                row.deleted_by = deleted_by
            elif deleted_at is None:
                row.deleted_at = None
                row.deleted_by = None

    def is_builtin(self, template_id: str) -> bool:
        """模板是否为内置模板。

        Args:
            template_id: 模板标识。

        Returns:
            内置返回 True，不存在返回 False。
        """
        with self._sessions() as session:
            row = session.get(TemplateRow, template_id)
            return row is not None and row.builtin

    def is_hidden(self, template_id: str) -> bool:
        """模板当前已隐藏时返回 True。"""
        with self._sessions() as session:
            row = session.get(TemplateRow, template_id)
            return row is not None and row.hidden

    def is_deleted(self, template_id: str) -> bool:
        """自建模板已软删除时返回 True。"""
        with self._sessions() as session:
            row = session.get(TemplateRow, template_id)
            return row is not None and row.deleted_at is not None
