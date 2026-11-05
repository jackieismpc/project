"""报告中心的服务：预览、签署、验签、导出和单批次报告。

* 每个入口先校验权限：预览、验签、打印需要 ``report_view``；签署需要 ``experiment_sign``；
  导出需要 ``data_export``。UI 不碰数据库，只通过这里取数据。
* **签署**是一个原子操作：任一步失败都不留下半成品，试验状态不变（见 :meth:`ReportService.sign`）。
* 已签署的试验锁定：状态是「已签署」，状态机不再接受任何事件，复核服务拒绝修改
  （``review_service``）；同一份签名文件可以反复导出。
* 签名是本地自签证书的，只能「防篡改」，没有第三方 CA 的法律效力。
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import re
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage, QPagedPaintDevice

from gelinspect.domain.conclusions import ExperimentConclusion
from gelinspect.domain.entities import AuditEvent, Experiment, User
from gelinspect.domain.enums import (
    AuditAction,
    BatchKind,
    ReportScope,
)
from gelinspect.domain.permissions import Permission
from gelinspect.domain.signing import SigningBlock, SigningBlocker, signing_blocks
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import (
    AuditQuery,
    AuditRepository,
    ExperimentQuery,
    ExperimentRepository,
    ExperimentSummary,
    PageRequest,
    ReportRecord,
    ReportRepository,
    StateConflictError,
    TelemetryRepository,
    UnitOfWork,
    UserRepository,
)
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.encrypted_zip import EncryptedZipError, write_encrypted_zip
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.export_service import ExportPolicy
from gelinspect.services.media_service import MediaService
from gelinspect.services.monitor_service import audit_event
from gelinspect.services.reporting.document import (
    ExperimentSection,
    ReportDocument,
    RowImagePair,
    build_section,
)
from gelinspect.services.reporting.keystore import (
    KeyStore,
    SigningIdentity,
    certificate_fingerprint,
)
from gelinspect.services.reporting.pdf_signing import sign_pdf_bytes
from gelinspect.services.reporting.renderer import ReportRenderer
from gelinspect.services.reporting.strings import S
from gelinspect.services.reporting.texts import ReportLanguage, ReportTexts
from gelinspect.services.reporting.verify import (
    SignatureVerification,
    VerifyFailure,
    verify_pdf_bytes,
)
from gelinspect.services.video.paths import experiment_dir

_log = logging.getLogger(__name__)

_STARTED_MARK = f"{ExperimentState.READY.value} → {ExperimentState.INCUBATING.value}"
"""审计里「开始孵育」那条状态变化的开头，与监控页取开始时间的方式一致。"""
_BATCH_DIR = "_batch"
"""单批次报告放在 ``reports/_batch/``；以下划线开头，不会与试验标识的目录重名。"""
THUMBNAIL_LONG_SIDE = 900
"""8 连图缩略图的长边（像素）。"""
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class Signer(Protocol):
    """给 PDF 加数字签名的函数，测试里可以替换成会失败的实现。"""

    def __call__(
        self, pdf: bytes, identity: SigningIdentity, *, signer_name: str, reason: str
    ) -> bytes:
        """签名并返回已签名的 PDF。"""
        ...


class ReportStorage(Protocol):
    """报告服务需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储。"""
        ...

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...

    @property
    def reports(self) -> ReportRepository:
        """报告记录仓储。"""
        ...

    @property
    def users(self) -> UserRepository:
        """用户仓储。"""
        ...

    @property
    def telemetry(self) -> TelemetryRepository:
        """温度日志仓储。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元。"""
        ...


class SigningNotAllowedError(ServiceError):
    """现在不能签署，带上全部原因。

    Attributes:
        blocks: 不能签署的原因。
        conclusion: 试验的结论（用于说明无效或待定的原因），没有时为 None。
    """

    def __init__(
        self, blocks: tuple[SigningBlock, ...], conclusion: ExperimentConclusion | None = None
    ) -> None:
        """创建异常。

        Args:
            blocks: 不能签署的原因，非空。
            conclusion: 试验的结论。
        """
        names = "、".join(block.blocker.value for block in blocks)
        super().__init__(f"现在不能签署：{names}", ServiceErrorCode.SIGN_NOT_ALLOWED)
        self.blocks = blocks
        self.conclusion = conclusion


@dataclass(frozen=True, slots=True)
class ReportPreview:
    """一份报告的预览：内容、语言和能否签署。

    Attributes:
        document: 报告内容。
        texts: 报告语言的文字。
        scope: 单个试验或单批次。
        experiment_id: 试验标识；单批次报告为 None。
        blocks: 不能签署的原因，空表示可以签署。
        conclusion: 单个试验的结论；单批次报告为 None。
        record: 已签署的报告记录（最新一份），没有为 None。
        digest: 页脚的内容摘要。
    """

    document: ReportDocument
    texts: ReportTexts
    scope: ReportScope
    experiment_id: str | None
    blocks: tuple[SigningBlock, ...]
    conclusion: ExperimentConclusion | None
    record: ReportRecord | None
    digest: str

    @property
    def can_sign(self) -> bool:
        """现在能否签署。"""
        return not self.blocks

    @property
    def language(self) -> ReportLanguage:
        """报告语言。"""
        return self.texts.language


@dataclass(frozen=True, slots=True)
class SignedReport:
    """一次签署的结果。

    Attributes:
        record: 入库的报告记录。
        path: 签名文件的绝对路径。
        pages: 页数。
        verification: 签署后立即做的自检结果（必定通过，否则签署失败）。
    """

    record: ReportRecord
    path: Path
    pages: int
    verification: SignatureVerification


@dataclass(frozen=True, slots=True)
class ExportFailure:
    """导出时没能导出的一份报告。

    Attributes:
        report_id: 报告标识。
        reason: ``missing``（文件不存在）、``hash_mismatch``（与库里的哈希不符）、
            ``unsigned``（没有签名）或 ``not_found``（没有这条记录）。
    """

    report_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ExportResult:
    """批量导出的结果。

    Attributes:
        directory: 导出目录。
        exported: 已导出的文件数。
        failures: 没能导出的报告。
        manifest: 清单 CSV 的路径；加密导出时清单在 ZIP 里，这里是 ZIP 的路径。
        elapsed_s: 用时（秒）。
        archive: 加密导出时生成的 ZIP（AES-256），不加密时为 None。
    """

    directory: Path
    exported: int
    failures: tuple[ExportFailure, ...]
    manifest: Path
    elapsed_s: float
    archive: Path | None = None


def bgr_to_qimage(image: NDArray[np.uint8]) -> QImage:
    """把 BGR 数组转成独立拷贝的 ``QImage``（RGB888）。

    Args:
        image: H×W×3 的 BGR 图。

    Returns:
        不依赖原数组内存的图像。
    """
    rgb = np.ascontiguousarray(image[:, :, ::-1])
    height, width = rgb.shape[:2]
    return QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()


def _safe_part(value: str, limit: int = 40) -> str:
    return _SAFE.sub("_", value)[:limit] or "_"


class ReportService:
    """报告的预览、签署、验签、打印记录和导出。"""

    def __init__(
        self,
        auth: AuthService,
        storage: ReportStorage,
        *,
        data_dir: Path,
        keystore: KeyStore,
        media: MediaService | None = None,
        copyright_holder: str = "[版权方名称]",
        include_images: bool = False,
        renderer: ReportRenderer | None = None,
        signer: Signer | None = None,
        clock: Callable[[], datetime] | None = None,
        zone: tzinfo | None = None,
        export_policy: ExportPolicy | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用来校验权限和再次验证密码。
            storage: 存储。
            data_dir: 数据目录，报告放在 ``<data_dir>/reports/<试验>/``。
            keystore: 签名证书和私钥。
            media: 读取 8 连图用的媒体服务；没有时报告不能附图。
            copyright_holder: 版权声明的权利人（配置，默认占位文字）。
            include_images: 报告是否默认附 8 连图缩略页。
            renderer: 排版输出，测试可注入。
            signer: PDF 签名函数，测试可注入会失败的实现。
            clock: 返回带时区当前时间的函数，测试注入。
            zone: 报告里显示时间用的时区，默认本机时区。
            export_policy: 批量导出的加密策略（与 Excel 导出共用），默认不要求加密。
        """
        self._auth = auth
        self._export_policy = export_policy or ExportPolicy()
        self._storage = storage
        self._data_dir = data_dir
        self._keystore = keystore
        self._media = media
        self._holder = copyright_holder
        self._include_images = include_images
        self._renderer = renderer or ReportRenderer(zone=zone)
        self._signer: Signer = signer or sign_pdf_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._zone = zone

    # ------------------------------------------------------------------ 查询

    @property
    def copyright_holder(self) -> str:
        """版权声明里的权利人。"""
        return self._holder

    def set_copyright_holder(self, holder: str) -> None:
        """更换版权声明里的权利人（仪器设置保存后调用），之后生成的报告采用新名称。

        已签署的报告不受影响：它们的内容已经固化在签名文件里。

        Args:
            holder: 新的权利人名称。
        """
        self._holder = holder

    @property
    def images_available(self) -> bool:
        """是否能附 8 连图（有媒体服务）。"""
        return self._media is not None

    def certificate_info(self, session: Session | None) -> tuple[str, str] | None:
        """本机签名证书的主题和 SHA-256 指纹（不会生成证书）。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。

        Returns:
            ``(主题, 指纹)``；还没有证书时为 None。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        from gelinspect.services.reporting.keystore import certificate_subject

        self._auth.authorize(session, Permission.REPORT_VIEW)
        certificate = self._keystore.trusted_certificate()
        if certificate is None:
            return None
        return certificate_subject(certificate), certificate_fingerprint(certificate)

    def report_experiments(
        self, session: Session | None, limit: int = 200
    ) -> list[ExperimentSummary]:
        """可以出报告的试验：已完成和已签署的，新的在前。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            limit: 最多条数。

        Returns:
            试验摘要。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.REPORT_VIEW)
        items: list[ExperimentSummary] = []
        for state in (ExperimentState.COMPLETED, ExperimentState.SIGNED):
            page = self._storage.experiments.query(
                ExperimentQuery(state=state), PageRequest(limit=limit)
            )
            items.extend(page.items)
        items.sort(key=lambda item: (item.created_at, item.experiment_id), reverse=True)
        return items[:limit]

    def signed_reports(self, session: Session | None, *, limit: int = 200) -> list[ReportRecord]:
        """已签署的报告，新的在前。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            limit: 最多条数。

        Returns:
            报告记录。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.REPORT_VIEW)
        return self._storage.reports.list_recent(signed_only=True, limit=limit)

    def batch_numbers(self, session: Session | None, kind: BatchKind) -> list[str]:
        """有已完成试验的批号，用于选批次。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            kind: 鲎试剂批号或样品批号。

        Returns:
            批号，升序。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.REPORT_VIEW)
        return self._storage.experiments.batch_numbers(kind, _REPORTABLE)

    def batch_reports(
        self, session: Session | None, kind: BatchKind, batch_no: str
    ) -> list[ReportRecord]:
        """某批号已生成的单批次报告。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            kind: 批号种类。
            batch_no: 批号。

        Returns:
            报告记录，按生成时间排序。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.REPORT_VIEW)
        return self._storage.reports.list_for_batch(kind, batch_no)

    # ------------------------------------------------------------------ 预览

    def preview(
        self,
        session: Session | None,
        experiment_id: str,
        language: ReportLanguage = ReportLanguage.ZH_CN,
        *,
        with_images: bool | None = None,
    ) -> ReportPreview:
        """单个试验的报告预览。

        已签署的试验始终用签署时的语言，和已签署的文件内容一致。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            experiment_id: 试验标识。
            language: 报告语言（未签署时有效）。
            with_images: 是否附 8 连图，默认取配置。

        Returns:
            预览，``blocks`` 说明现在能否签署。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 试验不存在。
        """
        self._auth.authorize(session, Permission.REPORT_VIEW)
        experiment = self._load(experiment_id)
        record = self._latest_signed(experiment_id)
        if record is not None:
            language = ReportLanguage(record.language)
        section = self._section(experiment)
        images: dict[str, tuple[RowImagePair, ...]] = {}
        if self._wants_images(with_images) and session is not None:
            images[experiment_id] = self._images(session, experiment_id)
        document = ReportDocument(
            scope=ReportScope.EXPERIMENT,
            sections=(section,),
            copyright_holder=self._holder,
            signed_by=section.signer_name,
            signed_at=experiment.signed_at,
            images=images,
        )
        return ReportPreview(
            document=document,
            texts=ReportTexts(language),
            scope=ReportScope.EXPERIMENT,
            experiment_id=experiment_id,
            blocks=signing_blocks(experiment),
            conclusion=experiment.conclusion,
            record=record,
            digest=document.content_digest(),
        )

    def batch_preview(
        self,
        session: Session | None,
        kind: BatchKind,
        batch_no: str,
        language: ReportLanguage = ReportLanguage.ZH_CN,
    ) -> ReportPreview:
        """单批次报告预览：汇总该批号下全部已完成的试验。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            kind: 批号种类。
            batch_no: 批号。
            language: 报告语言。

        Returns:
            预览；其中有试验还没有签署时 ``blocks`` 说明不能签署。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 这个批号下没有已完成的试验。
        """
        self._auth.authorize(session, Permission.REPORT_VIEW)
        experiments = self._batch_members(kind, batch_no)
        sections = tuple(self._section(experiment) for experiment in experiments)
        document = ReportDocument(
            scope=ReportScope.BATCH,
            sections=sections,
            copyright_holder=self._holder,
            batch_kind=kind,
            batch_no=batch_no,
        )
        unsigned = tuple(
            e.experiment_id for e in experiments if e.state is not ExperimentState.SIGNED
        )
        blocks = (
            (SigningBlock(SigningBlocker.NOT_COMPLETED, state=ExperimentState.COMPLETED),)
            if unsigned
            else ()
        )
        return ReportPreview(
            document=document,
            texts=ReportTexts(language),
            scope=ReportScope.BATCH,
            experiment_id=None,
            blocks=blocks,
            conclusion=None,
            record=None,
            digest=document.content_digest(),
        )

    def paint(self, preview: ReportPreview, device: QPagedPaintDevice) -> int:
        """把预览画到打印机或预览控件的设备上。

        Args:
            preview: 预览。
            device: 分页设备（``QPrinter``）。

        Returns:
            页数。
        """
        return self._renderer.paint(preview.document, preview.texts, device)

    def record_print(self, session: Session | None, preview: ReportPreview) -> None:
        """写一条「打印」审计。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            preview: 打印的报告。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        user = self._auth.authorize(session, Permission.REPORT_VIEW)
        target = preview.experiment_id or f"{preview.document.batch_no}"
        self._audit_best_effort(
            AuditEvent(
                occurred_at=self._clock(),
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.REPORT_PRINTED,
                detail=(
                    f"打印报告 {target}（{'已签署' if preview.document.signed else '未签署'}，"
                    f"内容摘要 {preview.digest}）"
                ),
                experiment_id=preview.experiment_id,
            )
        )

    # ------------------------------------------------------------------ 签署

    def sign(
        self,
        session: Session | None,
        experiment_id: str,
        password: str,
        language: ReportLanguage = ReportLanguage.ZH_CN,
        *,
        with_images: bool | None = None,
    ) -> SignedReport:
        """签署试验并生成带数字签名的 PDF。

        步骤：校验权限 → 检查签署前提 → 再次验证密码 → 写入签署人和时间 → 生成 PDF →
        PAdES 签名并自检 → 落盘 → 同一个工作单元里更新状态为「已签署」、写签署人和时间、
        报告记录（含文件 SHA-256）和审计。**任一步失败都不留半成品**：文件被删除，数据库不变，
        试验仍是「已完成」。

        Args:
            session: 调用者的会话，需要 ``experiment_sign`` 权限。
            experiment_id: 试验标识。
            password: 签署人再次输入的密码。
            language: 报告语言。
            with_images: 是否附 8 连图，默认取配置。

        Returns:
            签署结果。

        Raises:
            PermissionDeniedError: 没有权限。
            SigningNotAllowedError: 有待复核、结论待定或无效、状态不对。
            InvalidCredentialsError: 密码错误（计入登录失败次数，写审计）。
            ServiceError: 试验不存在、签署失败（``SIGN_FAILED``）、
                试验被别处改过（``SIGN_CONFLICT``）。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_SIGN)
        assert session is not None
        experiment = self._load(experiment_id)
        blocks = signing_blocks(experiment)
        if blocks:
            raise SigningNotAllowedError(blocks, experiment.conclusion)
        self._auth.confirm_password(session, password, purpose="签署")
        before = experiment.state
        now = self._clock()
        experiment.sign(user.username, now)
        images: dict[str, tuple[RowImagePair, ...]] = {}
        final: Path | None = None
        staged: Path | None = None
        try:
            if self._wants_images(with_images):
                images[experiment_id] = self._images(session, experiment_id)
            section = self._section(experiment, signer=user)
            document = ReportDocument(
                scope=ReportScope.EXPERIMENT,
                sections=(section,),
                copyright_holder=self._holder,
                signed_by=user.display_name,
                signed_at=now,
                images=images,
            )
            texts = ReportTexts(language)
            directory = experiment_dir(self._data_dir, "reports", experiment_id)
            name = f"{experiment_id}.pdf"
            signed, pages, identity = self._render_and_sign(
                document, texts, signer_name=user.display_name, reason_id=experiment_id
            )
            final = directory / name
            if final.exists():
                raise FileExistsError(f"报告文件已存在，不覆盖：{final.name}")
            staged = self._stage(directory, name, signed)
        except Exception as exc:
            self._discard(staged, final)
            _log.exception("签署试验 %s 失败，没有生成报告", experiment_id)
            raise ServiceError(
                f"签署失败，没有生成报告，试验状态没有改变：{exc}",
                ServiceErrorCode.SIGN_FAILED,
                reason=str(exc)[:200],
            ) from exc
        record = ReportRecord(
            report_id=uuid.uuid4().hex,
            experiment_id=experiment_id,
            path=self._relative(final),
            sha256=hashlib.sha256(signed).hexdigest(),
            created_by=user.username,
            created_at=now,
            signed=True,
            scope=ReportScope.EXPERIMENT,
            language=language.value,
            signed_by=user.username,
            signed_at=now,
            certificate_sha256=identity.fingerprint,
            content_sha256=document.content_digest(),
            experiment_ids=(experiment_id,),
        )
        verdict = experiment.conclusion.verdict.value if experiment.conclusion else "-"
        events = [
            audit_event(
                user,
                AuditAction.EXPERIMENT_STATE_CHANGED,
                f"{before.value} → {experiment.state.value}（sign）：签署人 {user.username}",
                experiment_id,
                now,
            ),
            audit_event(
                user,
                AuditAction.EXPERIMENT_SIGNED,
                f"签署试验并生成报告 {name}：结论 {verdict}；语言 {language.value}；"
                f"文件 SHA-256 {record.sha256}；内容摘要 {record.content_sha256}；"
                f"证书指纹 {record.certificate_sha256}",
                experiment_id,
                now,
            ),
        ]
        try:
            with self._storage.unit_of_work() as uow:
                uow.experiments.update_state(experiment_id, experiment.state, expected=before)
                uow.experiments.record_signature(
                    experiment_id, signed_by=user.username, signed_at=now
                )
                uow.reports.add(record)
                uow.audit.append_many(events)
                # 文件放到最终位置是提交前的最后一步：之后只剩提交，提交失败时下面删除它。
                os.replace(staged, final)
        except StateConflictError as exc:
            self._discard(staged, final)
            raise ServiceError(
                "试验状态已被其他操作改变，请刷新后重试", ServiceErrorCode.SIGN_CONFLICT
            ) from exc
        except BaseException:
            self._discard(staged, final)
            raise
        verification = verify_pdf_bytes(signed, identity.certificate, expected_sha256=record.sha256)
        return SignedReport(record=record, path=final, pages=pages, verification=verification)

    def sign_batch(
        self,
        session: Session | None,
        kind: BatchKind,
        batch_no: str,
        password: str,
        language: ReportLanguage = ReportLanguage.ZH_CN,
    ) -> SignedReport:
        """签署单批次报告。只有批次里全部试验都已签署时才能签署。

        单批次报告是已签署试验的汇总，本身不改变任何试验的状态。

        Args:
            session: 调用者的会话，需要 ``experiment_sign`` 权限。
            kind: 批号种类。
            batch_no: 批号。
            password: 签署人再次输入的密码。
            language: 报告语言。

        Returns:
            签署结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 批次里没有试验、有试验未签署（``BATCH_NOT_SIGNABLE``）或签署失败。
            InvalidCredentialsError: 密码错误。
            StorageBusyError: 数据库忙。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_SIGN)
        assert session is not None
        experiments = self._batch_members(kind, batch_no)
        if any(e.state is not ExperimentState.SIGNED for e in experiments):
            raise ServiceError(
                "单批次报告只能在其中全部试验都已签署后签署", ServiceErrorCode.BATCH_NOT_SIGNABLE
            )
        self._auth.confirm_password(session, password, purpose="签署单批次报告")
        now = self._clock()
        report_id = uuid.uuid4().hex
        final: Path | None = None
        staged: Path | None = None
        try:
            sections = tuple(self._section(experiment) for experiment in experiments)
            document = ReportDocument(
                scope=ReportScope.BATCH,
                sections=sections,
                copyright_holder=self._holder,
                signed_by=user.display_name,
                signed_at=now,
                batch_kind=kind,
                batch_no=batch_no,
            )
            signed, _pages, identity = self._render_and_sign(
                document,
                ReportTexts(language),
                signer_name=user.display_name,
                reason_id=f"{kind.value}:{batch_no}",
            )
            directory = self._batch_dir(kind, batch_no)
            name = f"batch_{_safe_part(kind.value)}_{_safe_part(batch_no)}_{report_id[:8]}.pdf"
            final = directory / name
            staged = self._stage(directory, name, signed)
        except Exception as exc:
            self._discard(staged, final)
            _log.exception("签署单批次报告 %s 失败", batch_no)
            raise ServiceError(
                f"签署失败，没有生成报告：{exc}",
                ServiceErrorCode.SIGN_FAILED,
                reason=str(exc)[:200],
            ) from exc
        record = ReportRecord(
            report_id=report_id,
            experiment_id=None,
            path=self._relative(final),
            sha256=hashlib.sha256(signed).hexdigest(),
            created_by=user.username,
            created_at=now,
            signed=True,
            scope=ReportScope.BATCH,
            batch_kind=kind,
            batch_no=batch_no,
            language=language.value,
            signed_by=user.username,
            signed_at=now,
            certificate_sha256=identity.fingerprint,
            content_sha256=document.content_digest(),
            experiment_ids=tuple(e.experiment_id for e in experiments),
        )
        event = AuditEvent(
            occurred_at=now,
            actor=user.username,
            actor_role=user.role,
            action=AuditAction.REPORT_SIGNED,
            detail=(
                f"签署单批次报告 {name}：{kind.value} {batch_no}，试验 "
                f"{'、'.join(record.experiment_ids)}；文件 SHA-256 {record.sha256}；"
                f"内容摘要 {record.content_sha256}；证书指纹 {record.certificate_sha256}"
            ),
        )
        try:
            with self._storage.unit_of_work() as uow:
                uow.reports.add(record)
                uow.audit.append(event)
                os.replace(staged, final)
        except BaseException:
            self._discard(staged, final)
            raise
        verification = verify_pdf_bytes(signed, identity.certificate, expected_sha256=record.sha256)
        return SignedReport(record=record, path=final, pages=_pages, verification=verification)

    # ------------------------------------------------------------------ 验签

    def verify_report(self, session: Session | None, report_id: str) -> SignatureVerification:
        """重新验签一份已入库的报告：签名、证书、是否覆盖整个文件，以及文件 SHA-256。

        结果（包括失败）写审计。文件被篡改、被替换、丢失都会得到明确原因。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            report_id: 报告标识。

        Returns:
            验签结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 没有这条报告记录。
        """
        user = self._auth.authorize(session, Permission.REPORT_VIEW)
        record = self._storage.reports.get(report_id)
        if record is None:
            raise ServiceError(f"报告不存在：{report_id}", ServiceErrorCode.REPORT_NOT_FOUND)
        path = self.resolve(record)
        try:
            data = path.read_bytes()
        except OSError:
            result = SignatureVerification(
                ok=False,
                failures=(VerifyFailure.FILE_MISSING,),
                signature_intact=False,
                file_sha256="",
            )
        else:
            result = verify_pdf_bytes(
                data, self._keystore.trusted_certificate(), expected_sha256=record.sha256
            )
        self._audit_verification(user, record.experiment_id, path.name, result)
        return result

    def verify_file(self, session: Session | None, path: Path) -> SignatureVerification:
        """验证任意一个 PDF 文件（例如别处拷来的报告）。不核对文件哈希，只验签名和证书。

        Args:
            session: 调用者的会话，需要 ``report_view`` 权限。
            path: PDF 文件。

        Returns:
            验签结果。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        user = self._auth.authorize(session, Permission.REPORT_VIEW)
        try:
            data = path.read_bytes()
        except OSError:
            result = SignatureVerification(
                ok=False,
                failures=(VerifyFailure.FILE_MISSING,),
                signature_intact=False,
                file_sha256="",
            )
        else:
            result = verify_pdf_bytes(data, self._keystore.trusted_certificate())
        self._audit_verification(user, None, path.name, result)
        return result

    # ------------------------------------------------------------------ 导出

    def export_report_file(
        self, session: Session | None, report_id: str, destination: Path
    ) -> Path:
        """把一份已签署报告导出为指定文件（同一份签名文件，可反复导出）。

        Args:
            session: 调用者的会话，需要 ``data_export`` 权限。
            report_id: 报告标识。
            destination: 目标文件路径，已存在时覆盖（用户在保存对话框里已确认）。

        Returns:
            写出的文件路径。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 没有这条记录、文件缺失或与库里的哈希不符（拒绝导出被改动的文件）。
        """
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        record = self._storage.reports.get(report_id)
        if record is None or not record.signed:
            raise ServiceError(f"报告不存在：{report_id}", ServiceErrorCode.REPORT_NOT_FOUND)
        data = self._read_checked(record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        self._audit_best_effort(
            AuditEvent(
                occurred_at=self._clock(),
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.REPORT_EXPORTED,
                detail=f"导出报告 {Path(record.path).name} → {destination.name}"
                f"（SHA-256 {record.sha256}）",
                experiment_id=record.experiment_id,
            )
        )
        return destination

    def export_reports(
        self,
        session: Session | None,
        report_ids: Sequence[str],
        directory: Path,
        *,
        password: str | None = None,
    ) -> ExportResult:
        """批量导出已签署报告到目录：复制 PDF，并写一份清单 CSV。

        每份文件先与库里的 SHA-256 核对：被改动或缺失的不导出，在清单里标出原因。
        给出密码时，PDF 和清单不落成明文，而是打进目录里的一个 AES-256 加密 ZIP。

        Args:
            session: 调用者的会话，需要 ``data_export`` 权限。
            report_ids: 要导出的报告标识。
            directory: 目标目录，不存在时创建。
            password: 导出密码；开启了导出加密时必须给出。

        Returns:
            导出结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 没有选择任何报告（``EXPORT_EMPTY``）、目录无法写入（``EXPORT_FAILED``）。
        """
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        self._export_policy.check(password)
        if not report_ids:
            raise ServiceError("没有可导出的报告", ServiceErrorCode.EXPORT_EMPTY)
        started = time.perf_counter()
        packed: list[tuple[str, Path | bytes]] = []
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ServiceError(
                f"无法创建导出目录：{exc}", ServiceErrorCode.EXPORT_FAILED, reason=str(exc)
            ) from exc
        rows: list[list[str]] = []
        failures: list[ExportFailure] = []
        exported = 0
        used: set[str] = set()
        for report_id in report_ids:
            record = self._storage.reports.get(report_id)
            if record is None or not record.signed:
                failures.append(ExportFailure(report_id, "not_found"))
                rows.append([report_id, "", "", "", "", "", "", "", "", "", "not_found"])
                continue
            name = _unique_name(Path(record.path).name, used)
            status = "ok"
            try:
                data = self._read_checked(record)
                if password is None:
                    (directory / name).write_bytes(data)
                else:
                    packed.append((name, data))
                exported += 1
            except ServiceError as exc:
                status = (
                    "missing"
                    if exc.code is ServiceErrorCode.REPORT_FILE_MISSING
                    else "hash_mismatch"
                )
                failures.append(ExportFailure(report_id, status))
            except OSError as exc:
                raise ServiceError(
                    f"写入导出目录失败：{exc}", ServiceErrorCode.EXPORT_FAILED, reason=str(exc)
                ) from exc
            rows.append(
                [
                    record.report_id,
                    record.scope.value,
                    record.experiment_id or "",
                    record.batch_no or "",
                    name if status == "ok" else "",
                    record.sha256,
                    record.signed_by or "",
                    record.signed_at.isoformat() if record.signed_at else "",
                    record.language,
                    record.content_sha256 or "",
                    status,
                ]
            )
        archive: Path | None = None
        if password is None:
            manifest = self._write_manifest(directory, rows)
        else:
            archive = directory / f"reports_{self._clock():%Y%m%d_%H%M%S}.zip"
            packed.append(("manifest.csv", self._manifest_bytes(rows)))
            try:
                write_encrypted_zip(archive, packed, password)
            except (OSError, EncryptedZipError) as exc:
                raise ServiceError(
                    f"写入导出目录失败：{exc}", ServiceErrorCode.EXPORT_FAILED, reason=str(exc)
                ) from exc
            manifest = archive
        elapsed = time.perf_counter() - started
        self._audit_best_effort(
            AuditEvent(
                occurred_at=self._clock(),
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.REPORT_EXPORTED,
                detail=(
                    f"批量导出报告：{exported} 份成功，{len(failures)} 份失败，"
                    f"目录 {directory.name}"
                    + ("" if archive is None else f"，已加密（AES-256 ZIP {archive.name}）")
                ),
            )
        )
        return ExportResult(directory, exported, tuple(failures), manifest, elapsed, archive)

    def resolve(self, record: ReportRecord) -> Path:
        """报告文件的绝对路径（记录里存的是相对数据目录的路径）。

        Args:
            record: 报告记录。

        Returns:
            绝对路径。
        """
        path = Path(record.path)
        return path if path.is_absolute() else self._data_dir / path

    # ------------------------------------------------------------------ 内部

    def _load(self, experiment_id: str) -> Experiment:
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            )
        return experiment

    def _latest_signed(self, experiment_id: str) -> ReportRecord | None:
        signed = [
            record
            for record in self._storage.reports.list_for_experiment(experiment_id)
            if record.signed
        ]
        return signed[-1] if signed else None

    def _batch_members(self, kind: BatchKind, batch_no: str) -> list[Experiment]:
        ids = self._storage.experiments.experiment_ids_for_batch(kind, batch_no, _REPORTABLE)
        experiments = [e for e in (self._storage.experiments.get(i) for i in ids) if e is not None]
        if not experiments:
            raise ServiceError("这个批号下没有已完成的试验", ServiceErrorCode.BATCH_EMPTY)
        return experiments

    def _display_name(self, username: str | None) -> str | None:
        if username is None:
            return None
        found = self._storage.users.get_by_username(username)
        return found.display_name if found is not None else username

    def _started_at(self, experiment_id: str) -> datetime | None:
        page = self._storage.audit.query(
            AuditQuery(experiment_id=experiment_id, action=AuditAction.EXPERIMENT_STATE_CHANGED),
            PageRequest(limit=1000),
        )
        times = [
            r.event.occurred_at for r in page.items if r.event.detail.startswith(_STARTED_MARK)
        ]
        return min(times) if times else None

    def _section(self, experiment: Experiment, *, signer: User | None = None) -> ExperimentSection:
        signer_name = (
            signer.display_name if signer is not None else self._display_name(experiment.signed_by)
        )
        return build_section(
            experiment,
            operator_name=self._display_name(experiment.operator) or experiment.operator,
            signer_name=signer_name,
            started_at=self._started_at(experiment.experiment_id),
            temperature_range=self._storage.telemetry.temperature_range(experiment.experiment_id),
        )

    def _wants_images(self, flag: bool | None) -> bool:
        return self._media is not None and (self._include_images if flag is None else flag)

    def _images(self, session: Session, experiment_id: str) -> tuple[RowImagePair, ...]:
        media = self._media
        if media is None:
            return ()
        pairs: list[RowImagePair] = []
        for row in media.rows_with_images(session, experiment_id):
            thumbnails = media.row_thumbnails(
                session, experiment_id, row, long_side=THUMBNAIL_LONG_SIDE
            )
            pairs.append(
                RowImagePair(
                    row_index=row,
                    before=None if thumbnails.before is None else bgr_to_qimage(thumbnails.before),
                    after=None if thumbnails.after is None else bgr_to_qimage(thumbnails.after),
                )
            )
        return tuple(pairs)

    def _render_and_sign(
        self, document: ReportDocument, texts: ReportTexts, *, signer_name: str, reason_id: str
    ) -> tuple[bytes, int, SigningIdentity]:
        """排版、签名并自检。自检不过就抛异常，绝不交出验不过的文件。"""
        pdf, pages = self._renderer.render_pdf(document, texts)
        identity = self._keystore.load_or_create()
        signed = self._signer(
            pdf, identity, signer_name=signer_name, reason=texts.s(S.SIGN_REASON, id=reason_id)
        )
        check = verify_pdf_bytes(signed, identity.certificate)
        if not check.ok:
            raise RuntimeError(
                "刚生成的签名没有通过自检：" + "、".join(f.value for f in check.failures)
            )
        return signed, pages, identity

    @staticmethod
    def _stage(directory: Path, name: str, data: bytes) -> Path:
        """先写成同目录下的临时文件并落盘，提交成功后才改成正式名字。"""
        directory.mkdir(parents=True, exist_ok=True)
        staged = directory / f".{name}.part"
        with staged.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return staged

    @staticmethod
    def _discard(staged: Path | None, final: Path | None) -> None:
        """删除签署失败留下的文件。正式文件只在本次签署刚放进去时才会存在，所以可以删。"""
        for path in (staged, final):
            if path is not None:
                with suppress(OSError):
                    path.unlink(missing_ok=True)

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self._data_dir).as_posix()
        except ValueError:
            return str(path)

    def _batch_dir(self, kind: BatchKind, batch_no: str) -> Path:
        digest = hashlib.sha256(f"{kind.value}:{batch_no}".encode()).hexdigest()[:8]
        directory = (
            self._data_dir
            / "reports"
            / _BATCH_DIR
            / (f"{_safe_part(kind.value)}_{_safe_part(batch_no)}_{digest}")
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _read_checked(self, record: ReportRecord) -> bytes:
        """读报告文件并核对库里的 SHA-256；不符不放行。"""
        path = self.resolve(record)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ServiceError(
                f"报告文件不存在：{path}", ServiceErrorCode.REPORT_FILE_MISSING, path=str(path)
            ) from exc
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise ServiceError(
                f"报告文件与入库的 SHA-256 不符，可能已被改动：{path.name}",
                ServiceErrorCode.REPORT_HASH_MISMATCH,
                name=path.name,
            )
        return data

    @staticmethod
    def _write_manifest(directory: Path, rows: list[list[str]]) -> Path:
        path = directory / "manifest.csv"
        if path.exists():
            path = directory / f"manifest_{datetime.now(UTC):%Y%m%d_%H%M%S}.csv"
        path.write_bytes(ReportService._manifest_bytes(rows))
        return path

    @staticmethod
    def _manifest_bytes(rows: list[list[str]]) -> bytes:
        """清单 CSV 的内容（UTF-8 带 BOM，Excel 能直接打开）。"""
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "report_id",
                "scope",
                "experiment_id",
                "batch_no",
                "file",
                "sha256",
                "signed_by",
                "signed_at",
                "language",
                "content_sha256",
                "status",
            ]
        )
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8-sig")

    def _audit_verification(
        self,
        user: User,
        experiment_id: str | None,
        name: str,
        result: SignatureVerification,
    ) -> None:
        outcome = "通过" if result.ok else "未通过：" + "、".join(f.value for f in result.failures)
        self._audit_best_effort(
            AuditEvent(
                occurred_at=self._clock(),
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.REPORT_VERIFIED,
                detail=f"验签 {name}：{outcome}",
                experiment_id=experiment_id,
            )
        )

    def _audit_best_effort(self, event: AuditEvent) -> None:
        """写审计；数据库忙时只记日志（导出、打印、验签的结果已经确定，不因此回滚）。"""
        try:
            self._storage.audit.append(event)
        except Exception:
            _log.exception("审计没有写入：%s %s", event.action.value, event.detail)


_REPORTABLE = (ExperimentState.COMPLETED, ExperimentState.SIGNED)
"""可以出报告的试验状态。"""


def _unique_name(name: str, used: set[str]) -> str:
    """导出目录里不重名：同名时加序号。"""
    candidate, stem, suffix, counter = name, Path(name).stem, Path(name).suffix, 1
    while candidate in used:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used.add(candidate)
    return candidate
