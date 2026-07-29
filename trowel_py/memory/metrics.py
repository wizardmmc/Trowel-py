"""计算离线检索评估使用的集合 precision、recall 和有序 precision@k。

``precision`` 与 ``recall`` 把检索项和相关项都转成集合，忽略顺序与重复；
``precision_at_k`` 则保留检索结果的位置和重复项，只把相关项转成集合。
"""

from __future__ import annotations

from collections.abc import Iterable


def precision(retrieved: Iterable[str], relevant: Iterable[str]) -> float:
    """计算去重后的检索结果中相关项目所占的比例。

    两个输入都会完整转成集合，原始顺序不参与计算。

    Args:
        retrieved: 检索到的项目标识；重复项只计一次。
        relevant: 预先标注为相关的项目标识；重复项只计一次。

    Returns:
        取值范围为 0.0 到 1.0 的 precision；没有检索结果时为 0.0。
    """
    rel = set(relevant)
    got = set(retrieved)
    if not got:
        return 0.0
    return len(got & rel) / len(got)


def recall(retrieved: Iterable[str], relevant: Iterable[str]) -> float:
    """计算去重后的相关项目中被检索到的比例。

    函数先把相关项转成集合；为空时直接返回 0.0，不遍历检索项。否则再把检索项
    转成集合，原始顺序和重复均不参与计算。

    Args:
        retrieved: 检索到的项目标识；重复项只计一次。
        relevant: 预先标注为相关的项目标识；重复项只计一次。

    Returns:
        取值范围为 0.0 到 1.0 的 recall；没有相关项目时为 0.0。
    """
    rel = set(relevant)
    if not rel:
        return 0.0
    got = set(retrieved)
    return len(got & rel) / len(rel)


def precision_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """计算前 ``k`` 个检索位置的 precision。

    与集合 precision 不同，同一相关标识在检索结果的多个位置出现时会分别计为
    命中；相关项自身的重复标注不重复计数。结果不足 ``k`` 项时仍以 ``k`` 为
    分母；``k`` 小于或等于 0 时返回 0.0，且不会读取相关项。

    Args:
        retrieved: 按排名顺序排列的项目标识。
        relevant: 预先标注为相关的项目标识。
        k: 纳入计算的排名位置数。

    Returns:
        取值范围为 0.0 到 1.0 的 precision@k。
    """
    if k <= 0:
        return 0.0
    rel = set(relevant)
    top = retrieved[:k]
    hits = sum(1 for r in top if r in rel)
    return hits / k
