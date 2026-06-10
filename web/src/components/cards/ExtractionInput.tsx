import { useState } from "react";

interface ExtractionInputProps {
  onExtract: (content: string) => Promise<void>;
  loading: boolean;
}

export function ExtractionInput({ onExtract, loading }: ExtractionInputProps) {
  const [content, setContent] = useState("");

  const handleSubmit = () => {
    if (content.trim().length === 0) return;
    onExtract(content);
  };

  const detectFormat = (text: string): string => {
    if (text.includes("diff --git") || text.includes("+++ ") || text.includes("--- ")) {
      return "git diff";
    }
    return "text";
  };

  const format = detectFormat(content);

  return (
    <div className="extraction-input">
      <div className="extraction-input__header">
        <h2>Extract Cards</h2>
        {content.length > 0 && (
          <span className="extraction-input__format">Detected: {format}</span>
        )}
      </div>
      <textarea
        className="extraction-input__textarea"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="Paste your text or git diff here..."
        rows={8}
        data-testid="extraction-textarea"
      />
      <button
        className="extraction-input__button"
        onClick={handleSubmit}
        disabled={loading || content.trim().length === 0}
        data-testid="extract-button"
      >
        {loading ? "Extracting..." : "Extract"}
      </button>
    </div>
  );
}
