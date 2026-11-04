"""分层与权限无关：ORM 不外泄，仓储不做权限判断。"""

from __future__ import annotations

import ast
from pathlib import Path

from gelinspect.infrastructure.db import Database

SRC = Path(__file__).parents[3] / "src" / "gelinspect"
DB = SRC / "infrastructure" / "db"


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_db_package_is_not_imported_outside_infrastructure() -> None:
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for layer in ("domain", "interfaces", "services", "ui")
        for path in (SRC / layer).rglob("*.py")
        for module in imports_of(path)
        if module.startswith(("gelinspect.infrastructure", "sqlalchemy", "alembic"))
    ]
    assert violations == []


def test_repositories_do_not_depend_on_permissions() -> None:
    """权限由服务层检查，数据层对谁调用都一视同仁。"""
    offenders = [
        str(path.relative_to(SRC))
        for path in DB.rglob("*.py")
        if "gelinspect.domain.permissions" in imports_of(path)
    ]
    assert offenders == []


def test_repository_results_are_domain_objects(database: Database) -> None:
    from gelinspect.domain.entities import Experiment
    from gelinspect.interfaces.storage import AuditRecord, ExperimentQuery, PageRequest

    from .conftest import make_event, make_experiment

    database.experiments.save(make_experiment())
    database.audit.append(make_event())
    assert isinstance(database.experiments.get("E1"), Experiment)
    summary = database.experiments.query(ExperimentQuery(), PageRequest()).items[0]
    assert type(summary).__module__ == "gelinspect.interfaces.storage"
    assert isinstance(database.audit.get(1), AuditRecord)
