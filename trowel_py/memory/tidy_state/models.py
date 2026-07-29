"""Tidy 成功水位的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .periods import _valid_period


@dataclass(frozen=True)
class TidyState:
    """记录周级和月级 Tidy 水位。

    Attributes:
        weekly_last: 最近记录的成功 ISO 周，约定格式为 ``YYYY-Www``；空水位为
            ``None``。
        monthly_last: 最近记录的成功月份，约定格式为 ``YYYY-MM``；空水位为
            ``None``。
        updated_at: 调用方在最近一次覆盖水位时一同记录的时间文本；模型本身不
            解析或校验。
    """

    weekly_last: str | None = None
    monthly_last: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        """按状态文件结构展开当前字段。

        Returns:
            顶层固定包含 ``weekly``、``monthly`` 和 ``updated_at``；前两项
            分别是含 ``last_successful`` 的字典，字段值为 ``None`` 时也保留。
        """
        return {
            "weekly": {"last_successful": self.weekly_last},
            "monthly": {"last_successful": self.monthly_last},
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: object) -> "TidyState":
        """从状态文件字段恢复水位。

        非字典输入返回空状态；缺失、类型错误或格式非法的周月周期分别置空。
        ``updated_at`` 不做类型校验，按原值传入数据类。

        Args:
            d: 解码后的状态文件值。

        Returns:
            经过周月周期校验的状态。
        """
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
        """返回覆盖周水位和更新时间的新状态。

        周期不做格式或前后顺序校验，月水位保持不变。

        Args:
            period: 新的周水位文本，约定格式为 ``YYYY-Www``。
            updated_at: 新的更新时间文本。
        """
        return replace(
            self,
            weekly_last=period,
            updated_at=updated_at,
        )

    def with_monthly(self, period: str, updated_at: str) -> "TidyState":
        """返回覆盖月水位和更新时间的新状态。

        周期不做格式或前后顺序校验，周水位保持不变。

        Args:
            period: 新的月水位文本，约定格式为 ``YYYY-MM``。
            updated_at: 新的更新时间文本。
        """
        return replace(
            self,
            monthly_last=period,
            updated_at=updated_at,
        )
