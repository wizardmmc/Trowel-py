/** 以 cc-switch 的紧凑触发器和月历结构选择 Statistics 自然日范围。 */

import { useMemo, useState } from "react";
import type { StatisticsDateRange } from "../domain/types";
import { StatisticsPopover } from "./StatisticsPopover";
import "./statistics-date-range.css";

type RangePreset = "today" | "1d" | "7d" | "14d" | "30d";
type DraftField = "start" | "end";

const PRESETS: readonly {
  readonly value: RangePreset;
  readonly label: string;
  readonly ariaLabel: string;
}[] = [
  { value: "today", label: "当天", ariaLabel: "当天" },
  { value: "1d", label: "1d", ariaLabel: "最近 1 个完整自然日" },
  { value: "7d", label: "7d", ariaLabel: "最近 7 个自然日" },
  { value: "14d", label: "14d", ariaLabel: "最近 14 个自然日" },
  { value: "30d", label: "30d", ariaLabel: "最近 30 个自然日" },
];

interface StatisticsDateRangePickerProps {
  /** 当前已生效的首尾自然日与解释时区。 */
  readonly value: StatisticsDateRange;
  /** 用户确认预设或自定义范围后提交新值。 */
  readonly onChange: (value: StatisticsDateRange) => void;
}

/** 展示紧凑日期触发器，并在独立浮层内处理预设和自定义自然日。 */
export function StatisticsDateRangePicker({
  value,
  onChange,
}: StatisticsDateRangePickerProps) {
  const [draftStart, setDraftStart] = useState(value.startDate);
  const [draftEnd, setDraftEnd] = useState(value.endDate);
  const [activeField, setActiveField] = useState<DraftField>("start");
  const [displayMonth, setDisplayMonth] = useState(() =>
    startOfMonth(parseLocalDate(value.startDate)),
  );
  const [error, setError] = useState<string | null>(null);
  const today = localIsoDate(new Date());
  const selectedPreset = inferPreset(value, today);
  const triggerLabel = selectedPreset
    ? PRESETS.find((preset) => preset.value === selectedPreset)?.label ?? "日期"
    : compactRangeLabel(value.startDate, value.endDate);
  const triggerTitle = `${value.startDate} 至 ${value.endDate}`;

  const calendarDays = useMemo(
    () => monthGrid(displayMonth),
    [displayMonth],
  );
  const resetDraft = () => {
    setDraftStart(value.startDate);
    setDraftEnd(value.endDate);
    setDisplayMonth(startOfMonth(parseLocalDate(value.startDate)));
    setActiveField("start");
    setError(null);
  };

  const applyPreset = (preset: RangePreset, close: () => void) => {
    onChange(presetRange(preset, value.timezone, today));
    close();
  };

  const applyCustom = (close: () => void) => {
    if (!isIsoDate(draftStart) || !isIsoDate(draftEnd)) {
      setError("请选择完整的开始和结束日期");
      return;
    }
    const days = inclusiveDayCount(draftStart, draftEnd);
    if (days < 1) {
      setError("开始日期不能晚于结束日期");
      return;
    }
    if (days > 366) {
      setError("自定义范围不能超过 366 天");
      return;
    }
    onChange({
      startDate: draftStart,
      endDate: draftEnd,
      timezone: value.timezone,
    });
    close();
  };

  const pickDay = (day: Date) => {
    const picked = localIsoDate(day);
    setError(null);
    if (activeField === "start") {
      setDraftStart(picked);
      if (picked > draftEnd) setDraftEnd(picked);
      setActiveField("end");
    } else if (picked < draftStart) {
      setDraftStart(picked);
      setActiveField("end");
    } else {
      setDraftEnd(picked);
    }
    if (
      day.getMonth() !== displayMonth.getMonth() ||
      day.getFullYear() !== displayMonth.getFullYear()
    ) {
      setDisplayMonth(startOfMonth(day));
    }
  };

  return (
    <StatisticsPopover
      triggerAriaLabel="选择统计日期范围"
      contentAriaLabel="选择统计日期范围"
      preferredWidth={620}
      triggerClassName="statistics-date-range__trigger"
      contentClassName="statistics-date-range__popover"
      onOpenChange={(open) => {
        if (open) resetDraft();
      }}
      triggerContent={
        <>
          <CalendarIcon />
          <span title={triggerTitle}>{triggerLabel}</span>
          <ChevronIcon />
        </>
      }
    >
      {(close) => (
        <div className="statistics-date-range">
          <div className="statistics-date-range__presets">
            {PRESETS.map((preset) => (
              <button
                key={preset.value}
                type="button"
                aria-label={preset.ariaLabel}
                aria-pressed={selectedPreset === preset.value}
                onClick={() => applyPreset(preset.value, close)}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div className="statistics-date-range__layout">
            <div className="statistics-date-range__fields">
              <p>支持自选自然日，最长 366 天</p>
              <DateField
                label="开始日期"
                ariaLabel="自定义开始日期"
                value={draftStart}
                active={activeField === "start"}
                onFocus={() => setActiveField("start")}
                onChange={(next) => {
                  setDraftStart(next);
                  if (isIsoDate(next)) {
                    setDisplayMonth(startOfMonth(parseLocalDate(next)));
                  }
                  setError(null);
                }}
              />
              <DateField
                label="结束日期"
                ariaLabel="自定义结束日期"
                value={draftEnd}
                active={activeField === "end"}
                onFocus={() => setActiveField("end")}
                onChange={(next) => {
                  setDraftEnd(next);
                  if (isIsoDate(next)) {
                    setDisplayMonth(startOfMonth(parseLocalDate(next)));
                  }
                  setError(null);
                }}
              />
              {error && <p className="statistics-date-range__error">{error}</p>}
              <div className="statistics-date-range__actions">
                <button type="button" onClick={close}>取消</button>
                <button
                  type="button"
                  className="is-primary"
                  aria-label="应用自定义日期"
                  onClick={() => applyCustom(close)}
                >
                  应用
                </button>
              </div>
            </div>
            <div className="statistics-date-range__calendar">
              <div className="statistics-date-range__month">
                <button
                  type="button"
                  aria-label="上个月"
                  onClick={() => setDisplayMonth(shiftMonth(displayMonth, -1))}
                >
                  ‹
                </button>
                <button
                  type="button"
                  aria-label="回到本月"
                  onClick={() => setDisplayMonth(startOfMonth(new Date()))}
                >
                  {displayMonth.getFullYear()}年{displayMonth.getMonth() + 1}月
                </button>
                <button
                  type="button"
                  aria-label="下个月"
                  onClick={() => setDisplayMonth(shiftMonth(displayMonth, 1))}
                >
                  ›
                </button>
              </div>
              <div className="statistics-date-range__weekdays" aria-hidden="true">
                {['日', '一', '二', '三', '四', '五', '六'].map((day) => (
                  <span key={day}>{day}</span>
                ))}
              </div>
              <div className="statistics-date-range__days">
                {calendarDays.map((day) => {
                  const date = localIsoDate(day);
                  const endpoint = date === draftStart || date === draftEnd;
                  const inRange = date >= draftStart && date <= draftEnd;
                  const currentMonth = day.getMonth() === displayMonth.getMonth();
                  return (
                    <button
                      key={date}
                      type="button"
                      aria-label={date}
                      aria-pressed={endpoint}
                      className={`${currentMonth ? "" : "is-outside"}${inRange ? " is-in-range" : ""}${endpoint ? " is-endpoint" : ""}${date === today ? " is-today" : ""}`.trim()}
                      onClick={() => pickDay(day)}
                    >
                      {day.getDate()}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </StatisticsPopover>
  );
}

/** 展示一个可切换月历编辑目标的原生日期输入。 */
function DateField({
  label,
  ariaLabel,
  value,
  active,
  onFocus,
  onChange,
}: {
  readonly label: string;
  readonly ariaLabel: string;
  readonly value: string;
  readonly active: boolean;
  readonly onFocus: () => void;
  readonly onChange: (value: string) => void;
}) {
  return (
    <label className={`statistics-date-range__field${active ? " is-active" : ""}`}>
      <span>{label}</span>
      <input
        type="date"
        aria-label={ariaLabel}
        value={value}
        onFocus={onFocus}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    </label>
  );
}

/** 把界面预设转换为后端支持的闭区间自然日范围。 */
function presetRange(
  preset: RangePreset,
  timezone: string,
  today: string,
): StatisticsDateRange {
  if (preset === "today") {
    return { startDate: today, endDate: today, timezone };
  }
  if (preset === "1d") {
    const yesterday = shiftIsoDate(today, -1);
    return { startDate: yesterday, endDate: yesterday, timezone };
  }
  const days = preset === "7d" ? 7 : preset === "14d" ? 14 : 30;
  return {
    startDate: shiftIsoDate(today, -(days - 1)),
    endDate: today,
    timezone,
  };
}

/** 判断当前范围是否与某个预设完全一致。 */
function inferPreset(
  value: StatisticsDateRange,
  today: string,
): RangePreset | null {
  return (
    PRESETS.find((preset) => {
      const expected = presetRange(preset.value, value.timezone, today);
      return (
        expected.startDate === value.startDate && expected.endDate === value.endDate
      );
    })?.value ?? null
  );
}

/** 把完整 ISO 日期压缩成触发器内的月日文字。 */
function compactRangeLabel(start: string, end: string): string {
  if (start === end) return start.slice(5).replace("-", "/");
  return `${start.slice(5).replace("-", "/")}–${end.slice(5).replace("-", "/")}`;
}

/** 计算首尾均包含的自然日数量。 */
function inclusiveDayCount(start: string, end: string): number {
  return Math.round(
    (parseLocalDate(end).getTime() - parseLocalDate(start).getTime()) /
      86_400_000,
  ) + 1;
}

/** 按本地日历移动 ISO 日期，避免 UTC 转换改变日期。 */
function shiftIsoDate(value: string, days: number): string {
  const date = parseLocalDate(value);
  date.setDate(date.getDate() + days);
  return localIsoDate(date);
}

/** 把本地 Date 转换为不带时区偏移的 ISO 日期。 */
function localIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** 把已校验的 ISO 日期解析成本地午夜。 */
function parseLocalDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

/** 返回给定本地日期所在月份的第一天。 */
function startOfMonth(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), 1);
}

/** 按日历月移动月历视图。 */
function shiftMonth(value: Date, offset: number): Date {
  return new Date(value.getFullYear(), value.getMonth() + offset, 1);
}

/** 生成包含前后月补位的六周月历网格。 */
function monthGrid(month: Date): Date[] {
  const start = startOfMonth(month);
  start.setDate(start.getDate() - start.getDay());
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    return day;
  });
}

/** 绘制日期范围触发器的线框日历图标。 */
function CalendarIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5.5 3v2M14.5 3v2M3.5 7h13M4.5 4.5h11a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" />
    </svg>
  );
}

/** 绘制随浮层状态旋转的展开箭头。 */
function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="m4 6 4 4 4-4" />
    </svg>
  );
}

/** 校验原生日期输入是否提供了真实存在的 ISO 自然日。 */
function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = parseLocalDate(value);
  return !Number.isNaN(parsed.getTime()) && localIsoDate(parsed) === value;
}
