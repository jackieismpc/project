# 权限

## 目的

定义管理员、实验员、分析员三个角色各自可以做什么，作为界面入口控制和服务层检查的唯一依据。本文对应 P1 的契约，P7（外壳与权限）和后续页面依赖它。

代码：`src/gelinspect/domain/permissions.py`。

## 流程

1. 用户登录后，会话持有其角色（P7，`services/auth.py` 的 `Session`）。
2. 服务层在执行操作前调用 `AuthService.authorize(session, permission)`（内部用 `has_permission`，并重新读取用户）。没有权限时拒绝操作，并写 `permission_denied` 审计事件。
3. 界面根据同一函数隐藏或禁用入口。界面控制只是便利，真正的检查在服务层。

新增功能时，先在 `Permission` 中增加权限，再在 `PERMISSION_ROLES` 中声明角色。`tests/domain/test_permissions.py` 要求每个权限都出现在矩阵中，并把矩阵内容固定下来，防止无意的修改。

## 权限

| 权限 | 说明 | 管理员 | 实验员 | 分析员 |
|---|---|:-:|:-:|:-:|
| `user_manage` | 管理用户和角色 | ✓ | | |
| `instrument_configure` | 修改仪器和相机设置 | ✓ | | |
| `audit_view` | 查看审计追踪 | ✓ | | ✓ |
| `template_view` | 查看检测模板 | ✓ | ✓ | ✓ |
| `template_manage` | 新建、修改和停用检测模板 | ✓ | | |
| `reagent_manage` | 录入和校验试剂批次 | | ✓ | |
| `experiment_view` | 查看试验和结果 | ✓ | ✓ | ✓ |
| `experiment_create` | 新建和编辑试验 | | ✓ | |
| `experiment_run` | 启动试验和恢复异常 | | ✓ | |
| `experiment_stop` | 停止试验 | | ✓ | |
| `result_review` | 人工复核单管结果 | | | ✓ |
| `experiment_sign` | 签署试验和报告 | | | ✓ |
| `report_view` | 预览和打印报告、验签 | ✓ | ✓ | ✓ |
| `data_export` | 导出报告和数据 | ✓ | | ✓ |

设计原则是职责分离：

- 实验员执行试验，但不能复核和签署。
- 分析员复核和签署，但不能操作试验。
- 管理员管理系统和模板，但不能操作试验、不能改结果。

这是 P1 拟定的方案，计划中没有细化到权限级别，**需要甲方 QA 确认**。例如，参考软件的演示中由 `admin` 账号运行试验；小型实验室也可能需要一人兼任多个角色。修改只涉及 `PERMISSION_ROLES` 和对应的测试。

## 数据

| 实体 | 字段 | 说明 |
|---|---|---|
| `User` | 标识、登录名、显示名称、角色、是否启用 | 每个用户一个角色。密码哈希属于数据层（P3） |
| `AuditEvent` | 时间、操作者、当时的角色、事件类型、操作记录、关联试验 | 对应审计页的序号、操作者、时间、权限、操作记录。序号和哈希链由 P3 生成 |

矩阵是代码常量，不存数据库，修改需要发布新版本。相关审计事件：`login`、`logout`、`login_failed`、`user_changed`。

## 测试

| 文件 | 覆盖内容 |
|---|---|
| `tests/domain/test_permissions.py` | 矩阵覆盖全部权限；矩阵与规格完全一致；逐一检查“角色 × 权限”；反查函数；矩阵只读；实验员不能复核和签署 |

P7 已落地：`tests/ui/test_main_window.py` 逐角色检查侧栏和快捷栏与矩阵一致，`tests/services/test_auth.py` 检查服务层的再校验（见 [auth-and-i18n.md](auth-and-i18n.md)）。

需要人工验证：在真实窗口里用三个角色分别登录，确认页面入口与矩阵一致。
