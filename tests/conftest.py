"""全局 pytest 配置：注册契约测试的命令行选项。"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--analyzer-backend",
        action="append",
        default=[],
        help="要校验的 GelAnalyzer 实现，格式为 pkg.module:Class 或 pkg.module.Class，可重复",
    )
    parser.addoption(
        "--analyzer-config",
        default=None,
        help="传给 GelAnalyzer.load 的配置文件路径",
    )
