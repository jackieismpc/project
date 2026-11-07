# 安全与基础防护（security）

对应阶段：P13。代码：`interfaces/security.py`（`KeyProtector` 协议）、`infrastructure/security/`（`protectors.py` 密钥保护、`audit_keys.py` 审计链密钥环、`integrity.py` 文件清单）、`infrastructure/db/repositories/audit.py`（HMAC 分段）、迁移 `rev_0005_audit_chain_segments.py`、`services/encrypted_zip.py`（AES ZIP）、`services/export_service.py`（导出加密策略）、`services/license.py`（授权接口）、`services/runtime_paths.py`（运行模式和路径）、`infrastructure/unconfigured.py`（发布包的占位后端）、`startup.py`（启动时校验和 `--diagnose`）；界面 `ui/pages/export_password.py`、设置页“导出加密”、审计页的链类型说明。打包见 [deployment.md](../deployment.md)。

## 目的

研发计划要求“基础防反编译、防拷贝、防破解”和“分层加密与完整性校验”。本阶段做到**合理的基础级别**：提高篡改、窃取和伪造的门槛，让改动留下痕迹；**不承诺绝对安全**。面向部署人员和管理员。

### 能防什么

| 威胁 | 措施 | 效果 |
|---|---|---|
| 拷走数据目录里的签名私钥，在别处伪造签名报告 | 私钥由 `KeyProtector` 加密：Windows DPAPI（绑定 Windows 用户）、macOS 钥匙串（主密钥在登录钥匙串里） | 只复制文件解不开；要在原电脑、原系统用户下才能用 |
| 直接改数据库里的审计记录，再重算整条 SHA-256 链和链头 | 审计链从切换点起改用 HMAC-SHA256，密钥不在数据库里 | 没有密钥重算不出正确的哈希；校验报告 `hash_mismatch` |
| 把链“降级”回纯 SHA-256（删掉分段记录后整条重算） | 切换点同时记在受保护的密钥文件里，以它为准 | 校验报告 `segment_mismatch` |
| 导出的 Excel、批量报告在传递中被他人打开 | 可选的 AES-256 加密 ZIP（WinZip AE-2），默认关闭，设置页可开 | 没有密码打不开；内容被改认证码不过 |
| 替换、篡改发布包里的库、资源、迁移脚本、翻译、配置 | 构建时生成 SHA-256 清单并用 Ed25519 签名，公钥编译进主程序；启动时校验 | 不一致时弹出提示并写审计（`integrity_checked`），不阻止使用 |
| 直接阅读或修改 Python 源码 | Nuitka 把全部 Python 代码编译成本机代码，发布包里没有 `.py`（迁移脚本除外，Alembic 要按文件加载） | 比 `.pyc` 难还原得多，但不是不可能 |
| 带演示账号的版本流到正式环境 | 正式包默认排除 `simulation/`（构建时检查编译报告里没有它），配置里没有 `bootstrap` | 首次启动只能“创建管理员”，没有任何默认密码 |

### 不能防什么

- **有本机管理员或同一系统用户权限的人**：DPAPI 和钥匙串保护的是“文件离开这台电脑或这个用户”的情况。同一用户下运行的程序（包括恶意程序）可以调用同样的接口解密。
- **调试器、内存转储**：运行中的私钥和 HMAC 密钥在内存里是明文。
- **替换整个主程序**：公钥在主程序里，换掉主程序等于换掉校验者。macOS 上主程序在加入清单后重新做了 ad-hoc 签名，所以不在清单里，由系统代码签名覆盖；但 ad-hoc 签名任何人都能重做。
- **同时删除密钥文件并重写整个数据库**：没有密钥文件时，软件只能相信数据库的状态，会开新的一段 HMAC 链。删掉密钥文件本身会被发现（旧分段报“密钥不可用”），但“删密钥 + 把整条链改写成纯 SHA-256”这个组合发现不了。异地备份密钥目录和数据库的哈希可以缓解。
- **安装密钥回退模式**（见下）：安装密钥和密文在同一个目录，能读整个数据目录的人就能解密。
- **反编译的决心**：Nuitka 编译产物仍可被逆向工程分析；“防破解”只做到基础级别。
- **授权**：只留了接口，默认不启用，即使启用也不锁定任何功能（见下文）。
- **数据库本身不加密**：试验数据、用户名、审计内容在 SQLite 文件里是明文，保护靠操作系统的账户和磁盘加密（建议开启 BitLocker / FileVault）。

## 流程

### 密钥保护（`KeyProtector`）

