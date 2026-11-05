"""业务服务的错误分类。

异常信息是简体中文（写审计和日志用）；界面按 :class:`ServiceErrorCode` 和参数重新生成
当前语言的提示，与账号相关的错误（``services.auth.AuthError``）做法一致。
"""

from __future__ import annotations

from enum import StrEnum


class ServiceErrorCode(StrEnum):
    """业务错误的分类，界面据此生成提示。"""

    TEMPLATE_NOT_FOUND = "template_not_found"
    TEMPLATE_READ_ONLY = "template_read_only"
    TEMPLATE_NAME_REQUIRED = "template_name_required"
    TEMPLATE_NAME_DUPLICATE = "template_name_duplicate"
    TEMPLATE_NAME_TOO_LONG = "template_name_too_long"
    TEMPLATE_TUBE_COUNT = "template_tube_count"
    TEMPLATE_SAMPLE_NAME_REQUIRED = "template_sample_name_required"
    TEMPLATE_VALUE_INVALID = "template_value_invalid"
    DRAFT_TEMPLATE_MISSING = "draft_template_missing"
    DRAFT_COUNT_MISMATCH = "draft_count_mismatch"
    DRAFT_TUBES_INVALID = "draft_tubes_invalid"
    DRAFT_SAMPLE_NAME_REQUIRED = "draft_sample_name_required"
    REAGENT_LOCKED = "reagent_locked"
    EXPERIMENT_NOT_FOUND = "experiment_not_found"
    EXPERIMENT_NOT_READY = "experiment_not_ready"
    ENGINE_BUSY = "engine_busy"
    ENGINE_REFUSED = "engine_refused"
    STOP_NOT_ALLOWED = "stop_not_allowed"
    TUBE_NOT_FOUND = "tube_not_found"
    REVIEW_NOTE_REQUIRED = "review_note_required"
    REVIEW_NOT_ALLOWED = "review_not_allowed"
    REVIEW_CONCLUSION_INVALID = "review_conclusion_invalid"
    REVIEW_CONFLICT = "review_conflict"
    MEDIA_MISSING = "media_missing"
    MEDIA_TAMPERED = "media_tampered"


class ServiceError(Exception):
    """业务服务错误基类。

    Attributes:
        code: 错误分类。
        params: 提示文字里的参数。
    """

    def __init__(self, message: str, code: ServiceErrorCode, **params: str | float) -> None:
        """创建异常。

        Args:
            message: 简体中文说明。
            code: 错误分类。
            **params: 提示文字里的参数。
        """
        super().__init__(message)
        self.code = code
        self.params = params
