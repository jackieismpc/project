"""冒烟测试：校验版本号与命令行入口。"""

import pytest

from gelinspect import __version__
from gelinspect.__main__ import main


def test_version_is_0_1_0() -> None:
    """包版本号应为 0.1.0。"""
    assert __version__ == "0.1.0"


def test_main_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    """``--version`` 应输出版本号并以 0 退出。"""
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
