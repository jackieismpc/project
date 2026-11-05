"""构建当前平台的发布包（Nuitka standalone 目录模式，不是 onefile）。

用法::

    uv run python scripts/build.py                    # 正式包：不含 simulation/，无演示账号
    uv run python scripts/build.py --with-simulation  # 演示包：带模拟后端和演示账号

产物放在 ``dist/``（已被 git 忽略），中间文件在 ``build/``：

* macOS：``GelInspect.app``，再打成 ``.dmg``（``hdiutil`` 不可用时打 ``.zip``）。
  没有 Apple 签名，只做 ad-hoc 签名，首次打开的放行步骤见 ``docs/deployment.md``。
* Windows：目录 ``GelInspect/``（内含 ``GelInspect.exe``）和同名 ``.zip``；
  可选再用 ``packaging/windows/gelinspect.iss``（Inno Setup）做安装程序。

每个包都带：字体和图标、构建时生成的 ``.qm`` 翻译、数据库迁移脚本、默认配置、
文件清单（SHA-256，Ed25519 签名，启动时校验）和第三方许可声明（``licenses/``）。

构建步骤：生成 ``gelinspect/_build_info.py``（版本、构建标识、清单公钥；构建完删除）→
``lrelease`` → Nuitka → 复制数据文件 → 许可声明 → 检查（正式包不得含 simulation）→
清单签名 →（macOS）重新 ad-hoc 签名 → 打包 → ``dist/build-report-<变体>.json``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PACKAGE = SRC / "gelinspect"
BUILD_INFO = PACKAGE / "_build_info.py"
TRANSLATIONS = ROOT / "translations"
PACKAGING = ROOT / "packaging"
MIGRATIONS = PACKAGE / "infrastructure" / "db" / "migrations"

sys.path.insert(0, str(SRC))

from gelinspect import __version__  # noqa: E402
from gelinspect.infrastructure.security.integrity import (  # noqa: E402
    MANIFEST_FILE,
    SIGNATURE_FILE,
    build_manifest,
    verify_manifest,
)
from gelinspect.services.config import DEFAULT_CONFIG  # noqa: E402

NOFOLLOW = (
    "pytest",
    "_pytest",
    "pytestqt",
    "mypy",
    "mypyc",
    "nuitka",
    "setuptools",
    "pip",
    "ruff",
    "IPython",
    "matplotlib",
    "tkinter",
)
"""不跟随导入的模块：开发工具和用不到的可选依赖。"""

EXCLUDED_DISTRIBUTIONS: frozenset[str] = frozenset()
"""许可声明里不列出的依赖（没有打进包的）。pyHanko 在导入时就需要 aiohttp，所以它也在包里。"""


def log(message: str) -> None:
    """打印带时间的进度。"""
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def run(command: Sequence[str], *, env: dict[str, str] | None = None) -> None:
    """运行命令，失败时退出。"""
    log("$ " + " ".join(command))
    completed = subprocess.run(list(command), cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"命令失败（返回码 {completed.returncode}）：{command[0]}")


def platform_tag() -> str:
    """平台标识，例如 ``macos-arm64``、``windows-x64``。"""
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    return f"{system}-{arch}"


def git_revision() -> str:
    """当前提交的短哈希，没有 git 时为 ``nogit``。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "nogit"
    return completed.stdout.strip() or "nogit"


# ---------------------------------------------------------------------- 清单签名密钥


