"""检测模板管理：列表、新建、复制、编辑，内置模板只读。

权限：所有角色可查看（``template_view``）；新建、复制、编辑需要 ``template_manage``（管理员）。
每次改动在同一事务里写审计（``template_changed``）。服务层再校验权限和内置只读，
界面被绕过也不能改内置模板。本模块不含药典判定规则，布局待确认的模板照常保存和使用。
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from gelinspect.domain.entities import AuditEvent, User
from gelinspect.domain.enums import AuditAction, ExperimentType, TubeRole
from gelinspect.domain.errors import InvalidPlatePositionError
from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import TUBE_COUNT, tube_to_position
from gelinspect.domain.templates import ExperimentTemplate, TemplateTubeItem
from gelinspect.interfaces.storage import ExperimentRepository, TemplateRepository, UnitOfWork
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

    @property
    def experiments(self) -> ExperimentRepository:
        """已保存试验仓储。"""
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
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class TemplateItemInput:
    """编辑模板时一支试管的输入。

    Attributes:
        sample_name: 样品名称。
        role: 用途。
        dilution_factor: 稀释倍数，留空为 None。
        spiked_endotoxin: 外加内毒素的绝对值，留空为 None。
        lambda_multiple: λ 的倍数，留空为 None；有值时优先于绝对值。
    """

    sample_name: str
    role: TubeRole = TubeRole.TEST
    dilution_factor: float | None = None
    spiked_endotoxin: float | None = None
    lambda_multiple: float | None = None


@dataclass(frozen=True, slots=True)
class TemplateDraft:
    """编辑模板的输入。

    Attributes:
        name: 名称。
        experiment_type: 类型。
        items: 每支试管的样品和判定配置。
        tube_numbers: 可选物理管号序列；省略时按 1 到 N 编号，提供时保留稀疏布局。
    """

    name: str
    experiment_type: ExperimentType
    items: Sequence[TemplateItemInput]
    tube_numbers: Sequence[int] | None = None


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
            TemplateItemInput(
                i.sample_name, i.role, i.dilution_factor, i.spiked_endotoxin, i.lambda_multiple
            )
            for i in ordered
        ),
        tube_numbers=tuple(item.tube_no for item in ordered),
    )


def build_template_config(template_id: str, draft: TemplateDraft) -> ExperimentTemplate:
    """统一构建并校验可复用模板或单次实验的试管配置。"""
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
    try:
        tube_numbers = (
            tuple(draft.tube_numbers)
            if draft.tube_numbers is not None
            else tuple(range(1, len(draft.items) + 1))
        )
    except TypeError as error:
        raise ServiceError("模板管位列表无效", ServiceErrorCode.TEMPLATE_VALUE_INVALID) from error
    if any(type(tube_no) is not int for tube_no in tube_numbers):
        raise ServiceError("模板管号必须是整数", ServiceErrorCode.TEMPLATE_VALUE_INVALID)
    if len(tube_numbers) != len(draft.items) or len(set(tube_numbers)) != len(tube_numbers):
        raise ServiceError(
            "模板管位必须唯一且与试管数一致", ServiceErrorCode.TEMPLATE_VALUE_INVALID
        )
    try:
        for tube_no in tube_numbers:
            tube_to_position(tube_no)
    except InvalidPlatePositionError as error:
        raise ServiceError(str(error), ServiceErrorCode.TEMPLATE_VALUE_INVALID) from error
    for index, (item, tube_no) in enumerate(zip(draft.items, tube_numbers, strict=True), start=1):
        sample = item.sample_name.strip()
        if not sample:
            raise ServiceError(
                f"第 {index} 支试管的样品名称不能为空",
                ServiceErrorCode.TEMPLATE_SAMPLE_NAME_REQUIRED,
                index=index,
            )
        values = (item.dilution_factor, item.spiked_endotoxin, item.lambda_multiple)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ServiceError(
                f"第 {index} 支试管的数值必须为有限数",
                ServiceErrorCode.TEMPLATE_VALUE_INVALID,
                index=index,
            )
        try:
            items.append(
                TemplateTubeItem(
                    tube_no,
                    sample,
                    item.role,
                    item.dilution_factor,
                    None if item.lambda_multiple is not None else item.spiked_endotoxin,
                    item.lambda_multiple,
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

    def template_draft_from_experiment(
        self, session: Session | None, experiment_id: str
    ) -> TemplateDraft:
        """基于试验自身快照构造可另存的模板，不读取当前模板库记录。

        Args:
            session: 调用者会话，需要模板管理和试验查看权限。
            experiment_id: 已保存试验标识。

        Returns:
            创建试验时保存的模板配置。

        Raises:
            ServiceError: 试验不存在或缺少创建时模板快照。
        """
        self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}", ServiceErrorCode.EXPERIMENT_NOT_FOUND
            )
        if experiment.template_snapshot is None:
            raise ServiceError(
                "该试验没有保存完整模板快照，不能另存为模板",
                ServiceErrorCode.EXPERIMENT_TEMPLATE_SNAPSHOT_MISSING,
            )
        return draft_of(experiment.template_snapshot)

    def list_templates(
        self, session: Session | None, *, include_hidden: bool = False
    ) -> list[TemplateInfo]:
        """列出全部模板：内置在前（按试验类型顺序），自定义在后（按名称）。

        Args:
            session: 调用者的会话，需要 ``template_view`` 权限。
            include_hidden: 是否包含隐藏模板，仅模板管理权限可用。

        Returns:
            模板列表。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.TEMPLATE_VIEW)
        if include_hidden:
            self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        repo = self._storage.templates
        infos = [
            TemplateInfo(t, repo.is_builtin(t.template_id), repo.is_hidden(t.template_id))
            for t in repo.list_all(include_hidden=include_hidden)
        ]
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

        复制品是用户自己的模板，不再带“布局待确认”标记。

        Args:
            session: 调用者的会话，需要 ``template_manage`` 权限。
            source_id: 源模板标识。
            name: 新名称，默认“原名称 副本”，重名时自动加序号。

        Returns:
            新模板。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 源模板不存在，或名称不合法。
            StorageBusyError: 数据库忙，什么都没有写入。
        """
        user = self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        source = self._require(source_id).template
        if self._storage.templates.is_hidden(source_id):
            raise ServiceError("模板已隐藏，恢复后才能复制", ServiceErrorCode.TEMPLATE_NOT_FOUND)
        base = draft_of(source)
        new_name = (name or "").strip() or self._free_name(f"{source.name} 副本")
        draft = TemplateDraft(new_name, base.experiment_type, base.items, base.tube_numbers)
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

    def delete_template(self, session: Session | None, template_id: str) -> None:
        """软删除自建模板并记录审计；内置模板不能删除。"""
        user = self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        info = self._require(template_id)
        if info.builtin:
            raise ServiceError("内置模板只能隐藏，不能删除", ServiceErrorCode.TEMPLATE_READ_ONLY)
        self._set_lifecycle(
            user,
            template_id,
            deleted=True,
            detail=f"删除自建模板 {info.template.name}",
        )

    def set_builtin_hidden(
        self, session: Session | None, template_id: str, *, hidden: bool
    ) -> None:
        """隐藏或恢复内置模板。"""
        user = self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        info = self._require(template_id, allow_hidden=True)
        if not info.builtin:
            raise ServiceError("只有内置模板可以隐藏或恢复", ServiceErrorCode.TEMPLATE_READ_ONLY)
        self._set_lifecycle(
            user,
            template_id,
            hidden=hidden,
            detail=f"{'隐藏' if hidden else '恢复'}内置模板 {info.template.name}",
        )

    def list_hidden_builtins(self, session: Session | None) -> list[ExperimentTemplate]:
        """列出可恢复的内置模板，需要模板管理权限。"""
        self._auth.authorize(session, Permission.TEMPLATE_MANAGE)
        repo = self._storage.templates
        hidden = repo.list_all(include_hidden=True)
        visible_ids = {t.template_id for t in repo.list_all()}
        return [
            template
            for template in hidden
            if repo.is_builtin(template.template_id) and template.template_id not in visible_ids
        ]

    def _set_lifecycle(
        self,
        user: User,
        template_id: str,
        *,
        detail: str,
        hidden: bool | None = None,
        deleted: bool = False,
    ) -> None:
        event = AuditEvent(
            occurred_at=datetime.now(UTC),
            actor=user.username,
            actor_role=user.role,
            action=AuditAction.TEMPLATE_CHANGED,
            detail=f"{detail}［{template_id}］",
        )
        with self._storage.unit_of_work() as uow:
            uow.templates.set_visibility(
                template_id,
                hidden=hidden,
                deleted=deleted,
                deleted_at=datetime.now(UTC) if deleted else None,
                deleted_by=user.username if deleted else None,
            )
            uow.audit.append(event)

    # ------------------------------------------------------------------ 内部

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _require(self, template_id: str, *, allow_hidden: bool = False) -> TemplateInfo:
        repo = self._storage.templates
        template = repo.get(template_id)
        if template is None:
            raise ServiceError(f"模板不存在：{template_id}", ServiceErrorCode.TEMPLATE_NOT_FOUND)
        if repo.is_deleted(template_id):
            raise ServiceError(f"模板已删除：{template_id}", ServiceErrorCode.TEMPLATE_NOT_FOUND)
        if repo.is_hidden(template_id) and not allow_hidden:
            raise ServiceError(f"模板已隐藏：{template_id}", ServiceErrorCode.TEMPLATE_NOT_FOUND)
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
        return build_template_config(template_id, draft)

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
