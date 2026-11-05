import pytest

from gelinspect.domain.enums import ExperimentType, TubeRole
from gelinspect.domain.errors import InvalidPlatePositionError
from gelinspect.domain.templates import BUILTIN_TEMPLATES, ExperimentTemplate, TemplateTubeItem


def test_all_five_types_have_a_template() -> None:
    assert set(BUILTIN_TEMPLATES) == set(ExperimentType)
    for experiment_type, template in BUILTIN_TEMPLATES.items():
        assert template.experiment_type is experiment_type
    assert len({t.template_id for t in BUILTIN_TEMPLATES.values()}) == 5


def test_sensitivity_template_matches_specification() -> None:
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    assert template.needs_confirmation is False
    assert template.tube_count == 17
    assert [item.tube_no for item in template.items] == list(range(1, 18))
    assert [item.lambda_multiple for item in template.items] == [
        *[2.0] * 4,
        *[1.0] * 4,
        *[0.5] * 4,
        *[0.25] * 4,
        0.0,
    ]
    assert all(item.spiked_endotoxin is None for item in template.items)
    assert all(item.dilution_factor is None for item in template.items)
    assert [item.role for item in template.items] == [
        *[TubeRole.TEST] * 16,
        TubeRole.NEGATIVE_CONTROL,
    ]
    assert template.items[0].sample_name == "灵敏度复核"
    assert template.items[16].sample_name == "阴性对照"


@pytest.mark.parametrize(
    ("experiment_type", "count"),
    [
        (ExperimentType.INTERFERENCE, 28),
        (ExperimentType.RETEST, 10),
        (ExperimentType.SEMI_QUANTITATIVE, 16),
        (ExperimentType.LIMIT, 8),
    ],
)
def test_presumed_templates_are_flagged_and_invent_nothing(
    experiment_type: ExperimentType, count: int
) -> None:
    template = BUILTIN_TEMPLATES[experiment_type]
    assert template.needs_confirmation is True
    assert template.tube_count == count
    assert [item.tube_no for item in template.items] == list(range(1, count + 1))
    for item in template.items:
        assert item.role is TubeRole.UNCONFIRMED
        assert item.dilution_factor is None
        assert item.spiked_endotoxin is None


def test_template_rejects_duplicates_and_empty() -> None:
    item = TemplateTubeItem(tube_no=1, sample_name="s", role=TubeRole.TEST)
    with pytest.raises(ValueError):
        ExperimentTemplate("t", "t", ExperimentType.LIMIT, (item, item))
    with pytest.raises(ValueError):
        ExperimentTemplate("t", "t", ExperimentType.LIMIT, ())


def test_item_rejects_bad_values() -> None:
    with pytest.raises(InvalidPlatePositionError):
        TemplateTubeItem(tube_no=81, sample_name="s", role=TubeRole.TEST)
    with pytest.raises(ValueError):
        TemplateTubeItem(tube_no=1, sample_name="s", role=TubeRole.TEST, dilution_factor=-1.0)


def test_sensitivity_spikes_follow_lambda() -> None:
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    assert template.requires_lambda
    values = [item.spike_for(0.25) for item in template.items]
    assert values == [*[0.5] * 4, *[0.25] * 4, *[0.125] * 4, *[0.0625] * 4, 0.0]
    assert [item.spike_for(0.03) for item in template.items][:16:4] == [0.06, 0.03, 0.015, 0.0075]
    assert template.items[0].spike_for(None) is None


def test_absolute_spike_is_kept_for_old_custom_items() -> None:
    item = TemplateTubeItem(1, "样品", TubeRole.TEST, spiked_endotoxin=0.3)
    assert item.spike_for(0.5) == 0.3
    assert item.spike_for(None) == 0.3


def test_unconfirmed_templates_do_not_need_lambda() -> None:
    assert not BUILTIN_TEMPLATES[ExperimentType.LIMIT].requires_lambda


def test_negative_multiple_is_rejected() -> None:
    with pytest.raises(ValueError):
        TemplateTubeItem(1, "样品", TubeRole.TEST, lambda_multiple=-1.0)
