"""报告界面的文字：不能签署的原因、验签结果的说明。

和 ``labels.py`` 一样：服务层给出稳定的分类，界面按当前语言生成提示。
"""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from gelinspect.domain.conclusions import ExperimentConclusion
from gelinspect.domain.signing import SigningBlock, SigningBlocker
from gelinspect.services.reporting.verify import SignatureVerification, VerifyFailure
from gelinspect.ui.labels import conclusion_note_text, experiment_state_label
from gelinspect.ui.punctuation import join_items

_BLOCKS: dict[SigningBlocker, str] = {
    SigningBlocker.ALREADY_SIGNED: str(
        QT_TRANSLATE_NOOP("Signing", "试验已签署，结果和复核已锁定，不能再修改。")
    ),
    SigningBlocker.NOT_COMPLETED: str(
        QT_TRANSLATE_NOOP("Signing", "试验还没有完成（当前状态：{state}），完成并复核后才能签署。")
    ),
    SigningBlocker.PENDING_REVIEW: str(
        QT_TRANSLATE_NOOP("Signing", "{tubes} 号管等待人工复核，复核全部完成后才能签署。")
    ),
    SigningBlocker.RESULT_MISSING: str(QT_TRANSLATE_NOOP("Signing", "{tubes} 号管还没有结果。")),
    SigningBlocker.NO_CONCLUSION: str(
        QT_TRANSLATE_NOOP("Signing", "这个试验没有入库的结论，不能签署。")
    ),
    SigningBlocker.VERDICT_PENDING: str(
        QT_TRANSLATE_NOOP(
            "Signing",
            "试验结论为「待定」，不是最终结论，不能签署：未判定的结果不能被当成放行依据。",
        )
    ),
    SigningBlocker.VERDICT_INVALID: str(
        QT_TRANSLATE_NOOP(
            "Signing",
            "试验结论为「无效」，不能签署：不得据此放行。请处理下面的无效原因后重新做试验。",
        )
    ),
}

_BATCH_UNSIGNED = str(
    QT_TRANSLATE_NOOP("Signing", "批次里还有试验没有签署，全部签署后才能签署单批次报告。")
)


def describe_signing_blocks(
    blocks: tuple[SigningBlock, ...],
    conclusion: ExperimentConclusion | None = None,
    *,
    batch: bool = False,
) -> list[str]:
    """不能签署的原因，每条一行，当前语言。

    结论为「无效」或「待定」时，把结论里的原因逐条列在后面，让操作人知道为什么。

    Args:
        blocks: 不能签署的原因。
        conclusion: 试验的结论（单个试验），用来说明无效或待定的原因。
        batch: 是否单批次报告。

    Returns:
        文字行。
    """
    lines: list[str] = []
    if batch:
        return [QCoreApplication.translate("Signing", _BATCH_UNSIGNED)] if blocks else []
    for block in blocks:
        template = QCoreApplication.translate("Signing", _BLOCKS[block.blocker])
        state = "" if block.state is None else experiment_state_label(block.state)
        lines.append(template.format(tubes=join_items(block.tubes), state=state))
        if (
            block.blocker in (SigningBlocker.VERDICT_INVALID, SigningBlocker.VERDICT_PENDING)
            and conclusion is not None
        ):
            lines.extend(f"· {conclusion_note_text(note)}" for note in conclusion.unmet)
    return lines


_FAILURES: dict[VerifyFailure, str] = {
    VerifyFailure.FILE_MISSING: str(
        QT_TRANSLATE_NOOP("Verify", "报告文件不存在，可能已被删除或移走。")
    ),
    VerifyFailure.FILE_HASH_MISMATCH: str(
        QT_TRANSLATE_NOOP("Verify", "文件的 SHA-256 与库里记录的不一致：文件被替换或改动过。")
    ),
    VerifyFailure.UNREADABLE: str(
        QT_TRANSLATE_NOOP("Verify", "文件无法解析：不是合法的 PDF，或内容已被破坏。")
    ),
    VerifyFailure.NOT_SIGNED: str(QT_TRANSLATE_NOOP("Verify", "文件没有数字签名。")),
    VerifyFailure.MULTIPLE_SIGNATURES: str(
        QT_TRANSLATE_NOOP("Verify", "文件含有多个签名，本软件生成的报告只有一个签名。")
    ),
    VerifyFailure.CONTENT_MODIFIED: str(
        QT_TRANSLATE_NOOP("Verify", "文件内容与签名不符：签署之后被修改过。")
    ),
    VerifyFailure.SIGNATURE_INVALID: str(QT_TRANSLATE_NOOP("Verify", "签名本身无效。")),
    VerifyFailure.NOT_ENTIRE_FILE: str(
        QT_TRANSLATE_NOOP("Verify", "签名没有覆盖整个文件：签署之后被追加过内容。")
    ),
    VerifyFailure.UNTRUSTED_CERTIFICATE: str(
        QT_TRANSLATE_NOOP("Verify", "签名证书不是本机信任的那张（可能是别处签的文件）。")
    ),
    VerifyFailure.NO_LOCAL_CERTIFICATE: str(
        QT_TRANSLATE_NOOP("Verify", "本机还没有签名证书，无法判断证书是否可信。")
    ),
}

_VERIFY_OK = str(
    QT_TRANSLATE_NOOP(
        "Verify",
        "验签通过：签名完整、覆盖整个文件，证书是本机信任的那张，文件与库内记录一致。",
    )
)
_VERIFY_OK_EXTERNAL = str(
    QT_TRANSLATE_NOOP("Verify", "验签通过：签名完整、覆盖整个文件，证书是本机信任的那张。")
)
_VERIFY_FAILED = str(QT_TRANSLATE_NOOP("Verify", "验签未通过："))
SELF_SIGNED_NOTICE = str(
    QT_TRANSLATE_NOOP(
        "Verify",
        "签名用的是本机自签证书：能证明文件签署后没有被改动，但不具有第三方 CA 的法律效力。",
    )
)
"""验签区常驻的说明：自签证书防篡改，没有第三方 CA 的法律效力。"""


def describe_failure(failure: VerifyFailure) -> str:
    """一项验签失败的当前语言说明。

    Args:
        failure: 失败原因。

    Returns:
        说明文字。
    """
    return QCoreApplication.translate("Verify", _FAILURES[failure])


def describe_verification(result: SignatureVerification, *, stored: bool = True) -> list[str]:
    """验签结果的当前语言说明，第一行是结论。

    Args:
        result: 验签结果。
        stored: 是否已入库的报告（还核对了库里的文件哈希）；外部文件为 False。

    Returns:
        文字行：通过时一行；未通过时第一行是「验签未通过」，其后每个原因一行。
    """
    if result.ok:
        return [QCoreApplication.translate("Verify", _VERIFY_OK if stored else _VERIFY_OK_EXTERNAL)]
    return [
        QCoreApplication.translate("Verify", _VERIFY_FAILED),
        *(f"· {describe_failure(f)}" for f in result.failures),
    ]


def self_signed_notice() -> str:
    """自签证书的说明，当前语言。

    Returns:
        说明文字。
    """
    return QCoreApplication.translate("Verify", SELF_SIGNED_NOTICE)
