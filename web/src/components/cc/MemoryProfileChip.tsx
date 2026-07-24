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
