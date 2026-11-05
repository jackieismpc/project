# 报告（reports）

对应阶段：P11。代码：`src/gelinspect/services/reporting/`（`document.py` 内容模型、`html.py` 排版、`renderer.py` 渲染、`keystore.py` 证书、`pdf_signing.py` 签名、`verify.py` 验签、`report_service.py` 服务、`texts.py`/`strings.py` 文字）、`domain/signing.py`（签署前提）；界面 `ui/pages/reports_page.py`、`report_dialogs.py`、`ui/report_labels.py`、监控页两个标签；迁移 `rev_0004_reports.py`。视觉对照 `docs/design/Report.dc.html`。

## 目的

试验完成并复核后，分析员在报告中心预览、打印、签署报告，生成带数字签名的 A4 PDF；任何人都能重新验签，发现文件被改动。也支持把同一批号的多次试验汇总成单批次报告，以及批量导出已签署报告。

**签名是本机自签证书的：能证明文件签署后没有被改动，且由本机这把密钥签署，但没有第三方 CA 的法律效力。** 界面和本文都这样说明；需要法律效力时，后续换成 CA 证书（`KeyStore` 接口不变）。

## 流程

### 渲染

`QTextDocument`（HTML 子集）排版，`QPdfWriter` 输出 A4 PDF；预览用 `QPrintPreviewWidget`，打印用 `QPrinter`，三者共用同一套排版（`ReportRenderer.paint`）。只用 PySide6-Essentials，没有 QtPdf 或其他 PDF 库。中文用随软件内置的 Noto Sans SC，Qt 把用到的字形子集嵌入 PDF（测试检查字体都已嵌入）。页脚（不可更改声明、版权声明、内容摘要、页码）逐页自绘；结果表头跨页重复，行不会被切开。没有 `QGuiApplication` 时（无窗口环境）自动用 offscreen 平台创建（有子进程测试）。渲染要在主线程做。

报告内容：标题；文件名（试验编号）；温度范围（温度日志里真实的最低到最高，没有日志留空）；仪器编号；操作人；检测日期（开始孵育的时间）；签署人和日期；鲎试剂灵敏度；三种批号；MVD 和内毒素限值（录入项，目前没有录入入口，留空）；结果表（试管编号、样品名称、序列号、样品批号、稀释倍数、外加内毒素、阴阳性，复核过的标注「已复核」）；结论区（结论、λc、依据、规则版本和来源；待定或无效时写明原因）；页脚。可选附每排 8 连图缩略页（配置 `reports.include_images`，默认关）。

页脚的「内容摘要」是报告数据（规范化 JSON，时间用 UTC）的 SHA-256，**不是文件哈希**（文件哈希写进文件会改变自己）；同一份数据不论中英文、时区都相同。文件完整性由 PDF 签名和库里的文件 SHA-256 保证。版权权利人来自配置 `reports.copyright_holder`，默认占位 `[版权方名称]`（英文报告显示 `[Copyright holder]`）。

报告语言：未签署时可选简体中文或 English（默认跟随界面语言），签署时按所选语言生成，已签署的固定为签署时的语言。文字在 `strings.py`，译文读 `translations/gelinspect_en.ts`，与界面语言无关；结论依据、用途名称复用界面已有译文。

### 签署

`ReportService.sign`（需要 `EXPERIMENT_SIGN`）：

```text
校验权限 → 检查签署前提 → 再次输入密码（argon2，复用登录失败计数和锁定）
→ 写入签署人和时间（内存）→ 渲染 PDF → pyHanko PAdES 签名（SHA-256，增量更新）→ 自检验签
→ 写同目录临时文件并 fsync
→ 同一个工作单元：状态转「已签署」（带预期状态）、签署人和时间、报告记录（含文件 SHA-256）、审计、
  把文件放到最终位置 → 提交
```

