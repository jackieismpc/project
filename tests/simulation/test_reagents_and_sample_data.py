"""试剂白名单、示例试验数据和 simulation 的隔离。"""

import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from gelinspect.domain.entities import check_reagents
from gelinspect.domain.enums import (
    GelState,
    Interpretation,
    ReagentCheckStatus,
    ReagentKind,
)
from gelinspect.interfaces.traceability import (
    GovernmentTraceabilityClient,
    ReagentCodeFormatError,
    ReagentCodeParser,
    ReagentRegistry,
    ReagentRegistryUnavailableError,
    TraceabilityRecord,
    TraceabilitySubmissionError,
)
from simulation.reagents import (
    SAMPLE_CODES,
    SimReagentCodeParser,
    SimReagentRegistry,
    SimTraceabilityClient,
)
from simulation.sample_data import (
    SAMPLE_DATE,
    SENSITIVITY_ENDOTOXIN,
    SENSITIVITY_EXPECTED,
    SENSITIVITY_STATES,
    sensitivity_experiment,
    sensitivity_scenario,
)

ROOT = Path(__file__).parents[2]
TODAY = date(2026, 11, 4)


def test_implement_protocols() -> None:
    assert isinstance(SimReagentCodeParser(), ReagentCodeParser)
    assert isinstance(SimReagentRegistry(), ReagentRegistry)
    assert isinstance(SimTraceabilityClient(), GovernmentTraceabilityClient)


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("lal_valid", ReagentCheckStatus.VALID),
        ("lal_expired", ReagentCheckStatus.EXPIRED),
        ("lal_unregistered", ReagentCheckStatus.NOT_REGISTERED),
        ("endotoxin_valid", ReagentCheckStatus.VALID),
        ("water_valid", ReagentCheckStatus.VALID),
    ],
)
def test_sample_codes_cover_valid_expired_and_unregistered(
    name: str, status: ReagentCheckStatus
) -> None:
    code = SimReagentCodeParser().parse(SAMPLE_CODES[name])
    assert SimReagentRegistry().verify(code, TODAY).status is status


def test_expiry_depends_on_date() -> None:
    code = SimReagentCodeParser().parse(SAMPLE_CODES["lal_valid"])
    registry = SimReagentRegistry()
    assert registry.verify(code, date(2027, 6, 30)).status is ReagentCheckStatus.VALID
    assert registry.verify(code, date(2027, 7, 1)).status is ReagentCheckStatus.EXPIRED


def test_parser_fields_and_errors() -> None:
    code = SimReagentCodeParser().parse(SAMPLE_CODES["water_valid"])
    assert code.kind is ReagentKind.WATER and code.expiry_date is None
    assert code.batch_no == "WAT2607A" and code.serial_no == "0001"
    for bad in (
        "",
        "only-one",
        "a|b|unknown_kind",
        "a|b|water|2027",
        "|b|water",
        "a|b|water|1|2|3",
    ):
        with pytest.raises(ReagentCodeFormatError):
            SimReagentCodeParser().parse(bad)


def test_registry_unavailable_can_be_injected() -> None:
    code = SimReagentCodeParser().parse(SAMPLE_CODES["lal_valid"])
    with pytest.raises(ReagentRegistryUnavailableError):
        SimReagentRegistry(available=False).verify(code, TODAY)


def test_traceability_client() -> None:
    client = SimTraceabilityClient()
    assert client.is_enabled() is False
    client.enabled = True
    record = TraceabilityRecord(experiment_id="E1")
    assert client.submit(record).receipt_id == "SIM-000001"
    client.fail_next = True
    with pytest.raises(TraceabilitySubmissionError):
        client.submit(record)
    assert client.submit(record).receipt_id == "SIM-000002"


def test_sensitivity_sample_data() -> None:
    assert [SENSITIVITY_STATES[n] for n in range(1, 8)] == [
        *[GelState.FULLY_GELLED] * 6,
        GelState.PARTIALLY_GELLED,
    ]
    assert all(SENSITIVITY_STATES[n] is GelState.NOT_GELLED for n in range(8, 18))
    assert SENSITIVITY_EXPECTED[1] is Interpretation.POSITIVE
    assert SENSITIVITY_EXPECTED[7] is Interpretation.PENDING_REVIEW
    assert SENSITIVITY_EXPECTED[8] is Interpretation.NEGATIVE
    assert SENSITIVITY_EXPECTED[17] is Interpretation.NEGATIVE
    assert sorted(set(SENSITIVITY_ENDOTOXIN.values())) == [0.0, 0.125, 0.25, 0.5, 1.0]
    assert SENSITIVITY_ENDOTOXIN[17] == 0.0 and SENSITIVITY_ENDOTOXIN[1] == 1.0


def test_sensitivity_experiment_matches_template_and_reagents_pass() -> None:
    experiment = sensitivity_experiment()
    assert len(experiment.assignments) == 17
    assert check_reagents(experiment.reagents, SAMPLE_DATE).passed
    scenario = sensitivity_scenario()
    assert scenario.state_for(7) is GelState.PARTIALLY_GELLED
    assert scenario.state_for(18) is None


# ----- 隔离 -----


def run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(code)],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_main_code_imports_without_simulation_package() -> None:
    done = run_isolated(
        """
        import importlib, importlib.abc, pkgutil, sys

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name == "simulation" or name.startswith("simulation."):
                    raise ImportError("simulation 已被移走")
                return None

        sys.meta_path.insert(0, Blocker())
        import gelinspect
        failed = []
        for module in pkgutil.walk_packages(gelinspect.__path__, "gelinspect."):
            skipped = ("__main__", ".ui", ".migrations")
            if any(part in module.name for part in skipped):
                continue
            try:
                importlib.import_module(module.name)
            except Exception as exc:
                failed.append((module.name, repr(exc)))
        assert not failed, failed
        assert "simulation" not in sys.modules
        try:
            import simulation
        except ImportError:
            print("blocked")
        """
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "blocked" in done.stdout


def test_simulation_only_depends_on_domain_and_interfaces() -> None:
    import ast

    forbidden = ("gelinspect.services", "gelinspect.ui", "gelinspect.infrastructure")
    for path in (ROOT / "simulation").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            for name in [module, *names]:
                assert not (name and name.startswith(forbidden)), f"{path.name}: {name}"
