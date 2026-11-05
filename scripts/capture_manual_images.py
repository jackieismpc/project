"""用真实应用离屏截图，生成用户手册里的配图（``docs/images/*.png``）。

用法::

    QT_QPA_PLATFORM=offscreen uv run python scripts/capture_manual_images.py

做法与组件展示页 ``gelinspect.ui.gallery`` 一致：真实窗口、真实数据，``QWidget.grab()`` 离屏截图。
所有数据都由真实流程产生：实验员走向导启动试验，引擎、模拟视频和算法跑完，分析员复核并签署。
登录页隐藏演示账号提示（真实部署没有它）。全部图片约 1280×800，以最高压缩比存成 PNG。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import acceptance_support as support
from PySide6.QtWidgets import QDialog, QScrollArea, QWidget

from gelinspect.domain.enums import BatchKind, ReagentKind
from gelinspect.ui.i18n import Language
from gelinspect.ui.pages.monitor_page import TAB_DATA, TAB_IMAGES, TAB_REPORT
from gelinspect.ui.widgets.alert_stack import AlertStack
from gelinspect.ui.widgets.toast import Toast
from simulation.reagents import SAMPLE_CODES
from tests.ui.monitor_helpers import click_well
from tests.ui.wizard_helpers import (
    STEP_CONFIRM,
    STEP_REAGENTS,
    STEP_SAMPLES,
    choose_template,
    click,
    open_wizard,
    pass_reagents,
    scan,
    select_tubes,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
TEMPLATE = "灵敏度复核 (2λ:1)"


def shot(
    rig: support.Rig,
    name: str,
    widget: QWidget | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> None:
    """截图并存成 PNG（压缩等级最高）。

    Args:
        rig: 应用。
        name: 文件名（不含扩展名）。
        widget: 截哪个控件，默认整个主窗口。
        region: 只保留 ``(x, y, 宽, 高)``，用于裁掉含开发配置的部分。
    """
    support.settle(rig.app, 6)
    for toast in rig.window.findChildren(Toast):  # 引擎告警的提示条会盖住右上角
        toast.hide()
    for stack in rig.window.findChildren(AlertStack):  # 加速模拟下后几排总是「晚于到时」，截图不要
        stack.clear()
        stack.setVisible(False)
    support.settle(rig.app, 6)
    target = widget if widget is not None else rig.window
    pixmap = target.grab()
    if region is not None:
        pixmap = pixmap.copy(*region)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    if not pixmap.save(str(path), "PNG", 0):
        raise RuntimeError(f"保存失败：{path}")
    print(f"{name}.png  {pixmap.width()}×{pixmap.height()}  {path.stat().st_size // 1024} KB")


def show_dialog(rig: support.Rig, dialog: QDialog, name: str) -> None:
    """显示（非模态）一个对话框、截图并关闭。"""
    dialog.show()
    shot(rig, name, dialog)
    dialog.close()
    dialog.deleteLater()


def capture_setup(root: Path) -> None:
    """真实配置的首次启动：创建管理员。"""
    rig = support.open_rig(root, demo_accounts=False)
    try:
        shot(rig, "setup-admin")
    finally:
        support.close_rig(rig)


def capture_invalid(root: Path) -> None:
    """阴性对照出现阳性：结论「无效」。用脚本化的算法配置造出这个场景。"""
    config = root / "analyzer.yaml"
    states = "\n".join(f"  {n}: fully_gelled" for n in range(1, 18))
    config.write_text(f"scripted: true\ntube_states:\n{states}\n", encoding="utf-8")
    rig = support.open_rig(root / "invalid", analyzer_config=config)
    try:
        experiment_id = support.run_experiment(rig)
        support.sign_in(rig.app, rig.window, "analyst")
        rig.window.open_experiment(experiment_id)
        shot(rig, "monitor-invalid")
    finally:
        support.close_rig(rig)


def main() -> int:
    """逐页截图。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        capture_setup(root / "setup")
        capture_invalid(root)
        rig = support.open_rig(root / "main", incubation_minutes=10)
        app, window = rig.app, rig.window
        try:
            # 登录页：真实部署没有演示账号提示
            window.login_page.set_demo_accounts(())
            shot(rig, "login")

            # 实验员：向导四步
            page = open_wizard(app, window, "experimenter")
            choose_template(app, page, TEMPLATE)
            shot(rig, "wizard-1-template")
            click(app, page.template_next)
            for card in page.reagent_cards.values():  # 示例码只有模拟配置才有，截图时去掉
                card.set_samples([])
            scan(app, page, ReagentKind.LAL_REAGENT, SAMPLE_CODES["lal_expired"])
            shot(rig, "wizard-2-reagent-locked")
            pass_reagents(app, page)
            shot(rig, "wizard-2-reagents")
            assert page.current_step() == STEP_REAGENTS
            click(app, page.reagent_next)
            assert page.current_step() == STEP_SAMPLES
            select_tubes(app, page, range(1, 18))
            shot(rig, "wizard-3-samples")
            click(app, page.sample_next)
            assert page.current_step() == STEP_CONFIRM
            shot(rig, "wizard-4-confirm")
            click(app, page.confirm_start)
            assert support.wait_for(app, lambda: window.current_route == "monitor", 30.0)
            time.sleep(4.0)
            support.settle(app, 20)
            shot(rig, "monitor-running")
            experiment_id = window.page("monitor").experiment_id  # type: ignore[attr-defined]
            from tests.ui.monitor_helpers import wait_finished

            wait_finished(app, rig.context)
            support.wait_for(app, lambda: False, 2.0)  # 让监控页把最后的状态刷出来
            shot(rig, "monitor-pending-review")
            monitor = window.page("monitor")
            monitor.tabs.set_current(TAB_IMAGES)  # type: ignore[attr-defined]
            monitor.stack.setCurrentIndex(TAB_IMAGES)  # type: ignore[attr-defined]
            support.wait_for(app, lambda: False, 3.0)  # 等原图读出来
            scroller = monitor.findChild(QScrollArea)
            assert scroller is not None
            scroller.verticalScrollBar().setValue(340)
            shot(rig, "monitor-images")
            scroller.verticalScrollBar().setValue(0)
            monitor.tabs.set_current(TAB_DATA)  # type: ignore[attr-defined]
            monitor.stack.setCurrentIndex(TAB_DATA)  # type: ignore[attr-defined]
            support.settle(app, 4)
            click_well(monitor.plate, 11)  # type: ignore[attr-defined]
            support.settle(app, 10)

            # 分析员：单管详情、复核、结论、签署
            window.logout(confirm=False)
            support.sign_in(app, window, "analyst")
            window.open_tube(experiment_id, 11)
            support.wait_for(app, lambda: window.page("tube").images is not None, 10.0)  # type: ignore[attr-defined]
            shot(rig, "tube-detail-review")
            support.finish_and_review(rig, experiment_id)
            window.open_experiment(experiment_id)
            shot(rig, "monitor-conclusion")
            window.open_experiment(experiment_id)
            monitor = window.page("monitor")
            monitor.tabs.set_current(TAB_REPORT)  # type: ignore[attr-defined]
            monitor.stack.setCurrentIndex(TAB_REPORT)  # type: ignore[attr-defined]
            shot(rig, "monitor-report-tab")

            window.open_report(experiment_id)
            support.wait_for(app, lambda: window.page("reports").current_preview is not None, 20.0)  # type: ignore[attr-defined]
            shot(rig, "reports-preview")
            from gelinspect.ui.pages.report_dialogs import BatchExportDialog, SignDialog

            sign = SignDialog(
                lambda _p: None, subject=experiment_id, signer="分析员", parent=window
            )
            sign.password.setText("········")
            show_dialog(rig, sign, "sign-dialog")
            support.sign_experiment(rig, experiment_id)
            window.open_report(experiment_id)
            reports = window.page("reports")
            support.wait_for(app, lambda: reports.current_record is not None, 20.0)  # type: ignore[attr-defined]
            click(app, reports.verify_button)  # type: ignore[attr-defined]
            support.wait_for(app, lambda: reports.last_verification is not None, 20.0)  # type: ignore[attr-defined]
            shot(rig, "reports-signed")
            records = rig.context.reports.signed_reports(window.session)
            export = BatchExportDialog(records, lambda *_: None, window)  # type: ignore[arg-type,return-value]
            show_dialog(rig, export, "batch-export-dialog")
            stored = rig.context.database.experiments.get(experiment_id)
            assert stored is not None
            batch_no = next(
                r.batch_no for r in stored.reagents if r.kind is ReagentKind.LAL_REAGENT
            )
            window.open_batch_report(BatchKind.LAL_REAGENT.value, batch_no)
            shot(rig, "reports-batch")

            # 分析员：审计与历史
            assert window.navigate("history")
            shot(rig, "history")
            assert window.navigate("audit")
            audit = window.page("audit")
            audit.keyword_edit.setText("登录")  # type: ignore[attr-defined]
            audit.search_button.click()  # type: ignore[attr-defined]
            support.settle(app, 10)
            audit.verify_button.click()  # type: ignore[attr-defined]
            time.sleep(0.3)
            support.wait_for(app, lambda: not audit.verifying, 20.0)  # type: ignore[attr-defined]
            shot(rig, "audit")
            assert window.navigate("dashboard")
            shot(rig, "dashboard")
            window.lock_screen()
            shot(rig, "lock")
            window.logout(confirm=False)

            # 管理员
            support.sign_in(app, window, "admin")
            for route, name in (
                ("users", "users"),
                ("templates", "templates"),
                ("reagents", "reagents"),
            ):
                assert window.navigate(route)
                shot(rig, name)
            assert window.navigate("settings")
            # 右侧「视频输入」卡片下方会显示后端的类路径，只截到预览区为止
            shot(rig, "settings-instrument", region=(20, 120, 610, 680))
            shot(rig, "settings-video", region=(640, 120, 600, 440))
            assert window.navigate("users")
            from gelinspect.ui.pages.users_dialogs import NewUserDialog

            show_dialog(rig, NewUserDialog(lambda *_: None, window), "new-user")  # type: ignore[arg-type,return-value]
            assert window.navigate("dashboard")

            # 英文界面
            rig.language.set_language(Language.EN, persist=False)
            window.open_experiment(experiment_id)
            shot(rig, "monitor-english")
        finally:
            support.close_rig(rig)
    total = sum(p.stat().st_size for p in OUT.glob("*.png"))
    print(f"共 {len(list(OUT.glob('*.png')))} 张，{total / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
