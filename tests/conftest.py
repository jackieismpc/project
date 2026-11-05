"""全局 pytest 配置：注册契约测试的命令行选项；测试不碰系统钥匙串。"""

import os

import pytest

# 测试会建很多临时数据目录，每个都会生成密钥：一律用安装密钥保护，不往登录钥匙串里写条目。
# 钥匙串和 DPAPI 的实现由 tests/infrastructure/security/ 用假的 security 命令或在 Windows 上单独测。
os.environ.setdefault("GELINSPECT_KEY_PROTECTION", "file")


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