任一步失败都不留半成品：临时文件和最终文件被删，数据库回滚，试验仍是「已完成」（测试在渲染、签名、自检、写文件、工作单元每个写入点、提交前最后一步注入失败）。硬杀进程可能留下 `.part` 临时文件或一个没有记录的孤立 PDF，不影响数据库一致性。

**禁止签署**（`domain/signing.py`，没有任何配置项可以绕过）：有待复核的管；有管没有结果；试验没有完成；**结论为「待定」或「无效」**（PLAN 第 7 节第 21 项，防止未判定的结果被当成放行依据；无效时界面逐条列出无效原因）；没有入库的结论（P10 之前的旧试验）。「合格」和「不合格」可以签署。已签署的试验锁定：状态机不再接受事件，复核服务拒绝（`EXPERIMENT_SIGNED`），同一份签名文件可以反复导出。

### 证书

第一次需要签名时在 `data/keys/` 生成本地自签证书和私钥（默认 ECDSA P-256，可配 RSA 3072；主题 CN、O、有效期来自配置 `reports`）。目录和文件已被 `.gitignore`（`*.pem`、`*.key`、`/data/`）忽略；POSIX 下私钥权限 600、目录 700，被改宽会自动收紧；Windows 没有等价权限位，依赖用户目录的访问控制。私钥不进日志、审计和异常信息。私钥由 `KeyProtector` 加密保存（P13：Windows DPAPI、macOS 钥匙串，都不可用时用安装密钥派生的口令），P13 之前的明文私钥在启动时自动加密迁移、原明文覆盖删除，见 [security.md](security.md#报告签名私钥)。只剩私钥时用它重发证书；有证书没私钥时报错，不静默重新生成。

### 验签

「重新验签」（`REPORT_VIEW`）用 pyHanko 检查：签名完整且有效（`intact`/`valid`）、覆盖整个文件（没有追加内容）、证书是本机信任的那张（信任根只设为本机证书，自签证书不走公共信任链，并核对指纹）、文件 SHA-256 与库内一致。每项失败都有独立原因（`VerifyFailure`）并写审计；改一个字节、追加内容、换成另一份合法签名文件、换成别处签的文件、删除文件、不是 PDF 都能区分。也可选任意 PDF 验证（不核对库内哈希）。签名没有时间戳服务（离线），签名时间是本机时钟。

### 单批次报告

选批号种类（鲎试剂批号或样品批号）和批号，汇总该批号下已完成和已签署的试验，每个试验一节。预览和打印随时可用；**只有批次里全部试验都已签署后才能签署**（需要 `EXPERIMENT_SIGN` 和密码），它不改变任何试验的状态。没有试验时显示空状态。

### 导出和打印

- 导出已签名 PDF：复制入库的同一份文件，复制前核对 SHA-256，不符拒绝。
- 批量导出：选择若干已签署报告导出到目录，复制 PDF 并写 `manifest.csv`（报告、试验或批号、文件名、SHA-256、签署人和时间、语言、内容摘要、状态）。被改动或缺失的不导出，清单里标出原因。开启了导出加密（P13）时，PDF 和清单打进目录里的一个 AES-256 加密 ZIP，见 [security.md](security.md#敏感导出加密)。
- 打印：系统打印对话框，写 `report_printed` 审计。快捷栏「打印报告」带着监控页当前试验跳到报告中心；监控页的「报告打印预览」「单批次报告」两个标签是入口卡片，一键到报告中心（该试验或该批号）。
- Excel 数据导出属于 P12，本阶段没有。

## 权限

| 操作 | 权限 | 角色 |
|---|---|---|
| 进入报告中心、预览、打印、验签 | `report_view` | 全部 |
| 签署试验报告、签署单批次报告 | `experiment_sign` | 分析员 |
| 导出已签名 PDF、批量导出 | `data_export` | 管理员、分析员 |

界面按 `session.can(...)` 禁用入口，服务层每个入口先 `authorize`，拒绝时写 `permission_denied` 审计。

## 数据

- `reports` 表（迁移 0004）：`experiment_id`（单批次为空）、`path`（相对数据目录）、`sha256`（文件）、`scope`、`batch_kind`、`batch_no`、`language`、`signed_by`、`signed_at`、`certificate_sha256`、`content_sha256`、`experiment_ids`（JSON）。`experiments.signed_by`、`signed_at` 由 `record_signature` 写入。
- 文件：`data/reports/<试验>/<试验>.pdf`；单批次 `data/reports/_batch/<种类>_<批号>_<哈希>/batch_….pdf`（批号做了字符净化，不能越出目录）。证书 `data/keys/report-signing.pem` 和 `.key`。
- 审计：`experiment_state_changed`（completed → signed）、`experiment_signed`（文件和内容 SHA-256、证书指纹，不含密钥）、`report_signed`（单批次）、`report_verified`（含失败原因）、`report_exported`、`report_printed`；签署时密码错误写 `login_failed`。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/services/reporting/test_signing_flow.py` | 真实 SQLite + 真实 Qt + pyHanko：签署全流程、PDF 含中文和关键字段、字体嵌入、温度范围、**多个位置各翻转一个字节验签失败**（含只靠签名、不靠库内哈希）、追加内容、文件替换、外来证书、缺失文件、非 PDF、密码错误、实验员和管理员不能签署、签署后复核被拒、重复导出同一文件、私钥不进日志和审计 |
| `test_blocks_and_failures.py` | 待复核、待定、无效（含原因）、未完成、无结论不能签署；无绕过配置；渲染、签名、自检、写文件、工作单元各步、提交前最后一步注入失败时无半成品且状态不变；状态冲突；繁忙 |
| `test_rendering.py` | 80 管多页、表头和页脚逐页、行不跨页；中英文；长文字不超界；英文无中文残留；每个键有英文；版权配置；转义；内容摘要；8 连图 |
| `test_batch_and_export.py` | 单批次（鲎试剂批号、样品批号）、未签署不可签、空状态、权限、路径净化；批量导出和清单、被改动或缺失不导出、权限、重复导出；`perf`：100 份导出 |
| `test_keystore.py` | 生成、复用、RSA、权限收紧、缺证书补发、缺私钥报错、损坏、加密接口、配置、`.gitignore`、无窗口子进程 |
| `tests/ui/test_report_center.py` | 真实向导跑模拟试验 → 复核 → 签署（错误密码、正确密码）→ 验签 → 篡改 → 外部验签 → 导出和批量导出 → 打印；监控页标签和快捷栏跳转；单批次；空状态；中英文 × 两种窗口尺寸无超界 |

文本提取用 pyHanko 读取 PDF 加自带的 ToUnicode 解析（`tests/services/reporting/pdftext.py`），没有新增依赖。

**批量导出实测**（macOS 开发机，`uv run pytest -m perf -s tests/services/reporting/test_batch_and_export.py`）：100 份已签署报告（每份约 99 KB，含逐份 SHA-256 核对和清单）约 0.03 秒，指标 <10 秒。

需要人工验证：真实打印机和系统打印对话框；Windows（字体、权限位、打印）；高 DPI；真实 12 MP 图像下的 8 连图耗时；英文措辞审校；PDF 在 Acrobat 等阅读器里对自签证书的提示（会显示「未知签名者」，符合预期）。

## 已知限制

- 私钥加密保存，但运行中在内存里是明文；DPAPI/钥匙串保护的私钥不能随数据目录换电脑（见 security.md）；没有时间戳；证书过期后验签按当前时刻判断，所以有效期默认 10 年。
- MVD、内毒素限值没有录入界面，报告留空。
- 渲染和签名在主线程，几十管的报告耗时百毫秒级；很大的 8 连图报告会让界面短暂等待。
- 单批次报告签署后，批次里新增试验不会更新旧报告，需要重新生成。
