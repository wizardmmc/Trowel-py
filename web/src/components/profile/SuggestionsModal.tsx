import { useEffect, useState } from "react";
import { useSuggestionsStore } from "../../stores/suggestionsStore";
import { useProfileStore } from "../../stores/profileStore";
import type { ProfileDimension, Suggestion } from "../../api/client";
import "./SuggestionsModal.css";

/** the five dims in canonical order (mirrors ProfileView + backend titles). */
const DIMENSIONS: { readonly key: ProfileDimension; readonly title: string }[] = [
  { key: "ability", title: "能力水平" },
  { key: "methodology", title: "方法论偏好" },
  { key: "expression", title: "表达风格" },
  { key: "goal", title: "长程目标" },
  { key: "other", title: "其他" },
];

interface SuggestionsModalProps {
  /** whether the modal is shown (rendered); closed → renders nothing */
  open: boolean;
  onClose: () => void;
}

/**
 * SuggestionsModal — the AI calibration suggestion review (slice-050).
 *
 * Lists pending suggestions grouped by dimension. The user checks the ones to
 * keep (optionally editing the body first), then「采纳选中」appends each to the
 * matching dimension's existing content — never replacing what the user wrote
 * (C-1). Acceptance PUTs the profile with source=ai-calibration, then marks the
 * suggestions accepted. Discard marks one discarded without touching the profile.
 */
export function SuggestionsModal({ open, onClose }: SuggestionsModalProps) {
  const { suggestions, patchStatus } = useSuggestionsStore();
  const { profile, updateProfile } = useProfileStore();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Escape closes the modal (a11y). Listener is active only while open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const toggle = (id: string): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const acceptSelected = async (): Promise<void> => {
    if (!profile || selected.size === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const accepted = suggestions.filter((s) => selected.has(s.id));
      // PUT FIRST: write the merged profile. If PUT fails, nothing is marked —
      // suggestions stay pending and the user retries cleanly.
      const dims: Record<ProfileDimension, string> = {
        ability: profile.ability,
        methodology: profile.methodology,
        expression: profile.expression,
        goal: profile.goal,
        other: profile.other,
      };
      for (const s of accepted) {
        const body = edits[s.id] ?? s.body;
        const current = dims[s.dimension];
        dims[s.dimension] = current ? `${current}\n${body}` : body;
      }
      await updateProfile({ ...dims, source: "ai-calibration" });
      // Then mark accepted with allSettled. A failed mark does NOT undo the
      // PUT (the body is already in profile.md); it leaves that suggestion
      // pending + visible, so the user SEES the mismatch and can discard the
      // duplicate — instead of silently losing data. (code-review [4]: a
      // visible duplicate risk beats a silent loss; the earlier "mark-first"
      // order lost data when PUT failed after marks succeeded.)
      const results = await Promise.allSettled(
        accepted.map((s) => patchStatus(s.id, "accepted")),
      );
      const failed = results.filter((r) => r.status === "rejected");
      setSelected(new Set());
      setEdits({});
      setEditingId(null);
      if (failed.length > 0) {
        setError(
          `${accepted.length - failed.length}/${accepted.length} 条已写入画像；${failed.length} 条标记失败（内容已在画像里，列表仍显示，请丢弃重复的）`,
        );
        return; // keep the modal open so the user sees the error + leftovers
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "采纳失败");
    } finally {
      setBusy(false);
    }
  };

  const discard = async (s: Suggestion): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await patchStatus(s.id, "discarded");
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(s.id);
        return next;
      });
      setEdits((prev) => {
        if (!(s.id in prev)) return prev;
        const next = { ...prev };
        delete next[s.id];
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "丢弃失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="suggestions-backdrop"
      onClick={onClose}
      data-testid="suggestions-modal"
    >
      <div
        className="suggestions-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="AI 校准建议"
      >
        <div className="suggestions-header">
          <div>
            <h2>AI 校准建议</h2>
            <p className="subtitle">
              勾选想采纳的建议，可编辑后再追加到画像。采纳只会追加，不改你已经写的字。
            </p>
          </div>
          <button
            className="suggestions-close"
            onClick={onClose}
            aria-label="关闭"
            data-testid="suggestions-close"
          >
            ✕
          </button>
        </div>

        <div className="suggestions-body">
          {suggestions.length === 0 && (
            <p className="suggestions-empty">暂无新建议</p>
          )}
          {DIMENSIONS.map((dim) => {
            const items = suggestions.filter((s) => s.dimension === dim.key);
            if (items.length === 0) return null;
            return (
              <div className="suggestions-group" key={dim.key}>
                <div className="suggestions-group__title">
                  <span className="dot" />
                  {dim.title}
                </div>
                {items.map((s) => {
                  const isSel = selected.has(s.id);
                  const isEditing = editingId === s.id;
                  return (
                    <div
                      className={`suggestion ${
                        isSel ? "suggestion--selected" : ""
                      }`}
                      key={s.id}
                    >
                      <button
                        className={`suggestion__check ${isSel ? "is-on" : ""}`}
                        onClick={() => toggle(s.id)}
                        aria-pressed={isSel}
                        aria-label="勾选这条建议"
                        data-testid={`suggestion-check-${s.id}`}
                      />
                      <div className="suggestion__content">
                        {isEditing ? (
                          <textarea
                            className="suggestion__editor"
                            value={edits[s.id] ?? s.body}
                            onChange={(e) =>
                              setEdits((prev) => ({
                                ...prev,
                                [s.id]: e.target.value,
                              }))
                            }
                            data-testid={`suggestion-editor-${s.id}`}
                          />
                        ) : (
                          <p
                            className="suggestion__text"
                            data-testid={`suggestion-body-${s.id}`}
                          >
                            {edits[s.id] ?? s.body}
                          </p>
                        )}
                        {s.sources.length > 0 && (
                          <div className="suggestion__sources">
                            {s.sources.map((src, i) => (
                              <span key={i}>{src}</span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="suggestion__actions">
                        <button
                          className="suggestion__act"
                          onClick={() => setEditingId(isEditing ? null : s.id)}
                          data-testid={`suggestion-edit-${s.id}`}
                        >
                          {isEditing ? "完成" : "编辑"}
                        </button>
                        <button
                          className="suggestion__act suggestion__act--discard"
                          onClick={() => discard(s)}
                          data-testid={`suggestion-discard-${s.id}`}
                        >
                          丢弃
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>

        {error && (
          <p className="suggestions-error" data-testid="suggestions-error">
            {error}
          </p>
        )}

        <div className="suggestions-footer">
          <span className="summary">
            已选 <em>{selected.size}</em> / {suggestions.length} 条
          </span>
          <div className="actions">
            <button className="btn btn--secondary" onClick={onClose}>
              关闭
            </button>
            <button
              className="btn btn--primary"
              onClick={acceptSelected}
              disabled={busy || selected.size === 0}
              data-testid="suggestions-accept"
            >
              {busy ? "采纳中…" : "采纳选中"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