`create_key_protector(keys_dir, mode)` 按平台和配置 `security.key_protection`（环境变量 `GELINSPECT_KEY_PROTECTION` 可覆盖）选择：

| 方式 | 平台 | 做法 |
|---|---|---|
| `dpapi` | Windows | `ctypes` 调用 `crypt32.CryptProtectData` / `CryptUnprotectData`（带固定的附加熵，`CRYPTPROTECT_UI_FORBIDDEN`），不新增依赖 |
| `keychain` | macOS | 每个数据目录一个 32 字节随机主密钥，存在登录钥匙串（服务名 `GelInspect`，账户名 `data-<数据目录路径的哈希>`）；数据用 AES-256-GCM 加密。主密钥通过 `security -i` 的**标准输入**写入，不出现在进程参数里 |
| `file` | 都可用 | 回退方式：数据目录 `keys/install.secret`（32 字节随机数，权限 600）经 scrypt 派生 AES-256-GCM 密钥 |

- `auto`（默认）：系统凭据可用就用，否则回退到 `file` 并写警告日志；`os`：只用系统凭据，不可用就报错（启动失败并说明）；`file`：只用安装密钥。
- 落盘格式统一为 `GIKP1:<方式>:` 加密文。读取时按文件头选择方式，所以换了方式（例如原来回退到 `file`，后来钥匙串可用了）旧文件仍能读出，并在启动时重新加密（审计 `key_protection_changed`）。
- 错误密钥、数据被改、换了电脑或用户：抛 `KeyProtectionError`，信息里不带密钥内容。

**风险说明（`file` 回退）**：安装密钥和密文在同一目录，它只能防“只拿到一个密钥文件”和误把密钥文件当作明文传播。仓库开发（`uv run gelinspect`）在 macOS 上默认也会尝试钥匙串；沙盒或无图形会话里访问不了钥匙串时自动回退。测试一律用 `file`（`tests/conftest.py` 设置环境变量），不往钥匙串写条目。

### 报告签名私钥

`LocalKeyStore` 注入上面的保护器（组装根 `app_context.py`）：

1. 新生成的私钥直接加密落盘（`keys/report-signing.key` 以 `GIKP1:` 开头）。
2. **迁移**：启动时 `LocalKeyStore.migrate()` 发现 P13 之前的明文 PEM，就加密后原子替换；替换前先用随机数据覆盖原明文文件并 `fsync`（“安全删除”的尽力版本：SSD 磨损均衡、APFS/NTFS 写时复制和快照可能留有旧数据块）。写审计 `key_protection_changed`。没来得及迁移的明文私钥在第一次签名时也会顺手迁移。
3. 私钥无法解密（换了电脑或用户）时签名报错并说明：已签署的报告仍可验签（证书是公开的），需要在原环境运行或从备份恢复。

### 审计链 HMAC

```text
旧记录 1..N（SHA-256）│ N+1 切换事件 audit_chain_keyed（HMAC，prev_hash = 第 N 条）│ N+2… HMAC
```

- 启动时 `activate_audit_hmac`：读取密钥环 `keys/audit-chain.key`（整个文件由 `KeyProtector` 加密，JSON 里每把密钥有标识、密钥、起点序号）。没有分段就生成 32 字节密钥，**先存密钥环、再写库**：同一事务里追加切换事件、写 `audit_chain_segments`、更新链头，最后在密钥环里记下起点。两步之间被杀，下次启动按数据库补上起点。
- 写入：当前分段的密钥计算 `HMAC-SHA256(key, prev_hash + "\n" + 规范化内容)`，输入与 SHA-256 版完全相同。数据库已有分段而没有密钥时拒绝写入（`StorageError`），不会悄悄写出一段 SHA-256。
- 校验 `verify_audit_chain`：以密钥环里的分段为准，先比对数据库里的副本（不一致报 `segment_mismatch`），再逐条按所在分段用 SHA-256 或 HMAC 计算；结果带 `chain_type`（`sha256`、`hmac-sha256`、`sha256+hmac-sha256`）和 `hmac_from_seq`。审计页的校验横幅会写出链类型。
- 兼容旧链：切换点之前的记录按原算法校验，所以 P13 之前的库升级后整条链仍然有效；旧记录被改动同样能发现（重算到切换点时 `prev_hash` 对得上，但切换事件的 HMAC 对不上）。
- 密钥环丢失或解不开：**不拒绝启动**。解不开的文件改名为 `audit-chain.key.unreadable-<时间>` 留存，换新密钥开新的一段，错误写日志；旧分段校验时报 `key_unavailable`。恢复原文件（或原电脑、原用户）后可以校验旧分段。
- 换电脑迁移：DPAPI 和钥匙串保护的密钥不能随数据目录带走。需要迁移时，先在原机器上改用 `security.key_protection: file`（或设置环境变量）启动一次，让密钥改用安装密钥保护，再整体复制数据目录；到新机器后再改回 `auto`。

