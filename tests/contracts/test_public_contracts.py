from __future__ import annotations

import json

import pytest

from tests.contracts.public_contracts import (
    SNAPSHOT_PATH,
    _without_sql_comments,
    capture_public_contracts,
)


@pytest.fixture(scope="module")
def contracts() -> tuple[dict, dict]:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return expected, capture_public_contracts()


@pytest.mark.parametrize(
    "section",
    ("cli_help", "database_schemas", "event_types", "openapi"),
)
def test_public_contract_matches_snapshot(
    contracts: tuple[dict, dict], section: str
) -> None:
    expected, actual = contracts
    assert actual[section] == expected[section], (
        f"{section} changed; inspect the contract before running "
        "`python -m tests.contracts.public_contracts --update`"
    )


def test_schema_snapshot_ignores_comments_but_preserves_sql_strings() -> None:
    sql = (
        "CREATE TABLE sample (value TEXT DEFAULT '--keep', "
        "other TEXT DEFAULT '/* keep */') -- remove this\n"
        "/* remove this too */;"
    )
    assert _without_sql_comments(sql) == (
        "CREATE TABLE sample (value TEXT DEFAULT '--keep', "
        "other TEXT DEFAULT '/* keep */') ;"
    )
