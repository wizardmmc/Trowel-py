import { useState, useRef } from "react";

interface ExtractionInputProps {
  onExtract: (content: string) => Promise<void>;
  onExtractConversation: (content: string) => Promise<void>;
  loading: boolean;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".jsonl", ".json", ".txt"];

type FileFormat = "empty" | "jsonl" | "git-diff" | "text";

export function ExtractionInput({
  onExtract,
  onExtractConversation,
  loading,
}: ExtractionInputProps) {
  const [content, setContent] = useState("");
  const [fileFormat, setFileFormat] = useState<FileFormat>("empty");
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isDisabled = !content.trim() || loading;
  const formatLabel = detectFormatLabel(content, fileFormat);

  const handleExtract = () => {
    if (isDisabled) return;
    void onExtract(content.trim());
  };

  const processFile = async (file: File) => {
    setFileError(null);
    setFileFormat("empty");

    if (file.size > MAX_FILE_SIZE) {
      setFileError(
        `文件过大（${(file.size / 1024 / 1024).toFixed(1)}MB），上限 10MB。`,
      );
      return;
    }

    try {
      const text = await file.text();
      const format = detectFormat(text);
      setFileFormat(format);

      if (format === "jsonl") {
        await onExtractConversation(text);
      } else if (format === "empty") {
        setFileError("上传的文件是空的。");
      } else {
        setContent(text);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "文件解析失败";
      setFileError(message);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await processFile(file);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const file = e.dataTransfer.files[0];
    if (!file) return;
    const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      setFileError(
        "不支持的文件类型，请上传 .jsonl / .json / .txt。",
      );
      return;
    }
    await processFile(file);
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <div className="extraction-input">
      <div className="extraction-input__header">
        <h2 className="extraction-input__title">提取卡片</h2>
        {(content || fileFormat !== "empty") && (
          <span className="extraction-input__format-badge">
            识别为：{formatLabel}
          </span>
        )}
      </div>

      <div
        className="extraction-input__drop-zone"
        data-testid="drop-zone"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        <textarea
          className="extraction-input__textarea"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="粘贴一段 git diff 或文本，提取成知识卡片…"
          rows={8}
          disabled={loading}
          data-testid="extraction-textarea"
        />
        <div className="extraction-input__file-upload">
          <input
            ref={fileInputRef}
            type="file"
            accept=".jsonl,.json,.txt"
            onChange={handleFileUpload}
            className="extraction-input__file-input"
            disabled={loading}
            data-testid="file-input"
          />
          <span className="extraction-input__drop-hint">
            把文件拖到这里，或点击上传（.jsonl / .json / .txt）
          </span>
        </div>
      </div>

      <div className="extraction-input__actions">
        <button
          className="extraction-input__button"
          onClick={handleExtract}
          disabled={isDisabled}
          data-testid="extract-button"
        >
          {loading ? "提取中…" : "提取"}
        </button>
      </div>

      {fileError && <p className="extraction-input__error">{fileError}</p>}
    </div>
  );
}

function detectFormat(text: string): FileFormat {
  const trimmed = text.trim();
  if (!trimmed) return "empty";
  if (trimmed.includes("diff --git")) return "git-diff";
  const sampleLines = trimmed
    .split("\n")
    .slice(0, 3)
    .map((l) => l.trim())
    .filter(Boolean);
  if (sampleLines.length > 0 && sampleLines.every(isJsonObject)) {
    return "jsonl";
  }
  return "text";
}

function isJsonObject(line: string): boolean {
  try {
    const v = JSON.parse(line);
    return typeof v === "object" && v !== null;
  } catch {
    return false;
  }
}

function detectFormatLabel(text: string, fileFormat: FileFormat): string {
  if (fileFormat === "jsonl") return "CC JSONL（会话）";
  if (fileFormat === "git-diff") return "Git Diff";
  if (!text.trim()) return "空";
  if (text.includes("diff --git")) return "Git Diff";
  return "纯文本";
}
