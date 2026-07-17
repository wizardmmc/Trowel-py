/**
 * slice-060 — the memory/profile read-only chips on the Composer's bottom bar.
 *
 * Two frozen A/B switches stamped on the session at create time. Unlike the
 * model/effort chips these are DISPLAY-ONLY: a session's condition never changes
 * mid-life (想换条件就新建会话), so there is no caret / popover. on renders in
 * the success (green) tone with a filled dot; off renders muted with a dim dot.
 *
 * Sat next to ModelEffortChip so the user always sees which experiment
 * condition the active session is running under.
 */
interface MemoryProfileChipProps {
  readonly memoryEnabled: boolean;
  readonly profileEnabled: boolean;
}

export function MemoryProfileChip({
  memoryEnabled,
  profileEnabled,
}: MemoryProfileChipProps) {
  return (
    <>
      <CondChip label="memory" on={memoryEnabled} />
      <CondChip label="profile" on={profileEnabled} />
    </>
  );
}

function CondChip({ label, on }: { readonly label: string; readonly on: boolean }) {
  return (
    <span
      className={`cc-cond${on ? " cc-cond--on" : " cc-cond--off"}`}
      aria-label={`${label}: ${on ? "on" : "off"}`}
    >
      <span className="cc-cond__dot" aria-hidden="true" />
      <span className="cc-cond__label">{label}</span>
      <span className="cc-cond__val">{on ? "on" : "off"}</span>
    </span>
  );
}