def manifest_key(path: str | None) -> tuple[object, str]:
    """清单签名私钥：指定了 PEM 就读取，否则为本次构建临时生成（不落盘）。

    Returns:
        （私钥，公钥十六进制）。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if path:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SystemExit("GELINSPECT_MANIFEST_KEY 必须是 Ed25519 私钥（PEM）")
    else:
        key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return key, public.hex()


def write_build_info(variant: str, build_id: str, public_key: str) -> None:
    """生成编译进程序的构建信息模块。"""
    BUILD_INFO.write_text(
        '"""构建信息（由 scripts/build.py 生成，构建结束后删除，不入库）。"""\n\n'
        f"VERSION = {json.dumps(__version__)}\n"
        f"BUILD_ID = {json.dumps(build_id)}\n"
        f"VARIANT = {json.dumps(variant)}\n"
        f"MANIFEST_PUBLIC_KEY = {json.dumps(public_key)}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------- 翻译


def lrelease_tool() -> Path:
    """PySide6 自带的 lrelease。"""
    import PySide6

    package = Path(PySide6.__file__).resolve().parent
    for name in ("lrelease.exe", "lrelease"):
        if (package / name).is_file():
            return package / name
    raise SystemExit("找不到 lrelease，请先运行 uv sync")


def build_translations(target: Path) -> None:
    """把 ``.ts`` 编译成 ``.qm``，连同 ``.ts``（报告英文文字直接读 ``.ts``）放到 ``target``。"""
    target.mkdir(parents=True, exist_ok=True)
    tool = lrelease_tool()
    for ts in sorted(TRANSLATIONS.glob("*.ts")):
        shutil.copy2(ts, target / ts.name)
        if ts.stem.endswith("_zh_CN"):
            continue  # 源语言不需要 .qm
        run([str(tool), "-silent", str(ts), "-qm", str(target / f"{ts.stem}.qm")])


# ---------------------------------------------------------------------- 许可声明


def runtime_distributions(with_simulation: bool) -> list[metadata.Distribution]:
    """运行时依赖的闭包（不含开发依赖和可选依赖）。"""
    from packaging.requirements import Requirement

    seen: dict[str, metadata.Distribution] = {}
    pending = ["gelinspect"]
    while pending:
        name = pending.pop()
        key = name.lower().replace("_", "-")
        if key in seen or key in EXCLUDED_DISTRIBUTIONS:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = dist
        for raw in dist.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            pending.append(requirement.name)
    seen.pop("gelinspect", None)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """分发包自带的许可文件（LICENSE、COPYING、NOTICE 等）。"""
    found: list[tuple[str, str]] = []
    for file in dist.files or ():
        name = Path(str(file)).name.upper()
        if not any(word in name for word in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            continue
        if name.endswith((".PY", ".PYC", ".J2")):
            continue
        path = Path(str(dist.locate_file(file)))
        try:
            found.append((str(file), path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return found


def license_name(dist: metadata.Distribution) -> str:
    """分发包声明的许可证。"""
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    declared = meta.get("License")
    if declared and len(declared) < 120:
        return str(declared)
    classifiers = [
        c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if "License" in c
    ]
    return "、".join(classifiers) or "见下方许可文件"


def write_notices(target: Path, with_simulation: bool) -> int:
    """写第三方许可声明目录 ``licenses/``。

    Returns:
        列出的依赖数。
    """
    target.mkdir(parents=True, exist_ok=True)
    assets = PACKAGE / "ui" / "assets"
    for source in (
        PACKAGING / "licenses" / "LGPL-3.0.txt",
        PACKAGING / "licenses" / "GPL-3.0.txt",
        PACKAGING / "licenses" / "LGPL-NOTICE.md",
        assets / "fonts" / "OFL-NotoSansSC.txt",
        assets / "fonts" / "OFL-IBMPlexMono.txt",
        assets / "icons" / "LICENSE.lucide.txt",
    ):
        shutil.copy2(source, target / source.name)
    python_license = (
        Path(sys.base_prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "LICENSE.txt"
    )
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE.txt"  # Windows 的布局
    if python_license.is_file():
        shutil.copy2(python_license, target / "Python-LICENSE.txt")
    nuitka_dist = metadata.distribution("nuitka")
    for file in nuitka_dist.files or ():
        if Path(str(file)).name == "LICENSE-RUNTIME.txt":
            shutil.copy2(
                Path(str(nuitka_dist.locate_file(file))), target / "Nuitka-LICENSE-RUNTIME.txt"
            )
    dists = runtime_distributions(with_simulation)
    lines = [
        "GelInspect 第三方组件许可声明 / Third-party notices",
        "=" * 60,
        "",
        "本软件使用下列开源组件。Qt / PySide6 / Shiboken6 以 LGPL-3.0 提供，",
        "以独立的动态库（目录模式）随软件分发，可以替换，见 LGPL-NOTICE.md。",
        "字体：Noto Sans SC、IBM Plex Mono（SIL Open Font License 1.1）。",
        "图标：Lucide（ISC）。编译器运行库：Nuitka Runtime Library Exception（见",
        "Nuitka-LICENSE-RUNTIME.txt）。Python 运行时：PSF License（见 Python-LICENSE.txt，",
        "其中列出了 CPython 附带的第三方组件）。",
        "",
        "组件列表 / Components",
        "-" * 60,
    ]
    for dist in dists:
        lines.append(f"{dist.metadata['Name']} {dist.version}  —  {license_name(dist)}")
    lines.append("")
    for dist in dists:
        texts = license_texts(dist)
        lines += ["", "=" * 60, f"{dist.metadata['Name']} {dist.version}", "=" * 60]
        lines.append(f"License: {license_name(dist)}")
        for name, text in texts:
            lines += ["", f"--- {name} ---", text.rstrip()]
    (target / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(dists)


# ---------------------------------------------------------------------- Nuitka


def executable_name() -> str:
    """主程序文件名。不能与包目录 ``gelinspect`` 重名（macOS 文件系统不区分大小写）。"""
    return "GelInspect" if sys.platform == "win32" else "gelinspect-app"


def nuitka_command(work: Path, variant: str, jobs: int, report: Path) -> list[str]:
    """Nuitka 命令行。"""
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=app-dist",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--include-package=gelinspect",
        "--include-module=gelinspect._build_info",
        "--include-package-data=gelinspect",
        f"--output-dir={work}",
        f"--output-filename={executable_name()}",
        f"--report={report}",
        f"--jobs={jobs}",
        "--company-name=GelInspect",
        "--product-name=GelInspect",
        f"--file-version={__version__}",
        f"--product-version={__version__}",
        "--file-description=GelInspect",
        "--windows-console-mode=attach",
        "--macos-app-name=GelInspect",
        f"--macos-app-version={__version__}",
        "--macos-signed-app-name=com.gelinspect.app",
    ]
    nofollow = list(NOFOLLOW)
    if variant == "demo":
        command.append("--include-package=simulation")
    else:
        nofollow.append("simulation")
    command.append("--nofollow-import-to=" + ",".join(nofollow))
    if shutil.which("ccache") is None:
        # Nuitka 自动下载的 macOS ccache 是 x86_64 版，在 Apple 芯片上会让 xcrun 失败。
        command.append("--disable-ccache")
    command.append(str(PACKAGING / "launcher.py"))
    return command


def locate_dist(work: Path) -> tuple[Path, Path | None]:
    """Nuitka 产物：（程序目录，macOS 的 .app；其他平台为 None）。"""
    if sys.platform == "darwin":
        apps = sorted(work.glob("*.app"))
        if not apps:
            raise SystemExit(f"{work} 下没有 .app")
        return apps[0] / "Contents" / "MacOS", apps[0]
    dists = sorted(work.glob("*.dist"))
    if not dists:
        raise SystemExit(f"{work} 下没有 .dist 目录")
    return dists[0], None


# ---------------------------------------------------------------------- 数据文件


def copy_tree(source: Path, target: Path, patterns: Iterable[str]) -> None:
    """按模式复制目录里的文件，保持相对路径。"""
    for pattern in patterns:
        for path in sorted(source.rglob(pattern)):
            if "__pycache__" in path.parts:
                continue
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def install_data(app_dir: Path, variant: str, staging: Path) -> None:
    """复制 Nuitka 不会自动带上的数据：迁移脚本、翻译、配置、模拟算法配置、许可声明。"""
    # Alembic 运行时按文件加载 env.py 和 versions/*.py，只编译进主程序不够。
    copy_tree(
        MIGRATIONS,
        app_dir / "gelinspect" / "infrastructure" / "db" / "migrations",
        ("*.py", "*.mako"),
    )
    shutil.copytree(staging / "translations", app_dir / "translations", dirs_exist_ok=True)
    config_dir = app_dir / "config"
    config_dir.mkdir(exist_ok=True)
    if variant == "demo":
        demo = {key: value for key, value in DEFAULT_CONFIG.items() if key != "data_dir"}
        demo["security"] = {"key_protection": "auto", "integrity_check": True}
        import yaml

        text = (
            "# GelInspect 演示包的默认配置：模拟设备、视频和算法，时间加速 60 倍，\n"
            "# 首次启动创建演示账号（密码公开）。不要用于真实检测。\n"
            + yaml.safe_dump(demo, allow_unicode=True, sort_keys=False)
        )
        (config_dir / "gelinspect.yaml").write_text(text, encoding="utf-8")
        simulation_dir = app_dir / "simulation"
        simulation_dir.mkdir(exist_ok=True)
        shutil.copy2(ROOT / "simulation" / "analyzer.example.yaml", simulation_dir)
    else:
        shutil.copy2(PACKAGING / "config" / "release.yaml", config_dir / "gelinspect.yaml")
    shutil.copy2(PACKAGING / "config" / "release.yaml", config_dir / "config.example.yaml")


def check_variant(app_dir: Path, report: Path, variant: str) -> None:
    """正式包里不能有 simulation：编译报告里没有它的模块，目录里也没有它的文件。"""
    text = report.read_text(encoding="utf-8")
    compiled = 'name="simulation' in text or "name='simulation" in text
    if variant == "release":
        if compiled:
            raise SystemExit("正式包的编译报告里出现了 simulation 模块")
        leftovers = [p for p in app_dir.rglob("*") if "simulation" in p.relative_to(app_dir).parts]
        if leftovers:
            raise SystemExit(f"正式包里有 simulation 文件：{leftovers[:3]}")
    elif not compiled:
        raise SystemExit("演示包的编译报告里没有 simulation 模块")


# ---------------------------------------------------------------------- 签名与打包


def adhoc_sign(app: Path, added: Sequence[Path]) -> None:
    """macOS：给构建后加入的数据文件补 ad-hoc 签名，再重新封装整个包。

    Nuitka 把数据文件放在 ``Contents/MacOS`` 里，并逐个签名（非 Mach-O 文件的签名存在扩展属性里，
    不改文件内容，所以不影响清单里的哈希）。我们后加的迁移脚本、翻译、配置、许可声明和清单
    也要这样签，否则外层签名会报「code object is not signed at all」。
    """
    for start in range(0, len(added), 200):
        run(["codesign", "--force", "--sign", "-", *map(str, added[start : start + 200])])
    run(["codesign", "--force", "--sign", "-", "--identifier", "com.gelinspect.app", str(app)])
    run(["codesign", "--verify", "--deep", "--strict", str(app)])


def regular_files(root: Path) -> set[Path]:
    """目录里的普通文件（不跟随符号链接）。"""
    return {p for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def make_archive(dist_dir: Path, bundle: Path, base_name: str) -> Path:
    """打包：macOS 先试 .dmg，失败用 .zip；其他平台 .zip。"""
    if sys.platform == "darwin":
        dmg = dist_dir / f"{base_name}.dmg"
        dmg.unlink(missing_ok=True)
        completed = subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                bundle.stem,
                "-srcfolder",
                str(bundle),
                "-ov",
                "-format",
                "UDZO",
                str(dmg),
            ],
            check=False,
        )
        if completed.returncode == 0 and dmg.is_file():
            return dmg
        log("hdiutil 打包失败，改用 zip")
        archive = dist_dir / f"{base_name}.zip"
        archive.unlink(missing_ok=True)
        run(["ditto", "-c", "-k", "--keepParent", str(bundle), str(archive)])
        return archive
    archive = shutil.make_archive(str(dist_dir / base_name), "zip", bundle.parent, bundle.name)
    return Path(archive)


def tree_size(path: Path) -> int:
    """目录的总字节数。"""
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def sha256(path: Path) -> str:
    """文件的 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------- 主流程


