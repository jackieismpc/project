"""仪器设置的数据模型、校验和用户配置文件。

仪器设置保存在数据目录下的 ``user_settings.json``，**不改仓库里的默认配置**。文件里只记录与
默认配置不同的项（``overrides``）：默认配置以后更新了，没改过的项会自动跟着更新；一项改回默认值
就从文件里消失。启动时把文件叠加到默认配置上，文件有错（手改坏了）就报 ``ConfigError`` 并说明
怎么办，不会悄悄忽略后用另一套参数跑试验。

输入先是文字（:class:`SettingsDraft`，界面的输入框），由 :func:`parse_draft` 一次性校验，
把每个不合法的字段连同原因（:class:`SettingsProblem`）一起报出来，界面逐项提示。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gelinspect.services.config import AppConfig, ConfigError

USER_SETTINGS_FILE = "user_settings.json"
"""数据目录下保存用户设置的文件名。"""

SCHEMA_VERSION = 1
"""文件格式版本。"""

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
"""图片文件夹里认作图片的扩展名（小写）。"""

MAX_INSTRUMENT_NO_LENGTH = 64
MAX_HOLDER_LENGTH = 120


class VideoInput(StrEnum):
    """视频输入的类型。实时摄像头尚未实现，工业相机等型号确定后接入，所以这里没有它们。"""

    CONFIG = "config"
    """沿用配置文件里的视频源（模拟或接入的真实相机）。"""
    FILE = "file"
    """视频文件，循环回放。"""
    IMAGES = "images"
    """图片文件夹，按文件名顺序循环回放。"""


@dataclass(frozen=True, slots=True)
class InstrumentSettings:
    """生效的仪器设置。

    Attributes:
        instrument_no: 仪器编号，写入每个新试验。
        incubation_minutes: 每排孵育时长（分钟）。
        frames_per_side: 翻转前后各取多少帧。
        settle_s: 翻转结束后的稳定时间（秒），之后的帧才算翻转后的帧。
        temperature_target_c: 孵育温度的目标值（℃）。
        temperature_tolerance_c: 允许的温度偏差（℃），范围是目标值 ± 偏差。
        temperature_hold_s: 温度越限持续多久转为异常（秒）。
        video_input: 视频输入类型。
        video_path: 视频文件或图片文件夹的路径；沿用配置文件时为空。
        retention_days: 视频和图像的保留天数，None 表示不自动删除。
        copyright_holder: 报告页脚的版权方名称。
    """

    instrument_no: str
    incubation_minutes: float
    frames_per_side: int
    settle_s: float
    temperature_target_c: float
    temperature_tolerance_c: float
    temperature_hold_s: float
    video_input: VideoInput
    video_path: str
    retention_days: int | None
    copyright_holder: str

    @property
    def temperature_range_c(self) -> tuple[float, float]:
        """有效温度范围（下限，上限）。"""
        return (
            self.temperature_target_c - self.temperature_tolerance_c,
            self.temperature_target_c + self.temperature_tolerance_c,
        )


FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(InstrumentSettings))
"""全部设置项的名称，也是用户配置文件里的键。"""

IMPACTING_FIELDS: frozenset[str] = frozenset(
    {
        "incubation_minutes",
        "frames_per_side",
        "settle_s",
        "temperature_target_c",
        "temperature_tolerance_c",
        "temperature_hold_s",
        "video_input",
        "video_path",
    }
)
"""影响试验运行的设置项：试验进行中不能修改。其余项（仪器编号、保留天数、版权方）随时可改。"""

NUMBER_LIMITS: dict[str, tuple[float, float]] = {
    "incubation_minutes": (0.1, 1440.0),
    "frames_per_side": (3, 20),
    "settle_s": (0.0, 10.0),
    "temperature_target_c": (20.0, 45.0),
    "temperature_tolerance_c": (0.1, 5.0),
    "temperature_hold_s": (0.0, 3600.0),
    "retention_days": (1, 36500),
}
"""数值项的允许范围（含两端）。"""

_INTEGER_FIELDS = frozenset({"frames_per_side", "retention_days"})


class SettingsProblemCode(StrEnum):
    """设置项不合法的原因，界面据此生成当前语言的提示。"""

    REQUIRED = "required"
    NOT_A_NUMBER = "not_a_number"
    NOT_AN_INTEGER = "not_an_integer"
    OUT_OF_RANGE = "out_of_range"
    TOO_LONG = "too_long"
    BAD_CHARACTERS = "bad_characters"
    PATH_MISSING = "path_missing"
    NOT_A_FILE = "not_a_file"
    NOT_A_FOLDER = "not_a_folder"
    NO_IMAGES = "no_images"


@dataclass(frozen=True, slots=True)
class SettingsProblem:
    """一个不合法的设置项。

    Attributes:
        name: 设置项名称（:data:`FIELD_NAMES` 之一）。
        code: 原因。
        params: 提示里的参数，例如范围的 ``min``、``max``，路径 ``path``。
    """

    name: str
    code: SettingsProblemCode
    params: Mapping[str, float | str] = field(default_factory=dict)

    def describe(self) -> str:
        """简体中文说明，写日志和异常信息用。

        Returns:
            例如「incubation_minutes：超出范围 0.1–1440」。
        """
        extra = "、".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}：{self.code.value}" + (f"（{extra}）" if extra else "")


@dataclass(frozen=True, slots=True)
class SettingsDraft:
    """界面输入框里的内容（全是文字），保存前由 :func:`parse_draft` 校验。

    Attributes:
        instrument_no: 仪器编号。
        incubation_minutes: 孵育时长（分钟）。
        frames_per_side: 翻转前后各取的帧数。
        settle_s: 翻转后稳定时间（秒）。
        temperature_target_c: 目标温度（℃）。
        temperature_tolerance_c: 允许偏差（℃）。
        temperature_hold_s: 越限告警持续时间（秒）。
        video_input: 视频输入类型。
        video_path: 路径。
        retention_days: 保留天数；留空表示不自动删除。
        copyright_holder: 版权方名称。
    """

    instrument_no: str
    incubation_minutes: str
    frames_per_side: str
    settle_s: str
    temperature_target_c: str
    temperature_tolerance_c: str
    temperature_hold_s: str
    video_input: VideoInput
    video_path: str
    retention_days: str
    copyright_holder: str

    @classmethod
    def from_settings(cls, settings: InstrumentSettings) -> SettingsDraft:
        """由生效的设置生成输入框内容。

        Args:
            settings: 生效的设置。

        Returns:
            草稿。
        """
        return cls(
            instrument_no=settings.instrument_no,
            incubation_minutes=f"{settings.incubation_minutes:g}",
            frames_per_side=str(settings.frames_per_side),
            settle_s=f"{settings.settle_s:g}",
            temperature_target_c=f"{settings.temperature_target_c:g}",
            temperature_tolerance_c=f"{settings.temperature_tolerance_c:g}",
            temperature_hold_s=f"{settings.temperature_hold_s:g}",
            video_input=settings.video_input,
            video_path=settings.video_path,
            retention_days="" if settings.retention_days is None else str(settings.retention_days),
            copyright_holder=settings.copyright_holder,
        )


def _parse_number(
    name: str, text: str, problems: list[SettingsProblem], *, lenient: bool = False
) -> float | int | None:
    value_text = text.strip()
    if not value_text:
        problems.append(SettingsProblem(name, SettingsProblemCode.REQUIRED))
        return None
    low, high = NUMBER_LIMITS[name]
    shown = {"min": low, "max": high}
    try:
        number = float(value_text)
    except ValueError:
        problems.append(SettingsProblem(name, SettingsProblemCode.NOT_A_NUMBER, shown))
        return None
    if not math.isfinite(number):
        problems.append(SettingsProblem(name, SettingsProblemCode.NOT_A_NUMBER, shown))
        return None
    if name in _INTEGER_FIELDS:
        if number != int(number) or any(ch in value_text for ch in ".eE"):
            problems.append(SettingsProblem(name, SettingsProblemCode.NOT_AN_INTEGER, shown))
            return None
        number = int(number)
    if not lenient and not low <= number <= high:
        problems.append(SettingsProblem(name, SettingsProblemCode.OUT_OF_RANGE, shown))
        return None
    return number


def check_video_path(kind: VideoInput, raw: str, problems: list[SettingsProblem]) -> str:
    """检查视频路径：存在，且类型与视频输入一致（文件，或含图片的文件夹）。

    Args:
        kind: 视频输入类型。
        raw: 输入的路径。
        problems: 发现的问题追加到这里。

    Returns:
        规整成绝对路径的文字；有问题时可能不可用。
    """
    text = raw.strip()
    if not text:
        problems.append(SettingsProblem("video_path", SettingsProblemCode.REQUIRED))
        return ""
    path = Path(os.path.abspath(os.path.expanduser(text)))
    shown: dict[str, float | str] = {"path": str(path)}
    if not path.exists():
        problems.append(SettingsProblem("video_path", SettingsProblemCode.PATH_MISSING, shown))
    elif kind is VideoInput.FILE and not path.is_file():
        problems.append(SettingsProblem("video_path", SettingsProblemCode.NOT_A_FILE, shown))
    elif kind is VideoInput.IMAGES:
        if not path.is_dir():
            problems.append(SettingsProblem("video_path", SettingsProblemCode.NOT_A_FOLDER, shown))
        elif not any(p.suffix.lower() in IMAGE_SUFFIXES for p in path.iterdir() if p.is_file()):
            problems.append(SettingsProblem("video_path", SettingsProblemCode.NO_IMAGES, shown))
    return str(path)


def _check_text(name: str, text: str, maximum: int, problems: list[SettingsProblem]) -> str:
    value = text.strip()
    if not value:
        problems.append(SettingsProblem(name, SettingsProblemCode.REQUIRED))
    elif len(value) > maximum:
        problems.append(SettingsProblem(name, SettingsProblemCode.TOO_LONG, {"max": maximum}))
    elif any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        problems.append(SettingsProblem(name, SettingsProblemCode.BAD_CHARACTERS))
    return value


def parse_draft(
    draft: SettingsDraft,
    *,
    check_paths: bool = True,
    current: SettingsDraft | None = None,
) -> tuple[InstrumentSettings | None, list[SettingsProblem]]:
    """校验输入框的内容。

    Args:
        draft: 输入框内容。
        check_paths: 是否检查视频路径存在且类型对。
        current: 当前生效设置对应的草稿。没有改动的项只要求格式正确，不再要求在允许范围内
            （配置文件里写的值可以超出界面允许的范围，不能因此让用户改别的项保存不了），
            没有改动的视频路径也不再检查是否存在。

    Returns:
        ``(设置, [])``；有任何不合法的项时设置为 None，问题列表逐项说明原因。
    """
    problems: list[SettingsProblem] = []
    instrument_no = _check_text(
        "instrument_no", draft.instrument_no, MAX_INSTRUMENT_NO_LENGTH, problems
    )
    holder = _check_text("copyright_holder", draft.copyright_holder, MAX_HOLDER_LENGTH, problems)

    def unchanged(name: str) -> bool:
        return current is not None and getattr(draft, name) == getattr(current, name)

    numbers = {
        name: _parse_number(name, getattr(draft, name), problems, lenient=unchanged(name))
        for name in NUMBER_LIMITS
        if name != "retention_days"
    }
    retention: int | None = None
    if draft.retention_days.strip():
        parsed = _parse_number(
            "retention_days", draft.retention_days, problems, lenient=unchanged("retention_days")
        )
        retention = None if parsed is None else int(parsed)
    path = ""
    if draft.video_input is not VideoInput.CONFIG:
        if check_paths and not (unchanged("video_input") and unchanged("video_path")):
            path = check_video_path(draft.video_input, draft.video_path, problems)
        else:
            path = draft.video_path.strip()
            if not path:
                problems.append(SettingsProblem("video_path", SettingsProblemCode.REQUIRED))
    if problems:
        return None, problems
    return (
        InstrumentSettings(
            instrument_no=instrument_no,
            incubation_minutes=float(numbers["incubation_minutes"] or 0),
            frames_per_side=int(numbers["frames_per_side"] or 0),
            settle_s=float(numbers["settle_s"] or 0),
            temperature_target_c=float(numbers["temperature_target_c"] or 0),
            temperature_tolerance_c=float(numbers["temperature_tolerance_c"] or 0),
            temperature_hold_s=float(numbers["temperature_hold_s"] or 0),
            video_input=draft.video_input,
            video_path=path,
            retention_days=retention,
            copyright_holder=holder,
        ),
        [],
    )


# ---------------------------------------------------------------------- 与配置的互转


def settings_of(config: AppConfig) -> InstrumentSettings:
    """配置里当前生效的仪器设置。

    Args:
        config: 应用配置。

    Returns:
        仪器设置；视频源是 ``file`` 或 ``images`` 别名时带上路径，否则是「沿用配置文件」。
    """
    video = config.video
    kind = VideoInput(video.source) if video.source in ("file", "images") else VideoInput.CONFIG
    return InstrumentSettings(
        instrument_no=config.instrument_no,
        incubation_minutes=config.engine.incubation_minutes,
        frames_per_side=config.engine.frames_per_side,
        settle_s=video.snapshot.settle_s,
        temperature_target_c=config.engine.temperature_target_c,
        temperature_tolerance_c=config.engine.temperature_tolerance_c,
        temperature_hold_s=config.engine.temperature_hold_s,
        video_input=kind,
        video_path=video.device if kind is not VideoInput.CONFIG else "",
        retention_days=config.retention_days,
        copyright_holder=config.reports.copyright_holder,
    )


def apply_settings(base: AppConfig, settings: InstrumentSettings) -> AppConfig:
    """把仪器设置叠加到默认配置上，得到运行用的配置。

    Args:
        base: 默认配置（没有叠加过用户设置的）。
        settings: 要生效的设置。

    Returns:
        新配置，``base`` 不变。
    """
    engine = base.engine.model_copy(
        update={
            "incubation_minutes": settings.incubation_minutes,
            "frames_per_side": settings.frames_per_side,
            "temperature_target_c": settings.temperature_target_c,
            "temperature_tolerance_c": settings.temperature_tolerance_c,
            "temperature_hold_s": settings.temperature_hold_s,
        }
    )
    video_update: dict[str, Any] = {
        "snapshot": base.video.snapshot.model_copy(
            update={"frames_per_side": settings.frames_per_side, "settle_s": settings.settle_s}
        )
    }
    if settings.video_input is not VideoInput.CONFIG:
        video_update.update(
            source=settings.video_input.value,
            device=settings.video_path,
            options={"loop": True},  # 试验比一段视频长得多：播完从头再来，不能断流
        )
    return base.model_copy(
        update={
            "instrument_no": settings.instrument_no,
            "engine": engine,
            "video": base.video.model_copy(update=video_update),
            "retention_days": settings.retention_days,
            "reports": base.reports.model_copy(
                update={"copyright_holder": settings.copyright_holder}
            ),
        }
    )


# ---------------------------------------------------------------------- 用户配置文件


class _Overrides(BaseModel):
    """用户配置文件里 ``settings`` 的格式：全部可选，未知字段报错（防止拼写错误被忽略）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_no: str = Field(min_length=1, max_length=MAX_INSTRUMENT_NO_LENGTH)
    incubation_minutes: float = Field(ge=0.1, le=1440)
    frames_per_side: int = Field(ge=3, le=20)
    settle_s: float = Field(ge=0, le=10)
    temperature_target_c: float = Field(ge=20, le=45)
    temperature_tolerance_c: float = Field(ge=0.1, le=5)
    temperature_hold_s: float = Field(ge=0, le=3600)
    video_input: VideoInput
    video_path: str
    retention_days: int | None = Field(ge=1, le=36500)
    copyright_holder: str = Field(min_length=1, max_length=MAX_HOLDER_LENGTH)


