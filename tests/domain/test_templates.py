import pytest

from gelinspect.domain.enums import ExperimentType, TubeRole
from gelinspect.domain.errors import InvalidPlatePositionError
from gelinspect.domain.templates import (
    ALL_BUILTIN_TEMPLATES,
    BUILTIN_TEMPLATES,
    LIMIT_RETEST_TEMPLATE,
    ExperimentTemplate,
    TemplateTubeItem,
)


def test_every_type_has_a_template() -> None:
    assert set(BUILTIN_TEMPLATES) == set(ExperimentType)
    for experiment_type, template in BUILTIN_TEMPLATES.items():
        assert template.experiment_type is experiment_type
    assert len({t.template_id for t in BUILTIN_TEMPLATES.values()}) == 4


def test_limit_retest_template_belongs_to_limit_test() -> None:
    assert LIMIT_RETEST_TEMPLATE.experiment_type is ExperimentType.LIMIT
    assert LIMIT_RETEST_TEMPLATE.tube_count == 10 and not LIMIT_RETEST_TEMPLATE.needs_confirmation
    roles = [item.role for item in LIMIT_RETEST_TEMPLATE.items]
    assert roles.count(TubeRole.SAMPLE) == 4
    ids = [t.template_id for t in ALL_BUILTIN_TEMPLATES]
    assert len(ids) == len(set(ids)) == 5 and LIMIT_RETEST_TEMPLATE in ALL_BUILTIN_TEMPLATES


def test_sensitivity_template_matches_specification() -> None:
    """药典：2λ、λ、λ/2、λ/4 各 4 管，阴性对照 2 管。"""
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    assert template.needs_confirmation is False
    assert template.tube_count == 18
    assert [item.tube_no for item in template.items] == list(range(1, 19))
    assert [item.lambda_multiple for item in template.items] == [
        *[2.0] * 4,
        *[1.0] * 4,
        *[0.5] * 4,
        *[0.25] * 4,
        0.0,
        0.0,
    ]
    assert all(item.spiked_endotoxin is None for item in template.items)
    assert all(item.dilution_factor is None for item in template.items)
    assert [item.role for item in template.items] == [
        *[TubeRole.TEST] * 16,
        *[TubeRole.NEGATIVE_CONTROL] * 2,
    ]
    assert template.items[0].sample_name == "内毒素标准系列"
    assert template.items[16].sample_name == "阴性对照"


@pytest.mark.parametrize(
    ("experiment_type", "count", "roles"),
    [
        (
            ExperimentType.INTERFERENCE,
            28,
            {
                TubeRole.SAMPLE: 2,
                TubeRole.SAMPLE_SERIES: 16,
                TubeRole.TEST: 8,
                TubeRole.NEGATIVE_CONTROL: 2,
            },
        ),
        (
            ExperimentType.SEMI_QUANTITATIVE,
            20,
            {
                TubeRole.SAMPLE: 8,
                TubeRole.SAMPLE_POSITIVE_CONTROL: 2,
                TubeRole.TEST: 8,
                TubeRole.NEGATIVE_CONTROL: 2,
            },
        ),
        (
            ExperimentType.LIMIT,
            8,
            {
                TubeRole.SAMPLE: 2,
                TubeRole.SAMPLE_POSITIVE_CONTROL: 2,
                TubeRole.POSITIVE_CONTROL: 2,
                TubeRole.NEGATIVE_CONTROL: 2,
            },
        ),
    ],
)
def test_builtin_layouts_follow_the_pharmacopoeia(
    experiment_type: ExperimentType, count: int, roles: dict[TubeRole, int]
) -> None:
    template = BUILTIN_TEMPLATES[experiment_type]
    assert not template.needs_confirmation
    assert template.tube_count == count
    assert [item.tube_no for item in template.items] == list(range(1, count + 1))
    found = [item.role for item in template.items]
    assert {role: found.count(role) for role in roles} == roles
    assert TubeRole.UNCONFIRMED not in found
    for item in template.items:
        if item.role in (TubeRole.SAMPLE_POSITIVE_CONTROL, TubeRole.POSITIVE_CONTROL):
            assert item.lambda_multiple == 2.0


def test_template_rejects_duplicates_and_empty() -> None:
    item = TemplateTubeItem(tube_no=1, sample_name="s", role=TubeRole.TEST)
    with pytest.raises(ValueError):
        ExperimentTemplate("t", "t", ExperimentType.LIMIT, (item, item))
    with pytest.raises(ValueError):
        ExperimentTemplate("t", "t", ExperimentType.LIMIT, ())


def test_item_rejects_bad_values() -> None:
    with pytest.raises(InvalidPlatePositionError):
        TemplateTubeItem(tube_no=97, sample_name="s", role=TubeRole.TEST)
    with pytest.raises(ValueError):
        TemplateTubeItem(tube_no=1, sample_name="s", role=TubeRole.TEST, dilution_factor=-1.0)


def test_sensitivity_spikes_follow_lambda() -> None:
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    assert template.requires_lambda
    values = [item.spike_for(0.25) for item in template.items]
    assert values == [*[0.5] * 4, *[0.25] * 4, *[0.125] * 4, *[0.0625] * 4, 0.0, 0.0]
    assert [item.spike_for(0.03) for item in template.items][:16:4] == [0.06, 0.03, 0.015, 0.0075]
    assert template.items[0].spike_for(None) is None


def test_absolute_spike_is_kept_for_old_custom_items() -> None:
    item = TemplateTubeItem(1, "样品", TubeRole.TEST, spiked_endotoxin=0.3)
    assert item.spike_for(0.5) == 0.3
    assert item.spike_for(None) == 0.3


def test_templates_with_spiked_controls_need_lambda() -> None:
    assert all(t.requires_lambda for t in ALL_BUILTIN_TEMPLATES)
    plain = ExperimentTemplate(
        "t", "t", ExperimentType.LIMIT, (TemplateTubeItem(1, "样品", TubeRole.SAMPLE),)
    )
    assert not plain.requires_lambda


def test_negative_multiple_is_rejected() -> None:
    with pytest.raises(ValueError):
        TemplateTubeItem(1, "样品", TubeRole.TEST, lambda_multiple=-1.0)
