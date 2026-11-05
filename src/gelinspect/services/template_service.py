"""检测模板管理：列表、新建、复制、编辑，内置模板只读。

权限：所有角色可查看（``template_view``）；新建、复制、编辑需要 ``template_manage``（管理员）。
每次改动在同一事务里写审计（``template_changed``）。服务层再校验权限和内置只读，
界面被绕过也不能改内置模板。本模块不含药典判定规则，布局待确认的模板照常保存和使用。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from gelinspect.domain.entities import AuditEvent, User
from gelinspect.domain.enums import AuditAction, ExperimentType, TubeRole
from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import TUBE_COUNT
from gelinspect.domain.templates import ExperimentTemplate, TemplateTubeItem
from gelinspect.interfaces.storage import TemplateRepository, UnitOfWork
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode

MAX_NAME_LENGTH = 60
"""模板名称最大长度。"""

CUSTOM_PREFIX = "custom."
"""自定义模板标识的前缀，内置模板是 ``builtin.``。"""

_TYPE_ORDER = {kind: index for index, kind in enumerate(ExperimentType)}


class TemplateStorage(Protocol):
    """模板服务需要的存储。"""

    @property
    def templates(self) -> TemplateRepository:
        """模板仓储。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元。"""
        ...


@dataclass(frozen=True, slots=True)
class TemplateInfo:
    """列表里的一个模板。

    Attributes:
        template: 模板。
        builtin: 是否内置（只读）。
    """

    template: ExperimentTemplate
    builtin: bool


@dataclass(frozen=True, slots=True)
class TemplateItemInput:
    """编辑模板时一支试管的输入。

    Attributes:
        sample_name: 样品名称。
        role: 用途。
        dilution_factor: 稀释倍数，留空为 None。
        spiked_endotoxin: 外加内毒素，留空为 None。
    """

    sample_name: str
    role: TubeRole = TubeRole.TEST
    dilution_factor: float | None = None
    spiked_endotoxin: float | None = None


@dataclass(frozen=True, slots=True)
class TemplateDraft:
    """编辑模板的输入。

    Attributes:
        name: 名称。
        experiment_type: 类型。
        items: 每支试管，管号按顺序从 1 开始编。
    """

    name: str
    experiment_type: ExperimentType
    items: Sequence[TemplateItemInput]


def draft_of(template: ExperimentTemplate) -> TemplateDraft:
    """把模板转成可编辑的输入（编辑和复制的起点）。

    Args:
        template: 模板。

    Returns:
        输入，管位按管号升序。
    """
    ordered = sorted(template.items, key=lambda item: item.tube_no)
    return TemplateDraft(
        name=template.name,
        experiment_type=template.experiment_type,
        items=tuple(
            TemplateItemInput(i.sample_name, i.role, i.dilution_factor, i.spiked_endotoxin)
            for i in ordered
        ),
    )


class TemplateService:
    """模板管理服务。"""

    def __init__(self, auth: AuthService, storage: TemplateStorage) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用于校验权限。
            storage: 存储。
        """
        self._auth = auth
        self._storage = storage

    # ------------------------------------------------------------------ 查询

    def list_templates(self, session: Session | None) -> list[TemplateInfo]:
        """列出全部模板：内置在前（按试验类型顺序），自定义在后（按名称）。

        Args:
            session: 调用者的会话，需要 ``template_view`` 权限。

        Returns:
            模板列表。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.TEMPLATE_VIEW)
        repo = self._storage.templates
        infos = [TemplateInfo(t, repo.is_builtin(t.template_id)) for t in repo.list_all()]
        infos.sort(
            key=lambda i: (
                not i.builtin,
                _TYPE_ORDER[i.template.experiment_type] if i.builtin else 0,
                i.template.name,
            )
        )
        return infos

    def get_template(self, session: Session | None, template_id: str) -> TemplateInfo:
        """按标识取模板。

        Args:
            session: 调用者的会话，需要 ``template_view`` 权限。
            template_id: 模板标识。

        Returns:
            模板。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 模板不存在。
        """
        self._auth.authorize(session, Permission.TEMPLATE_VIEW)
        return self._require(template_id)

    # ------------------------------------------------------------------ 写入

    def create_template(self, session: Session | None, draft: TemplateDraft) -> ExperimentTemplate:
        """新建自定义模板。

        Args:
            session: 调用者的会话，需要 ``template_manage`` 权限。
            draft: 模板内容。

        Returns:
            保存后的模板。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 名称为空或重复，或内容不合法。
            StorageBusyError: 数据库忙，什么都没有写入。
        """
        user = self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        template = self._build(f"{CUSTOM_PREFIX}{self._new_id()}", draft)
        self._check_name(template.name, template.template_id)
        self._save(user, template, f"新建模板 {template.name}（{template.tube_count} 管）")
        return template

    def copy_template(
        self, session: Session | None, source_id: str, name: str | None = None
    ) -> ExperimentTemplate:
        """复制模板（内置或自定义）为新的自定义模板。

        复制品是用户自己的模板，不再带「布局待确认」标记。

        Args:
            session: 调用者的会话，需要 ``template_manage`` 权限。
            source_id: 源模板标识。
            name: 新名称，默认「原名称 副本」，重名时自动加序号。

        Returns:
            新模板。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 源模板不存在，或名称不合法。
            StorageBusyError: 数据库忙，什么都没有写入。
        """
        user = self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        source = self._require(source_id).template
        base = draft_of(source)
        new_name = (name or "").strip() or self._free_name(f"{source.name} 副本")
        draft = TemplateDraft(new_name, base.experiment_type, base.items)
        template = self._build(f"{CUSTOM_PREFIX}{self._new_id()}", draft)
        self._check_name(template.name, template.template_id)
        self._save(user, template, f"复制模板 {source.name} 为 {template.name}")
        return template

    def update_template(
        self, session: Session | None, template_id: str, draft: TemplateDraft
    ) -> ExperimentTemplate:
        """编辑自定义模板。内置模板只读。

        Args:
            session: 调用者的会话，需要 ``template_manage`` 权限。
            template_id: 模板标识。
            draft: 新内容。

        Returns:
            保存后的模板。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 模板不存在、是内置模板、名称不合法或重复，或内容不合法。
            StorageBusyError: 数据库忙，什么都没有写入。
        """
        user = self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        info = self._require(template_id)
        if info.builtin:
            raise ServiceError(
                f"内置模板 {info.template.name} 只读，请复制后再编辑",
                ServiceErrorCode.TEMPLATE_READ_ONLY,
                name=info.template.name,
            )
        template = self._build(template_id, draft)
        self._check_name(template.name, template_id)
        self._save(user, template, f"编辑模板 {template.name}（{template.tube_count} 管）")
        return template

    # ------------------------------------------------------------------ 内部

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _require(self, template_id: str) -> TemplateInfo:
        repo = self._storage.templates
        template = repo.get(template_id)
        if template is None:
            raise ServiceError(f"模板不存在：{template_id}", ServiceErrorCode.TEMPLATE_NOT_FOUND)
        return TemplateInfo(template, repo.is_builtin(template_id))

    def _check_name(self, name: str, own_id: str) -> None:
        for existing in self._storage.templates.list_all():
            if existing.template_id != own_id and existing.name == name:
                raise ServiceError(
                    f"已有同名模板：{name}", ServiceErrorCode.TEMPLATE_NAME_DUPLICATE, name=name
                )

    def _free_name(self, wanted: str) -> str:
        names = {t.name for t in self._storage.templates.list_all()}
        if wanted not in names:
            return wanted
        index = 2
        while f"{wanted} {index}" in names:
            index += 1
        return f"{wanted} {index}"

    def _build(self, template_id: str, draft: TemplateDraft) -> ExperimentTemplate:
        name = draft.name.strip()
        if not name:
            raise ServiceError("模板名称不能为空", ServiceErrorCode.TEMPLATE_NAME_REQUIRED)
        if len(name) > MAX_NAME_LENGTH:
            raise ServiceError(
                f"模板名称不能超过 {MAX_NAME_LENGTH} 个字符",
                ServiceErrorCode.TEMPLATE_NAME_TOO_LONG,
                max=MAX_NAME_LENGTH,
            )
        if not 1 <= len(draft.items) <= TUBE_COUNT:
            raise ServiceError(
                f"模板的试管数应在 1–{TUBE_COUNT} 内：{len(draft.items)}",
                ServiceErrorCode.TEMPLATE_TUBE_COUNT,
                max=TUBE_COUNT,
            )
        items: list[TemplateTubeItem] = []
        for index, item in enumerate(draft.items, start=1):
            sample = item.sample_name.strip()
            if not sample:
                raise ServiceError(
                    f"第 {index} 支试管的样品名称不能为空",
                    ServiceErrorCode.TEMPLATE_SAMPLE_NAME_REQUIRED,
                    index=index,
                )
            try:
                items.append(
                    TemplateTubeItem(
                        tube_no=index,
                        sample_name=sample,
                        role=item.role,
                        dilution_factor=item.dilution_factor,
                        spiked_endotoxin=item.spiked_endotoxin,
                    )
                )
            except ValueError as error:
                raise ServiceError(
                    f"第 {index} 支试管的数值不合法：{error}",
                    ServiceErrorCode.TEMPLATE_VALUE_INVALID,
                    index=index,
                ) from error
        return ExperimentTemplate(
            template_id=template_id,
            name=name,
            experiment_type=draft.experiment_type,
            items=tuple(items),
            needs_confirmation=False,
        )

    def _save(self, user: User, template: ExperimentTemplate, detail: str) -> None:
        event = AuditEvent(
            occurred_at=datetime.now(UTC),
            actor=user.username,
            actor_role=user.role,
            action=AuditAction.TEMPLATE_CHANGED,
            detail=f"{detail}［{template.template_id}］",
        )
        with self._storage.unit_of_work() as uow:
            uow.templates.save(template, builtin=False)
            uow.audit.append(event)
