"""运行模式与路径：仓库内运行（``uv run gelinspect``）还是发布包。

* **仓库模式**：从源码运行。资源（翻译、默认配置引用的 ``simulation/``）在仓库根目录，
  数据目录是仓库下的 ``data/``，行为与加入发布包之前完全一样。
* **发布模式**：Nuitka 编译的发布包。资源在程序所在目录，**不依赖仓库路径**；数据目录默认
  放在平台标准位置（Windows ``%LOCALAPPDATA%/GelInspect``，macOS
  ``~/Library/Application Support/GelInspect``），可以用环境变量 ``GELINSPECT_DATA_DIR``、
  命令行 ``--data-dir`` 或配置文件里的 ``data_dir`` 改。

判断依据：构建脚本生成的 ``gelinspect._build_info`` 模块存在，**并且**代码是被 Nuitka 编译的
（模块全局变量里有 ``__compiled__``）。仓库里即使残留了 ``_build_info.py``，也仍是仓库模式。
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import gelinspect

DATA_DIR_ENV = "GELINSPECT_DATA_DIR"
"""覆盖数据目录的环境变量。"""

APP_DIR_NAME = "GelInspect"
"""平台标准数据目录下的子目录名。"""

PACKAGED_CONFIG = Path("config") / "gelinspect.yaml"
"""发布包内默认配置的位置（相对程序目录）。"""

USER_CONFIG_FILE = "config.yaml"
"""发布模式下，数据目录里的部署配置文件名（存在时优先于发布包内的默认配置）。"""


class RunMode(StrEnum):
    """运行模式。"""

    REPOSITORY = "repository"
    """从仓库源码运行。"""
    PACKAGED = "packaged"
    """从发布包运行。"""


@dataclass(frozen=True, slots=True)
class BuildInfo:
    """构建信息（由 ``scripts/build.py`` 生成并编译进程序）。

    Attributes:
        version: 版本号。
        build_id: 构建标识（时间和提交）。
        variant: ``release``（不含模拟）或 ``demo``（含模拟和演示账号）。
        manifest_public_key: 校验文件清单签名的 Ed25519 公钥（十六进制）。
    """

    version: str
    build_id: str
    variant: str
    manifest_public_key: str


def _compiled() -> bool:
    return "__compiled__" in globals()


def build_info() -> BuildInfo | None:
    """读取构建信息。

    Returns:
        发布模式下的构建信息；仓库模式为 None。
    """
    if not _compiled():
        return None
    try:
        module = importlib.import_module("gelinspect._build_info")
    except ImportError:
        return None
    return BuildInfo(
        version=str(module.VERSION),
        build_id=str(module.BUILD_ID),
        variant=str(module.VARIANT),
        manifest_public_key=str(module.MANIFEST_PUBLIC_KEY),
    )


def run_mode() -> RunMode:
    """当前的运行模式。

    Returns:
        仓库模式或发布模式。
    """
    return RunMode.PACKAGED if build_info() is not None else RunMode.REPOSITORY


def project_root() -> Path:
    """定位仓库根目录：从包目录向上找 ``pyproject.toml``，找不到时用当前目录。

    Returns:
        仓库根目录。
    """
    here = Path(gelinspect.__file__).resolve().parent
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def app_root(mode: RunMode | None = None) -> Path:
    """资源的根目录：仓库模式是仓库根目录，发布模式是程序所在目录。

    发布包里 ``gelinspect`` 包目录（含字体、图标、迁移脚本）就在程序目录下，
    翻译、配置、``simulation`` 也在这里。

    Args:
        mode: 运行模式，默认自动判断。

    Returns:
        资源根目录。
    """
    if (mode or run_mode()) is RunMode.PACKAGED:
        return Path(gelinspect.__file__).resolve().parent.parent
    return project_root()


def platform_data_dir(
    platform: str | None = None, environ: Mapping[str, str] | None = None
) -> Path:
    """平台标准的应用数据目录。

    Args:
        platform: 平台名（``sys.platform`` 的取值），测试注入。
        environ: 环境变量，测试注入。

    Returns:
        Windows ``%LOCALAPPDATA%/GelInspect``；macOS
        ``~/Library/Application Support/GelInspect``；其他系统 ``$XDG_DATA_HOME/gelinspect``。
    """
    current = platform or sys.platform
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if current == "win32":
        base = env.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / APP_DIR_NAME
    if current == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_DIR_NAME.lower()


def default_data_dir(mode: RunMode | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """配置文件没写 ``data_dir`` 时用的数据目录。

    Args:
        mode: 运行模式，默认自动判断。
        environ: 环境变量，测试注入。

    Returns:
        ``GELINSPECT_DATA_DIR``（设置了时）；否则仓库模式是仓库下的 ``data/``，
        发布模式是平台标准位置。
    """
    env = os.environ if environ is None else environ
    override = env.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if (mode or run_mode()) is RunMode.PACKAGED:
        return platform_data_dir(environ=env)
    return project_root() / "data"


def translations_dir() -> Path:
    """翻译文件目录（``.ts`` 和 ``.qm``）。

    Returns:
        资源根目录下的 ``translations/``。
    """
    return app_root() / "translations"
