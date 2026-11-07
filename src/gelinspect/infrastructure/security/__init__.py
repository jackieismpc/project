"""安全相关的具体实现：密钥保护、审计链密钥、程序完整性清单。

* :mod:`.protectors`：``KeyProtector`` 的三种实现（Windows DPAPI、macOS 钥匙串、
  安装密钥派生的口令加密）和按平台选择的工厂。
* :mod:`.audit_keys`：审计哈希链的 HMAC 密钥环，由 ``KeyProtector`` 保护。
* :mod:`.integrity`：发布包文件清单（SHA-256 + Ed25519 签名）的生成与校验。

这些措施是“基础级别”的防护：提高篡改和拷贝的门槛，不承诺绝对安全，
能防什么、不能防什么见 ``docs/features/security.md``。
"""
