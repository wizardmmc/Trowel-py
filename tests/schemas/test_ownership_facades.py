import ast
import importlib
from pathlib import Path

import trowel_py


def test_production_modules_import_schemas_from_the_owning_domain() -> None:
    package_root = Path(trowel_py.__file__).parent
    violations: list[str] = []

    for path in sorted(package_root.rglob("*.py")):
        if path.parent == package_root / "schemas":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "trowel_py.schemas"
            ):
                violations.append(f"{path.relative_to(package_root)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                if any(alias.name.startswith("trowel_py.schemas") for alias in node.names):
                    violations.append(f"{path.relative_to(package_root)}:{node.lineno}")

    assert violations == []


def test_legacy_schema_paths_reexport_owner_objects() -> None:
    ownership = {
        "trowel_py.schemas.agent_host": {
            "trowel_py.agent_host.events": ["AgentEvent"],
        },
        "trowel_py.schemas.cc_host": {
            "trowel_py.cc_host.schemas": [
                "CreateSessionRequest",
                "TextEvent",
                "ToolCallEvent",
                "WorkflowTreeEvent",
            ],
        },
        "trowel_py.schemas.api": {
            "trowel_py.cards.schemas": [
                "ExtractRequest",
                "CardDraft",
                "ReviewRequest",
                "CardListResponse",
            ],
            "trowel_py.review.schemas": ["SubmitRequest"],
        },
        "trowel_py.schemas.card": {
            "trowel_py.cards.models": ["Card"],
        },
        "trowel_py.schemas.extracted_card": {
            "trowel_py.cards.schemas": ["ExtractedCard", "ExtractOutput"],
        },
        "trowel_py.schemas.re_explain": {
            "trowel_py.cards.schemas": ["ReExplainRequest", "ReExplainResultSchema"],
        },
        "trowel_py.schemas.follow_up": {
            "trowel_py.cards.schemas": ["FollowUpMessageSchema"],
        },
        "trowel_py.schemas.review": {
            "trowel_py.review.models": ["FSRSState", "ReviewLog"],
        },
        "trowel_py.schemas.feynman": {
            "trowel_py.feynman.schemas": [
                "FeynmanQuestionSchema",
                "FeynmanEvaluationSchema",
            ],
        },
        "trowel_py.schemas.event": {
            "trowel_py.events.models": ["EventLog"],
        },
        "trowel_py.schemas.garden": {
            "trowel_py.garden.schemas": ["PlantInfo", "GardenStats"],
        },
        "trowel_py.schemas.pet": {
            "trowel_py.pet.models": ["Pet"],
            "trowel_py.pet.schemas": ["FeedRequest", "EquipRequest"],
        },
        "trowel_py.schemas.player": {
            "trowel_py.player.models": ["Player", "PlayerProfile", "InventoryItem"],
            "trowel_py.player.schemas": ["BuyItemRequest"],
        },
    }

    for legacy_name, owners in ownership.items():
        legacy = importlib.import_module(legacy_name)
        for owner_name, names in owners.items():
            owner = importlib.import_module(owner_name)
            for name in names:
                owned = getattr(owner, name)
                assert getattr(legacy, name) is owned
                assert owned.__module__ == owner_name
