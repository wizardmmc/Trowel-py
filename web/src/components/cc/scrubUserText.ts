/**
 * slice-035 bug4: defensive FE scrub of reloaded user text.
 *
 * The backend (`history._clean_user_text`) already scrubs CC-internal
 * injections before they reach the FE. This helper is a second line of
 * defense — if anything slips through (an edge-case shape, an older jsonl),
 * we normalize it here so the user bubble never shows raw tags or a skill
 * description. Mirrors the backend rules:
 *
 * - raw slash-command tags  -> `/name args`
 * - trowel skill-trigger expansion -> `/name args`
 * - local-command-stdout / system-reminder / cparam -> "" (drop the bubble)
 *
 * Real user input passes through unchanged. Returns "" only for injections
 * that should not render at all (caller hides the bubble in that case).
 */

const COMMAND_NAME_RE = /<command-name>\s*\/?\s*(\S+?)\s*<\/command-name>/;
const COMMAND_ARGS_RE = /<command-args>([\s\S]*?)<\/command-args>/;
const SKILL_TRIGGER_RE = /^Use the Skill tool with skill='([^']+?)'\.\s*([\s\S]*)$/;

export function scrubUserText(text: string): string {
  if (!text) return "";

  // 1. Raw slash-command tags -> /name args
  const nameM = COMMAND_NAME_RE.exec(text);
  if (nameM) {
    const name = nameM[1].trim();
    const argsM = COMMAND_ARGS_RE.exec(text);
    const args = argsM ? argsM[1].trim() : "";
    return args ? `/${name} ${args}` : `/${name}`;
  }
  // 2. Local-command stdout -> drop
  if (text.includes("<local-command-stdout>")) return "";
  // 3. Trowel skill-trigger expansion -> /name args
  const triggerM = SKILL_TRIGGER_RE.exec(text);
  if (triggerM) {
    const name = triggerM[1].trim();
    const args = triggerM[2].trim();
    return args ? `/${name} ${args}` : `/${name}`;
  }
  // 4. Residual injection wrappers -> drop (defensive)
  const t = text.trimStart();
  if (t.startsWith("<system-reminder>") || t.startsWith("<cparam>")) return "";
  return text;
}
