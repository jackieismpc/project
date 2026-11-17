"""验证 Windows Qt 打印后端和测试平台配置。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="仅适用于 Windows Qt 打印后端")
def test_windows_test_configuration_defaults_to_native_qpa() -> None:
    """验证根测试配置的 Windows 默认平台，并保留调用方设置优先级。"""
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("QT_QPA_PLATFORM", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1]); "
            "import os; print(os.environ['QT_QPA_PLATFORM'])",
            str(root / "tests" / "conftest.py"),
        ],
        capture_output=True,
        check=False,
        cwd=root,
        env=env,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "windows"


@pytest.mark.skipif(os.name != "nt", reason="仅适用于 Windows Qt 打印后端")
def test_windows_test_configuration_preserves_explicit_qpa() -> None:
    """验证调用方显式指定的 Qt 平台不会被根测试配置覆盖。"""
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1]); "
            "import os; print(os.environ['QT_QPA_PLATFORM'])",
            str(root / "tests" / "conftest.py"),
        ],
        capture_output=True,
        check=False,
        cwd=root,
        env=env,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "offscreen"


@pytest.mark.skipif(os.name != "nt", reason="仅适用于 Windows Qt 打印后端")
def test_qprinter_initialization_has_no_windows_fatal_exception() -> None:
    """在独立原生 Qt 进程中创建打印机，并检查 faulthandler 输出。"""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-c",
            "from PySide6.QtWidgets import QApplication; "
            "from PySide6.QtPrintSupport import QPrinter; "
            "app = QApplication([]); "
            "printer = QPrinter(QPrinter.PrinterMode.HighResolution)",
        ],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Windows fatal exception: code 0x80040155" not in result.stderr