def settings_file(data_dir: Path) -> Path:
    """用户配置文件的路径。

    Args:
        data_dir: 数据目录。

    Returns:
        ``data_dir / user_settings.json``。
    """
    return data_dir / USER_SETTINGS_FILE


def load_overrides(path: Path) -> dict[str, Any]:
    """读取用户配置文件里与默认配置不同的项。

    Args:
        path: 文件路径，不存在表示没有改过任何设置。

    Returns:
        设置项名到值的字典，只含文件里写了的项。

    Raises:
        ConfigError: 文件读不了、不是合法的 JSON，或内容有误。
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"无法读取用户设置文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"用户设置文件 {path} 不是合法的 JSON：{exc}。请修正它，或删除这个文件以恢复默认设置"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
        raise ConfigError(f"用户设置文件 {path} 的格式不对：顶层应有 settings 字典")
    if data.get("version") != SCHEMA_VERSION:
        raise ConfigError(f"用户设置文件 {path} 的版本不受支持：{data.get('version')!r}")
    raw: dict[str, Any] = data["settings"]
    unknown = sorted(set(raw) - set(FIELD_NAMES))
    if unknown:
        raise ConfigError(f"用户设置文件 {path} 里有未知的设置项：{'、'.join(unknown)}")
    # 模型要求全部必填，文件只写了部分项：用占位值补齐后校验，只返回文件里写了的。
    probe = {**_FILLERS, **raw}
    try:
        model = _Overrides.model_validate(probe)
    except ValidationError as exc:
        raise ConfigError(
            f"用户设置文件 {path} 的内容有误：{exc}。请修正它，或删除这个文件以恢复默认设置"
        ) from exc
    return {name: getattr(model, name) for name in raw}


_FILLERS: dict[str, Any] = {
    "instrument_no": "x",
    "incubation_minutes": 1.0,
    "frames_per_side": 3,
    "settle_s": 0.0,
    "temperature_target_c": 37.0,
    "temperature_tolerance_c": 1.0,
    "temperature_hold_s": 0.0,
    "video_input": "config",
    "video_path": "",
    "retention_days": None,
    "copyright_holder": "x",
}
"""校验用的占位值：让只写了部分项的文件也能过模型的必填检查，占位值不会被返回。"""


def apply_overrides(base: AppConfig, overrides: Mapping[str, Any]) -> AppConfig:
    """把用户配置文件的内容叠加到默认配置上。

    Args:
        base: 默认配置。
        overrides: :func:`load_overrides` 的结果。

    Returns:
        运行用的配置；没有任何改动时就是 ``base``。
    """
    if not overrides:
        return base
    return apply_settings(base, replace(settings_of(base), **dict(overrides)))


def overrides_between(base: InstrumentSettings, new: InstrumentSettings) -> dict[str, Any]:
    """新设置里与默认设置不同的项（写入用户配置文件的内容）。

    Args:
        base: 默认配置下的设置。
        new: 要保存的设置。

    Returns:
        设置项名到值的字典，视频类型写成字符串。
    """
    result: dict[str, Any] = {}
    for name in FIELD_NAMES:
        value = getattr(new, name)
        if value != getattr(base, name):
            result[name] = value.value if isinstance(value, VideoInput) else value
    return result


def write_overrides(path: Path, overrides: Mapping[str, Any]) -> None:
    """原子地写用户配置文件；没有任何改动时删除文件。

    Args:
        path: 文件路径。
        overrides: 与默认配置不同的项。

    Raises:
        OSError: 写入失败（目录不可写、磁盘满）。
    """
    if not overrides:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"version": SCHEMA_VERSION, "settings": dict(overrides)},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".user_settings-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload + "\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
