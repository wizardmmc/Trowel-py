"""Tidy 成功水位的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .periods import _valid_period


@dataclass(frozen=True)
class TidyState:
    """Weekly 与 monthly 的最近成功周期。"""

    weekly_last: str | None = None
    monthly_last: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "weekly": {"last_successful": self.weekly_last},
            "monthly": {"last_successful": self.monthly_last},
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: object) -> "TidyState":
        if not isinstance(d, dict):
            return cls()
        weekly = d.get("weekly")
        monthly = d.get("monthly")
        weekly = weekly if isinstance(weekly, dict) else {}
        monthly = monthly if isinstance(monthly, dict) else {}
        return cls(
            weekly_last=_valid_period(
                weekly.get("last_successful"),
                "weekly",
            ),
            monthly_last=_valid_period(
                monthly.get("last_successful"),
                "monthly",
            ),
            updated_at=d.get("updated_at"),
        )

    def with_weekly(self, period: str, updated_at: str) -> "TidyState":
        return replace(
            self,
            weekly_last=period,
            updated_at=updated_at,
        )

    def with_monthly(self, period: str, updated_at: str) -> "TidyState":
        return replace(
            self,
            monthly_last=period,
            updated_at=updated_at,
        )
