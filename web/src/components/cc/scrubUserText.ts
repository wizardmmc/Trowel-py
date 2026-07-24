
const COMMAND_NAME_RE = /<command-name>\s*\/?\s*(\S+?)\s*<\/command-name>/;
const COMMAND_ARGS_RE = /<command-args>([\s\S]*?)<\/command-args>/;
const SKILL_TRIGGER_RE = /^Use the Skill tool with skill='([^']+?)'\.\s*([\s\S]*)$/;

/** 后端已做主清洗；这里兼容旧 history 与漏网的 CC 内部注入。 */
export function scrubUserText(text: string): string {
  if (!text) return "";

  const nameM = COMMAND_NAME_RE.exec(text);
  if (nameM) {
    const name = nameM[1].trim();
    const argsM = COMMAND_ARGS_RE.exec(text);
    const args = argsM ? argsM[1].trim() : "";
    return args ? `/${name} ${args}` : `/${name}`;
  }
  if (text.includes("<local-command-stdout>")) return "";
  const triggerM = SKILL_TRIGGER_RE.exec(text);
  if (triggerM) {
    const name = triggerM[1].trim();
    const args = triggerM[2].trim();
    return args ? `/${name} ${args}` : `/${name}`;
  }
  const t = text.trimStart();
  if (t.startsWith("<system-reminder>") || t.startsWith("<cparam>")) return "";
  return text;
}
