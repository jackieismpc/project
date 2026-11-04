"""命令行入口。

支持 ``gelinspect --version`` 与 ``gelinspect --simulate``。
骨架阶段仅输出版本号，业务启动逻辑在后续阶段实现。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from gelinspect import __version__


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    Returns:
        配置好的参数解析器。
    """
    parser = argparse.ArgumentParser(
        prog="gelinspect",
        description="凝胶智检系统（GelInspect）上位机软件",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="使用模拟数据运行（骨架阶段暂未生效）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """程序主入口。

    Args:
        argv: 命令行参数列表，为 None 时读取 ``sys.argv``。

    Returns:
        进程退出码，正常结束返回 0。
    """
    parser = build_parser()
    parser.parse_args(argv)
    print(f"gelinspect {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
