"""Router operator 配置及 native catalog 校验。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from trowel_py.model_os.routing.models import RouteCandidate, RouteMode
from trowel_py.model_os.work_broker import ModelTier


@dataclass(frozen=True)
class RoutingConfig:
    mode: RouteMode
    runtimes: dict[str, tuple[RouteCandidate, ...]]

    def candidates(self, runtime: str) -> tuple[RouteCandidate, ...]:
        return self.runtimes.get(runtime, ())

    def candidate(self, runtime: str, tier: ModelTier) -> RouteCandidate | None:
        return next(
            (item for item in self.candidates(runtime) if item.tier is tier),
            None,
        )

    def mode_for(self, runtime: str) -> RouteMode:
        if self.candidate(runtime, ModelTier.FAST) is None:
            return RouteMode.OFF
        return self.mode

    def requires_catalog(self, runtime: str) -> bool:
        return self.mode is not RouteMode.OFF and bool(self.candidates(runtime))


def load_routing_config(path: Path | None = None) -> RoutingConfig:
    """读取 ``[model_os.routing]``；缺失或损坏时关闭 Router。"""

    if path is None:
        root = Path(__file__).resolve().parents[3]
        path = next(
            (
                item
                for item in (
                    Path.cwd() / "config.toml",
                    Path.home() / ".trowel" / "config.toml",
                    root / "config.toml",
                )
                if item.is_file()
            ),
            root / "config.toml",
        )
    if not path.is_file():
        return RoutingConfig(RouteMode.OFF, {})
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        raw = data.get("model_os", {}).get("routing", {})
        if not isinstance(raw, Mapping):
            return RoutingConfig(RouteMode.OFF, {})
        mode = RouteMode(str(raw.get("mode", RouteMode.OFF.value)))
        runtimes: dict[str, tuple[RouteCandidate, ...]] = {}
        for runtime in ("claude_code", "codex"):
            runtime_value = raw.get(runtime)
            if not isinstance(runtime_value, Mapping):
                continue
            candidates = tuple(
                candidate
                for tier in (ModelTier.FAST, ModelTier.DEEP)
                if (candidate := _parse_candidate(runtime_value.get(tier.value), tier))
                is not None
            )
            if candidates:
                runtimes[runtime] = candidates
        return RoutingConfig(mode, runtimes)
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError):
        return RoutingConfig(RouteMode.OFF, {})


def _parse_candidate(value: object, tier: ModelTier) -> RouteCandidate | None:
    if not isinstance(value, Mapping):
        return None
    model = value.get("model")
    effort = value.get("effort")
    if not isinstance(model, str) or not model.strip():
        return None
    if effort is not None and (not isinstance(effort, str) or not effort.strip()):
        return None
    cap = value.get("budget_cap")
    if cap is not None and not isinstance(cap, dict):
        return None
    return RouteCandidate(tier, model, effort, cap)


def validate_routing_config(
    config: RoutingConfig,
    *,
    cc_catalog: Sequence[Any],
    codex_catalog: Sequence[Mapping[str, Any]],
) -> RoutingConfig:
    """只保留 native runtime 当前明确支持的候选。"""

    cc_rows = {str(item.value): item for item in cc_catalog}
    codex_rows = {str(item.get("model")): item for item in codex_catalog}
    validated: dict[str, tuple[RouteCandidate, ...]] = {}
    for runtime, candidates in config.runtimes.items():
        accepted: list[RouteCandidate] = []
        for candidate in candidates:
            if candidate.model is None:
                continue
            if runtime == "claude_code":
                row = cc_rows.get(candidate.model)
                if row is not None:
                    accepted.append(
                        RouteCandidate(
                            candidate.tier,
                            str(row.real_model),
                            candidate.effort,
                            candidate.budget_cap,
                            request_model=candidate.model,
                        )
                    )
                continue
            if runtime != "codex":
                continue
            row = codex_rows.get(candidate.model)
            if row is None or candidate.effort is None:
                continue
            efforts = {
                str(item.get("value")) if isinstance(item, Mapping) else str(item)
                for item in row.get("supported_efforts", ())
            }
            if candidate.effort in efforts:
                accepted.append(
                    RouteCandidate(
                        candidate.tier,
                        candidate.model,
                        candidate.effort,
                        candidate.budget_cap,
                        request_model=candidate.model,
                    )
                )
        if accepted:
            validated[runtime] = tuple(accepted)
    return RoutingConfig(config.mode, validated)
