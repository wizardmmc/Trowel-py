/** 计算多人长文本卡片不落单的稳定平衡行。 */

/** 按创建顺序生成每行最多三项、末行不留单项的索引分组。 */
export function balancedRows<T>(items: readonly T[]): readonly (readonly T[])[] {
  const rows: T[][] = [];
  let index = 0;
  let remaining = items.length;
  while (remaining > 0) {
    let size: number;
    if (remaining <= 3) size = remaining;
    else if (remaining === 4) size = 2;
    else if (remaining === 7) size = 3;
    else size = 3;
    rows.push(items.slice(index, index + size));
    index += size;
    remaining -= size;
  }
  return rows;
}