### 敏感导出加密

- 配置 `exports.encrypt`（默认 false）或“仪器设置 → 仪器与数据 → 敏感导出加密”（`encrypt_exports`，保存后立即生效，写 `settings_changed` 审计）。
- 开启后：历史页两种导出、审计导出、报告中心的 Excel 导出和批量导出都会先弹出“设置导出密码”（至少 8 个字符，输两遍）。取消则不导出。服务层同样检查：要求加密而没有密码时拒绝（`export_password_required`），密码太短拒绝（`export_password_weak`）。
- Excel：先写到目标目录下的临时子目录，再打成同名 `.zip`（内含 `.xlsx`），明文覆盖后删除。批量导出：PDF 和 `manifest.csv` 不落成明文，直接打进目录里的 `reports_<时间>.zip`。
- 格式：WinZip AES-256（AE-2）：PBKDF2-HMAC-SHA1（1000 次，16 字节盐）、AES-256-CTR（小端计数器）、HMAC-SHA1 认证码。只用 `cryptography` 和 `numpy` 实现，没有新增依赖。7-Zip、WinRAR、Keka、The Unarchiver、`bsdtar` 能打开（测试用 macOS 自带的 `bsdtar` 验证互通）；**Windows 资源管理器和 macOS“归档实用工具”不支持 AES**，界面和本文都说明了。
- 审计 `data_exported` / `report_exported` 注明“已加密”，从不记录密码。

### 程序完整性校验

- 构建时（`scripts/build.py`）：对发布包程序目录里每个文件算 SHA-256，写 `gelinspect-manifest.json`，用 Ed25519 签名写 `gelinspect-manifest.sig`。签名私钥默认每次构建临时生成、不落盘；需要固定密钥时用 `--manifest-key` 或 `GELINSPECT_MANIFEST_KEY` 指定 PEM。公钥写进 `gelinspect/_build_info.py`（构建时生成、编译进主程序、构建后删除）。
- 启动时（`startup.check_program_integrity`）：
  - 仓库模式（开发运行）没有清单，**跳过并写日志**“开发模式（仓库运行），跳过程序完整性校验”。
  - 发布模式：重新计算清单里每个文件的哈希，发现改动、缺失，或程序目录里多出可执行代码（`.so/.dylib/.dll/.pyd/.py/.exe`…），就写审计 `integrity_checked`（列出文件）并弹出“程序文件校验未通过”，**不阻止使用**。冒烟测试（`--smoke-test`）里校验不通过返回非 0。
  - 配置 `security.integrity_check: false` 可关闭（例如按 LGPL 替换了 Qt 库之后）。
- `__pycache__` 不计入；发布包启动时设置 `sys.dont_write_bytecode`，不往程序目录写缓存。

### 授权接口（默认不启用）

`services/license.py`：`LicenseService.check()` 返回 `not_enabled`、`valid`、`missing`、`invalid`、`expired`、`wrong_device`，`enforced` 永远为 false。离线授权文件格式（JSON + Ed25519 签名）和设备指纹接口见模块文档。配置 `license.enabled`（默认 false）、`license.file`（默认数据目录下的 `license.json`）。**本版本没有任何把用户锁在外面的逻辑**；是否在某些状态下限制功能、设备指纹用什么来源，需要甲方决定。

### 运行模式与数据目录

