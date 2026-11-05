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
    RESUME_NOT_ALLOWED = "resume_not_allowed"
    RETRY_NOT_ALLOWED = "retry_not_allowed"
    LAMBDA_REQUIRED = "lambda_required"
    MEDIA_MISSING = "media_missing"
    MEDIA_TAMPERED = "media_tampered"
    EXPERIMENT_SIGNED = "experiment_signed"
    SIGN_NOT_ALLOWED = "sign_not_allowed"
    SIGN_FAILED = "sign_failed"
    SIGN_CONFLICT = "sign_conflict"
    REPORT_NOT_FOUND = "report_not_found"
    REPORT_FILE_MISSING = "report_file_missing"
    REPORT_HASH_MISMATCH = "report_hash_mismatch"
    BATCH_EMPTY = "batch_empty"
    BATCH_NOT_SIGNABLE = "batch_not_signable"
    EXPORT_EMPTY = "export_empty"
    EXPORT_FAILED = "export_failed"
    EXPORT_PASSWORD_REQUIRED = "export_password_required"
    EXPORT_PASSWORD_WEAK = "export_password_weak"
    USER_NOT_FOUND = "user_not_found"
    USER_DUPLICATE = "user_duplicate"
    USER_LAST_ADMIN = "user_last_admin"
    USER_SELF_DISABLE = "user_self_disable"
    USER_SELF_RESET = "user_self_reset"
    USER_NAME_REQUIRED = "user_name_required"
    SETTINGS_INVALID = "settings_invalid"
    SETTINGS_BUSY = "settings_busy"
    SETTINGS_SAVE_FAILED = "settings_save_failed"
    SETTINGS_PREVIEW_FAILED = "settings_preview_failed"
    LIMITS_INVALID = "limits_invalid"
    AUDIT_QUERY_INVALID = "audit_query_invalid"


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
