"""命令行入口。

* ``gelinspect``：不带任何参数，打开主窗口（默认加载 ``simulation/`` 后端）。
* ``gelinspect --config <yaml>``：使用指定配置。
* ``gelinspect --simulate``：显式使用默认的模拟配置，与不带参数相同。
* ``gelinspect --version``：只打印版本号。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from gelinspect import __version__


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    Returns:
        配置好的参数解析器。
    """
    parser = argparse.ArgumentParser(
        prog="gelinspect",
        description="凝胶智检系统（GelInspect）上位机软件。不带参数直接打开主窗口。",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="YAML",
        help="使用指定的配置文件（完整配置，不与默认值合并）；默认加载 simulation/ 的模拟后端",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="显式使用默认的模拟配置（与不带参数相同）",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="开发和演示用：登录页显示演示账号，试剂卡显示示例码（正式使用不要加）",
    )
    # 隐藏参数：完整启动一遍后自动退出，用于干净克隆验证和 CI。
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="DIR",
        help="数据目录（数据库、日志、密钥）；默认仓库下的 data/，发布包为平台标准位置",
    )
    # 隐藏参数：不启动界面，打印运行模式、路径和程序完整性校验结果（打包验证、排错）。
    parser.add_argument("--diagnose", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """程序主入口。

    Args:
        argv: 命令行参数列表，为 None 时读取 ``sys.argv``。

    Returns:
        进程退出码，正常结束返回 0。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.simulate and args.config is not None:
        parser.error("--simulate 与 --config 不能同时使用")
    # 延迟导入：--version 和 --help 不需要加载 Qt。
    if args.diagnose:
        from gelinspect.startup import print_diagnosis

        return print_diagnosis(args.config, args.data_dir)
    from gelinspect.startup import run_gui

    return run_gui(
        config_path=args.config,
        data_dir=args.data_dir,
        smoke_test=args.smoke_test,
        demo=args.demo,
    )


if __name__ == "__main__":
    raise SystemExit(main())
