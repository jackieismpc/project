"""命令行入口。

* ``gelinspect``：打开主窗口。
* ``gelinspect --config <yaml>``：使用指定配置。
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
        help="使用指定的完整配置文件；不指定时读取当前启动配置",
    )
    # 隐藏参数：完整启动一遍后自动退出，用于干净克隆验证和 CI。
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="DIR",
        help="数据目录（数据库、日志、密钥）；源码默认仓库 data/，Windows发布包默认程序目录 data/",
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
    # 延迟导入：--version 和 --help 不需要加载 Qt。
    if args.diagnose:
        from gelinspect.startup import print_diagnosis

        return print_diagnosis(args.config, args.data_dir)
    from gelinspect.startup import run_gui

    return run_gui(
        config_path=args.config,
        data_dir=args.data_dir,
        smoke_test=args.smoke_test,
    )


if __name__ == "__main__":
    raise SystemExit(main())