`services/runtime_paths.py` 判断运行模式：构建生成的 `gelinspect._build_info` 存在**且**代码是 Nuitka 编译的才是发布模式。仓库里 `uv run gelinspect` 的行为不变（`./data`、内置模拟配置）。发布模式见 [deployment.md](../deployment.md#数据目录)。

### 排错：`--diagnose`

隐藏参数 `--diagnose` 不启动界面，打印 JSON：运行模式、程序目录、构建信息、默认数据目录、所用配置、能否导入 `simulation`、后端、完整性校验结果。完整性不通过或配置有误时返回 1。

## 权限

| 操作 | 权限 |
|---|---|
| 开关导出加密（仪器设置） | `instrument_configure`（管理员） |
| 加密导出 | 与原导出相同：`data_export`（审计导出另需 `audit_view`） |
| 校验审计链、查看链类型 | `audit_view` |
| 密钥迁移、HMAC 切换、完整性校验 | 系统在启动时执行，操作者记为 `system` |

## 数据

| 位置 | 内容 |
|---|---|
| `<数据目录>/keys/report-signing.key` | 签名私钥，`GIKP1:<方式>:` 加密 |
| `<数据目录>/keys/audit-chain.key` | 审计链密钥环，整个文件加密 |
| `<数据目录>/keys/install.secret` | 安装密钥（只在 `file` 方式或曾经回退时存在），权限 600 |
| macOS 登录钥匙串 | 服务名 `GelInspect`、账户 `data-<哈希>` 的主密钥 |
| 表 `audit_chain_segments`（迁移 0005） | 每段 HMAC 的起点、密钥标识、算法（数据库里的副本） |
| 程序目录 `gelinspect-manifest.json` / `.sig` | 文件清单和签名 |

审计事件（新增）：`audit_chain_keyed`（切换事件）、`key_protection_changed`（私钥加密或改用新方式）、`integrity_checked`（完整性校验不通过）。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/infrastructure/security/test_protectors.py` | 安装密钥往返、文件格式和权限、另一把安装密钥解不开、篡改、缺安装密钥不会悄悄重建；钥匙串（假 `security` 命令）往返、主密钥不出现在命令参数里、错误主密钥、缺条目、写入被拒；按数据目录区分账户；`auto` 优先钥匙串并能读旧格式、回退、`os` 方式报错；非 Windows 拒绝 DPAPI；Windows 上 DPAPI 往返和篡改（只在 Windows 运行）；真实钥匙串往返（`GELINSPECT_TEST_KEYCHAIN=1` 时运行） |
| `tests/infrastructure/security/test_audit_hmac.py` | 旧链切换并记录切换事件、旧记录仍按 SHA-256 校验且改动可检出、新库从第 1 条起 HMAC、重启复用密钥；**改库后重算整条 SHA 链被检出**、改旧记录重算被检出、降级为纯 SHA 链被检出、用猜的密钥重算被检出；没有密钥不能写；密钥文件丢失开新段并报告旧段、解不开的密钥文件留存且不挡启动、两步之间被打断能补上；密钥环加密且 `repr` 不含密钥 |
| `tests/infrastructure/security/test_integrity.py` | 清单通过；改动、缺失、多出代码分别报告（普通文本不算）；攻击者改文件后用自己的私钥重签被拒；只改清单被拒；缺清单 |
| `tests/services/reporting/test_key_protection.py` | 新私钥加密落盘；**明文私钥迁移**且明文消失；第一次使用时也迁移；错误密钥报错且不泄露；换保护方式重新加密；启动时自动迁移并写审计 |
| `tests/services/test_encrypted_zip.py` | AES ZIP 往返（中文文件名）、错误密码、篡改、短密码和重名、`bsdtar` 能解开且错误密码解不开 |
| `tests/services/test_export_encryption.py`、`reporting/test_batch_and_export.py`、`test_settings_service.py` | 默认不加密；要求加密时没有密码或密码太短被拒且不留文件；三种 Excel 导出加密往返、不留明文、审计注明加密且无密码；批量导出加密；设置开关立即生效并保存 |
| `tests/services/test_license.py` | 默认不启用；有效授权；缺失、过期、设备不符、内容被改、格式错、别人签的都只报告不锁定 |
| `tests/test_packaging.py` | 仓库模式路径不变；残留的构建信息不会切到发布模式；各平台数据目录；发布模式读包内配置并优先数据目录的 `config.yaml`；正式包配置没有模拟和演示账号、启动进入“创建管理员”；占位后端明确失败；仓库模式跳过完整性校验；发布模式校验不通过写审计、弹提示、冒烟测试失败；`--diagnose` |
| `tests/ui/test_history_page.py` | 开启加密时导出先要密码，取消不导出，确认后得到 ZIP；密码框检查长度和两次一致 |
| `tests/infrastructure/db/test_migrations.py`、`test_audit_chain.py` | 迁移到 0005；仓储只有追加和只读方法 |

需要人工验证：Windows 上 DPAPI 的实际运行（`uv run pytest tests/infrastructure/security`，Windows 上会运行 DPAPI 测试）；macOS 真实钥匙串（沙盒外 `GELINSPECT_TEST_KEYCHAIN=1 uv run pytest tests/infrastructure/security/test_protectors.py -k real_keychain`）；用 7-Zip（Windows）和 Keka（macOS）打开加密导出；发布包里改一个文件后启动，确认弹出提示且审计里有记录。
