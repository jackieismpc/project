# 新试验流程、模板管理与试剂溯源

对应阶段：P8。代码：`ui/pages/new_experiment.py`、`wizard_widgets.py`、`templates_page.py`、`template_editor.py`、`reagents_page.py`；`services/experiment_service.py`、`experiment_draft.py`、`template_service.py`、`reagent_service.py`、`errors.py`。视觉对照 `docs/design/Wizard.dc.html`。

## 目的

让实验员从选模板到启动试验走完一条不会放行不合格试剂的路径；管理员维护检测模板；所有人能查试剂批次和校验记录。判定规则（P10）不在本阶段；启动后跳到试验监控页（P9，见 [monitor-and-review.md](monitor-and-review.md)）。

## 流程

### 新试验向导（四步）

1. **选模板**：列出内置和自定义模板（类型、管数）；布局为推定的模板带“布局待确认”徽标，仍可使用。
2. **校验试剂**：鲎试剂、内毒素、检查用水三张卡。扫码枪是键盘楔式，输入框回车即触发，也可手输；示例码（仅模拟配置提供）点一下填入。结果由 `interfaces/traceability.py` 的解析器和备案清单给出。过期、未备案、有效期未知、清单不可用、没有配置，都**锁定流程**：红色横幅写明原因，“下一步”禁用，每次扫码写审计（`reagent_checked`）。种类放错、读不出码只提示重扫，不锁定。
3. **录入样品和管位**：左表（序号、样品名称、样品批号、外加内毒素、管号，可编辑；按 λ 倍数的外加内毒素由鲎试剂的 λ 算出，不能手工改），右侧 `PlateGrid` 选择模式。自动选择选模板默认管号，全部清除清空；显示“已选 n / N · 管位已对应 / 还差 n 个 / 多选了 n 个”。数量不符或样品名称为空不能下一步。模板需要 λ（灵敏度复核或有 λ 倍数）而鲎试剂没有时，第 2 步就不能继续，服务层也拒绝。选中的管号升序后依次对应样品行。
4. **确认启动**：汇总后点“确认并启动”。服务创建试验（就绪）并交给引擎；引擎启动前检查失败（温度越限、舱门、视频等）时横幅显示引擎给出的原因，试验仍为就绪，可“重试启动”（不会再建第二个试验）。启动成功（引擎进入孵育）后向导复位并跳到试验监控页。

返回上一步不丢数据；回到第 3 步时，已为失败启动创建的就绪试验会被放弃（状态 `stopped`，写审计），按新内容重建。离开未完成的向导（导航、快捷栏、退出登录）要确认。更换模板若已录入样品或管位也要确认。

### 服务层再校验

界面被绕过也不放行，`ExperimentService`：

* `create_experiment`：只信任扫码**原文**，自己解析、查备案、按当天日期判断；任一试剂不合格写审计并抛 `ReagentLockedError`，不保存试验。同时校验模板存在、样品数与模板一致、管号合法不重复、样品名称非空。成功时试验经状态机到就绪，创建、试剂校验、状态变化和校验记录在同一事务提交。
* `start_experiment`：对保存的批次按今天复核；过期（隔夜才启动）转 `reagent_locked` 并拒绝。再交给 `ExperimentEngine.start`（它仍校验就绪状态和权限）。
* `abandon_experiment`：放弃就绪但未启动的试验。

### 模板管理

列表加管位详情。内置模板只读，可复制；管理员可新建、复制（得到“X 副本”，可再编辑）、编辑自定义模板：名称、类型、每管的样品名、稀释倍数、外加内毒素（绝对值）、λ 倍数（填了以它为准）、用途。管号按行顺序从 1 编。自定义模板不带“布局待确认”标记。名称不能为空、不能重名（含与内置同名），1 到 96 管。

### 试剂溯源

* 已录入批次：同一种类同一批号取最近一次校验；过期整行红色并写“已过期”，剩余 30 天内黄色“即将到期”，未备案红色；显示剩余天数。
* 校验记录：每次向导里的扫码（有校验结论的）一行，最新在前。
* 扫码查询：所有角色可用，只校验、不写记录。
* 政府溯源码：按 `interfaces/traceability.py` 预留入口，标“接入预留”，输入和按钮禁用，说明状态。

### 配置

`traceability`（`services/config.py`）配置解析器、备案清单、政府溯源客户端的类路径，默认配置指向 `simulation/reagents.py`；真实配置不写时试剂无法校验，向导停在第 2 步说明原因，不会放行。`instrument_no` 写入新试验（默认 `GI-001`）。

## 权限

| 操作 | 权限 | 角色 |
|---|---|---|
| 进入向导、创建试验、放弃 | `experiment_create` | 实验员 |
| 向导里扫码校验 | `reagent_manage` | 实验员 |
| 启动 | `experiment_run` | 实验员 |
| 查看模板、试剂批次和记录、扫码查询 | `template_view` / `experiment_view` | 全部 |
| 新建、复制、编辑模板 | `template_manage` | 管理员 |

界面禁用入口，服务层再校验并写 `permission_denied` 审计。

## 数据

* 新表 `reagent_checks`（迁移 0002）：时间、操作者、种类、批号、产品编码、结论、是否备案、有效期、标示灵敏度、说明、扫码原文、关联试验。只追加。
* 审计：`reagent_checked`（扫码、锁定、创建时通过、启动前复核）、`experiment_created`、`experiment_state_changed`、`template_changed`。
* `UnitOfWork` 增加 `templates`、`reagent_checks`。写锁等待超时时所有仓储写入抛 `StorageBusyError`。

## 测试

* `tests/services/test_reagent_service.py`、`test_template_service.py`、`test_experiment_draft.py`、`test_experiment_service.py`：校验结论、锁定与审计、权限、数量和管号、启动前复核、放弃。
* `tests/services/test_new_experiment_engine.py`：真实 SQLite 和模拟后端，引擎收到的管位与 `domain.plate` 映射一致，启动失败的原因，设备忙。
* `tests/ui/test_new_experiment.py`：向导全流程、过期和未备案锁定、绕过界面仍被拒、扫码枪回车、数量不符、管位映射、返回不丢数据、离开确认、启动失败重试、权限。
* `tests/ui/test_templates_page.py`、`test_reagents_page.py`、`test_layout_languages.py`（向导四步、两页和编辑器，中英文、1280×800 与 1024×700 无超界）。
* `tests/infrastructure/db/test_lock_translation.py`、`test_reagent_checks.py`、`tests/services/test_auth.py`（数据库忙）。

需要人工验证：真实触摸屏和真实扫码枪（键盘楔式的字符间隔）；Windows；英文措辞审校；备案清单和试剂码格式（模拟约定，待甲方提供）。
