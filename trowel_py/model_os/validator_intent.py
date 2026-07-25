"""遇到不明确命令时拒绝匹配的 validator intent 识别。"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import PurePath

from trowel_py.model_os.cognitive_signals import (
    EvidenceRef,
    ValidatorIntent,
    ValidatorOutcomePayload,
)


_DANGEROUS_CHARS = frozenset(";&|$`><\\\n\r*?(){}!#~")

_NO_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "pytest": frozenset({"-q", "-v", "-x"}),
    "mypy": frozenset(
        {"--ignore-missing-imports", "--strict", "--no-error-summary"}
    ),
    "ruff": frozenset({"--fix"}),
    "tsc": frozenset({"-b", "--noEmit"}),
}
_VALUE_FLAGS: dict[str, dict[str, bool]] = {
    "pytest": {"-p": False},
    "ruff": {"--select": True, "--ignore": True},
    "tsc": {"-p": True, "--project": True},
}
_FIXED_EQUALS_FLAGS: dict[str, frozenset[str]] = {
    "pytest": frozenset({"--tb=short", "--tb=long"}),
}
_TARGET_RULES = frozenset({"path", "path_or_package", "project_or_config"})
_STRUCTURAL_TARGET = re.compile(r"[A-Za-z0-9_./:@+-]{1,512}")
_STRUCTURAL_VALUE = re.compile(r"[A-Za-z0-9_.,:@+-]{1,256}")

DEFAULT_VALIDATOR_REGISTRY: tuple[ValidatorIntent, ...] = (
    ValidatorIntent(
        "pytest",
        "pytest",
        "pytest",
        ("-q", "-v", "-x", "--tb=short", "--tb=long", "-p"),
        "path",
    ),
    ValidatorIntent(
        "mypy",
        "mypy",
        "mypy",
        ("--ignore-missing-imports", "--strict", "--no-error-summary"),
        "path_or_package",
    ),
    ValidatorIntent(
        "ruff",
        "ruff",
        "ruff",
        ("check", "--fix", "--select", "--ignore"),
        "path",
    ),
    ValidatorIntent(
        "tsc",
        "tsc",
        "tsc",
        ("-b", "--noEmit", "-p", "--project"),
        "project_or_config",
    ),
)


def has_dangerous_metacharacters(command: str) -> bool:
    return any(char in command for char in _DANGEROUS_CHARS)


def match_validator_intent_from_argv(
    argv: tuple[str, ...],
    registry: tuple[ValidatorIntent, ...] = DEFAULT_VALIDATOR_REGISTRY,
) -> ValidatorIntent | None:
    if not argv:
        return None
    for intent in registry:
        validator_args = _validator_args(intent, argv)
        if validator_args is None:
            continue
        return intent if _normalized_target(intent, validator_args) is not None else None
    return None


def normalize_validator_target(
    intent: ValidatorIntent, argv: tuple[str, ...]
) -> str | None:
    """从已匹配 argv 派生稳定 target；不信任调用方自报分组。"""

    validator_args = _validator_args(intent, argv)
    if validator_args is None:
        return None
    return _normalized_target(intent, validator_args)


def validator_persistence_argv(
    intent: ValidatorIntent, argv: tuple[str, ...]
) -> tuple[str, ...] | None:
    """只保留 intent 与完整 argv 哈希；原 argv 归 native evidence 所有。"""

    if match_validator_intent_from_argv(argv, (intent,)) is None:
        return None
    canonical = json.dumps(argv, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return (f"validator:{intent.intent_id}", f"argv_sha256:{digest}")


def match_validator_intent_from_shell(
    command: str,
    registry: tuple[ValidatorIntent, ...] = DEFAULT_VALIDATOR_REGISTRY,
) -> ValidatorIntent | None:
    if has_dangerous_metacharacters(command):
        return None
    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError:
        return None
    return match_validator_intent_from_argv(argv, registry)


def build_validator_outcome(
    *,
    intent: ValidatorIntent,
    argv: tuple[str, ...],
    exit_event_ref: EvidenceRef,
    output_ref: EvidenceRef | None,
    native_tool_item_id: str,
    exit_code: int,
    completed: bool,
) -> ValidatorOutcomePayload | None:
    if not completed or exit_event_ref.invocation_id != native_tool_item_id:
        return None
    if output_ref is None or output_ref.invocation_id != native_tool_item_id:
        return None
    matched = match_validator_intent_from_argv(argv, (intent,))
    if matched is None:
        return None
    return ValidatorOutcomePayload(
        intent_id=intent.intent_id,
        native_tool_item_id=native_tool_item_id,
        normalized_argv=argv,
        exit_event_ref=exit_event_ref,
        output_ref=output_ref,
        exit_code=exit_code,
        completed=True,
    )


def _validator_args(
    intent: ValidatorIntent, argv: tuple[str, ...]
) -> tuple[str, ...] | None:
    if not argv:
        return None
    executable = PurePath(argv[0]).name
    if executable == intent.executable:
        return argv[1:]
    if (
        re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable) is not None
        and len(argv) >= 3
        and argv[1] == "-m"
        and argv[2] == intent.executable
    ):
        return argv[3:]
    if (
        intent.intent_id == "tsc"
        and executable == "bun"
        and len(argv) >= 3
        and argv[1:3] == ("run", "typecheck")
    ):
        return argv[3:]
    return None


def _normalized_target(
    intent: ValidatorIntent, args: tuple[str, ...]
) -> str | None:
    if intent.target_rule not in _TARGET_RULES:
        return None
    targets: list[str] = []
    index = 0
    while index < len(args):
        part = args[index]
        if part == "check":
            if intent.intent_id != "ruff" or index != 0:
                return None
            index += 1
            continue
        if part.startswith("-"):
            flag = part.split("=", 1)[0]
            if part not in intent.allowed_flags and flag not in intent.allowed_flags:
                return None
            if "=" in part:
                value = part.split("=", 1)[1]
                if not value:
                    return None
                if part in _FIXED_EQUALS_FLAGS.get(intent.intent_id, ()):
                    index += 1
                    continue
                value_flags = _VALUE_FLAGS.get(intent.intent_id, {})
                if not value_flags.get(flag, False):
                    return None
                value_pattern = (
                    _STRUCTURAL_TARGET
                    if intent.intent_id == "tsc"
                    else _STRUCTURAL_VALUE
                )
                if value_pattern.fullmatch(value) is None:
                    return None
                if intent.intent_id == "tsc":
                    targets.append(value)
            elif flag in _NO_VALUE_FLAGS.get(intent.intent_id, ()):
                index += 1
                continue
            elif flag in _VALUE_FLAGS.get(intent.intent_id, {}):
                if index + 1 >= len(args):
                    return None
                value = args[index + 1]
                if value.startswith("-"):
                    return None
                value_pattern = (
                    _STRUCTURAL_TARGET
                    if intent.intent_id == "tsc"
                    else _STRUCTURAL_VALUE
                )
                if value_pattern.fullmatch(value) is None:
                    return None
                if intent.intent_id == "tsc" and flag in {"-p", "--project"}:
                    targets.append(value)
                index += 1
            else:
                return None
            index += 1
            continue
        if _STRUCTURAL_TARGET.fullmatch(part) is None:
            return None
        targets.append(part)
        index += 1
    canonical = sorted(set(targets))
    if not canonical:
        return "."
    if len(canonical) == 1:
        return canonical[0]
    return "[" + ",".join(canonical) + "]"