def build(args: argparse.Namespace) -> dict[str, object]:
    """执行一次构建，返回构建报告。"""
    started = time.monotonic()
    variant = "demo" if args.with_simulation else "release"
    tag = platform_tag()
    suffix = "-demo" if variant == "demo" else ""
    base_name = f"GelInspect-{__version__}-{tag}{suffix}"
    work = ROOT / "build" / variant
    dist_dir = ROOT / "dist"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    dist_dir.mkdir(exist_ok=True)
    build_id = f"{datetime.now(UTC):%Y%m%d%H%M%S}-{git_revision()}"
    private_key, public_hex = manifest_key(
        args.manifest_key or os.environ.get("GELINSPECT_MANIFEST_KEY")
    )
    staging = work / "staging"
    log(f"变体 {variant}，平台 {tag}，构建 {build_id}")
    build_translations(staging / "translations")
    write_build_info(variant, build_id, public_hex)
    report = work / "compilation-report.xml"
    env = dict(os.environ)
    env.setdefault("NUITKA_CACHE_DIR", str(ROOT / "build" / "nuitka-cache"))
    try:
        compile_started = time.monotonic()
        run(nuitka_command(work / "nuitka", variant, args.jobs, report), env=env)
        compile_s = time.monotonic() - compile_started
    finally:
        BUILD_INFO.unlink(missing_ok=True)
    app_dir, app_bundle = locate_dist(work / "nuitka")
    nuitka_files = regular_files(app_dir)
    install_data(app_dir, variant, staging)
    components = write_notices(app_dir / "licenses", args.with_simulation)
    check_variant(app_dir, report, variant)

    # 产物改名并移到 dist/
    if app_bundle is not None:
        bundle = dist_dir / ("GelInspect Demo.app" if variant == "demo" else "GelInspect.app")
    else:
        bundle = dist_dir / ("GelInspect-Demo" if variant == "demo" else "GelInspect")
    if bundle.exists():
        shutil.rmtree(bundle)
    shutil.move(str(app_bundle or app_dir), str(bundle))
    previous_dir = app_dir
    app_dir = bundle / "Contents" / "MacOS" if app_bundle is not None else bundle
    nuitka_files = {app_dir / p.relative_to(previous_dir) for p in nuitka_files}

    exclude = [executable_name()] if sys.platform == "darwin" else []
    count = build_manifest(
        app_dir,
        private_key,  # type: ignore[arg-type]
        version=__version__,
        build_id=build_id,
        variant=variant,
        exclude=exclude,
    )
    if sys.platform == "darwin":
        adhoc_sign(bundle, sorted(regular_files(app_dir) - nuitka_files))
    check = verify_manifest(app_dir, public_hex)
    if not check.ok:
        raise SystemExit(f"清单自检失败：{check.summary()}")
    archive = None if args.no_archive else make_archive(dist_dir, bundle, base_name)
    elapsed = time.monotonic() - started
    result: dict[str, object] = {
        "variant": variant,
        "platform": tag,
        "version": __version__,
        "build_id": build_id,
        "bundle": str(bundle.relative_to(ROOT)),
        "bundle_bytes": tree_size(bundle),
        "archive": None if archive is None else str(archive.relative_to(ROOT)),
        "archive_bytes": None if archive is None else archive.stat().st_size,
        "archive_sha256": None if archive is None else sha256(archive),
        "manifest_files": count,
        "manifest": [MANIFEST_FILE, SIGNATURE_FILE],
        "third_party_components": components,
        "compile_seconds": round(compile_s, 1),
        "total_seconds": round(elapsed, 1),
        "python": platform.python_version(),
    }
    (dist_dir / f"build-report-{variant}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="构建 GelInspect 当前平台的发布包")
    parser.add_argument(
        "--with-simulation",
        action="store_true",
        help="构建演示包：带 simulation/ 模拟后端和演示账号（正式包默认排除）",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="并行编译数")
    parser.add_argument(
        "--manifest-key",
        help="清单签名用的 Ed25519 私钥 PEM（默认每次构建临时生成；"
        "也可用环境变量 GELINSPECT_MANIFEST_KEY）",
    )
    parser.add_argument("--no-archive", action="store_true", help="只生成目录或 .app，不打包")
    args = parser.parse_args(argv)
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
