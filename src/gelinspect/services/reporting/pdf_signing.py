"""用 pyHanko 对 PDF 做标准 PAdES 签名（PAdES-B-B，SHA-256，增量更新）。

签名是离线的：没有时间戳服务，签名时间是签署人电脑的时钟，所以它证明“内容自签署后没有被
改动”，不证明“签署的绝对时间”。证书是本地自签的，没有第三方 CA 的法律效力。
"""

from __future__ import annotations

from io import BytesIO

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives import serialization
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields
from pyhanko.sign.signers import PdfSignatureMetadata, PdfSigner, SimpleSigner
from pyhanko_certvalidator.registry import SimpleCertificateStore

from gelinspect.services.reporting.keystore import SigningIdentity

SIGNATURE_FIELD = "GelInspectSignature"
"""签名域的名字。"""


def to_asn1(certificate_der: bytes) -> asn1_x509.Certificate:
    """把 DER 证书转成 pyHanko 使用的 asn1crypto 对象。

    Args:
        certificate_der: DER 编码的证书。

    Returns:
        asn1crypto 证书。
    """
    return asn1_x509.Certificate.load(certificate_der)


def sign_pdf_bytes(
    pdf: bytes, identity: SigningIdentity, *, signer_name: str, reason: str
) -> bytes:
    """给 PDF 加一个覆盖整个文件的 PAdES 签名。

    Args:
        pdf: 未签名的 PDF。
        identity: 签名身份。
        signer_name: 写进签名里的签署人名字。
        reason: 签署原因。

    Returns:
        已签名的 PDF（原文件后面追加签名，原内容不变）。

    Raises:
        Exception: pyHanko 的任何失败（PDF 不合法等）；调用方按“签署失败”处理。
    """
    certificate = to_asn1(identity.certificate.public_bytes(serialization.Encoding.DER))
    key = asn1_keys.PrivateKeyInfo.load(
        identity.private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    store = SimpleCertificateStore()  # type: ignore[no-untyped-call]
    store.register(certificate)
    signer = SimpleSigner(signing_cert=certificate, signing_key=key, cert_registry=store)
    metadata = PdfSignatureMetadata(
        field_name=SIGNATURE_FIELD,
        md_algorithm="sha256",
        subfilter=fields.SigSeedSubFilter.PADES,
        name=signer_name,
        reason=reason,
    )
    writer = IncrementalPdfFileWriter(BytesIO(pdf))
    out = PdfSigner(metadata, signer=signer).sign_pdf(writer)
    return bytes(out.getvalue())
