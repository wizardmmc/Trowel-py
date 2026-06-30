import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CardDetail } from "./CardDetail";
import type { CardDraft } from "../../api/client";
import {
  ORIGINAL_ID,
  MAX_RE_EXPLAINS,
  type ReExplainCandidate,
} from "../../stores/cardStore";

interface ReviewModalProps {
  draft: CardDraft | null;
  currentIndex: number;
  totalCount: number;
  onAccept: () => void;
  onReject: () => void;
  onEdit: (edits: Record<string, unknown>) => void;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
  loading: boolean;
  // re-explain (slice 021) — pure props; App wires them to cardStore
  reExplainRegens: ReExplainCandidate[];
  reExplainSelectedId: string;
  reExplainLoading: boolean;
  reExplainError: string | null;
  onRegenerate: (hint?: string) => void;
  onSelectCandidate: (id: string) => void;
  onResetReExplain: () => void;
}

export function ReviewModal({
  draft,
  currentIndex,
  totalCount,
  onAccept,
  onReject,
  onEdit,
  onNext,
  onPrev,
  onClose,
  loading,
  reExplainRegens,
  reExplainSelectedId,
  reExplainLoading,
  reExplainError,
  onRegenerate,
  onSelectCandidate,
  onResetReExplain,
}: ReviewModalProps) {
  const [hint, setHint] = useState("");
  if (!draft) return null;

  // V0 (the draft's own explanation) is always the first candidate and is
  // never overwritten — invariant 1: the draft explanation only changes when
  // the user picks a regen and accepts.
  const candidates: ReExplainCandidate[] = [
    { id: ORIGINAL_ID, tag: "原始版本", text: draft.explanation },
    ...reExplainRegens,
  ];
  const count = reExplainRegens.length;
  const atCap = count >= MAX_RE_EXPLAINS;

  const handleAccept = () => {
    // write-back: original → accept (no change); regen → edit with its text
    if (reExplainSelectedId !== ORIGINAL_ID) {
      const selected = reExplainRegens.find(
        (c) => c.id === reExplainSelectedId,
      );
      if (selected) {
        onEdit({ explanation: selected.text });
        return;
      }
    }
    onAccept();
  };

  const handleRegenerate = () => {
    if (atCap || reExplainLoading) return;
    onRegenerate(hint.trim() || undefined);
  };

  const handleCancelReExplain = () => {
    onResetReExplain();
    setHint("");
  };

  return (
    <AnimatePresence>
      <motion.div
        className="review-modal__overlay"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
        data-testid="review-modal"
      >
        <motion.div
          className="review-modal__content"
          initial={{ y: 50, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 50, opacity: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="review-modal__header">
            <h2>审核卡片（{currentIndex + 1}/{totalCount}）</h2>
            <button
              className="review-modal__close"
              onClick={onClose}
              data-testid="close-modal"
            >
              &times;
            </button>
          </div>

          <CardDetail draft={draft} />

          <div className="re-explain">
            <div className="re-explain__head">
              <span className="re-explain__label">
                重新解释 · 选一个最帮你理解的
              </span>
              <span className="re-explain__count">
                已生成 <b>{count}/{MAX_RE_EXPLAINS}</b>
                {atCap ? " · 不能再生成" : ""}
              </span>
            </div>
            <input
              className="re-explain__hint"
              placeholder="想往哪个方向重写？例：更通俗 / 举个做菜的比喻 / 少用术语"
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              data-testid="re-explain-hint"
            />
            {reExplainError && (
              <p
                className="re-explain__error"
                role="alert"
                data-testid="re-explain-error"
              >
                {reExplainError}
              </p>
            )}
            <div className="re-explain__candidates">
              {candidates.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className={`re-explain__cand${
                    reExplainSelectedId === c.id
                      ? " re-explain__cand--selected"
                      : ""
                  }`}
                  onClick={() => onSelectCandidate(c.id)}
                  data-testid={`re-explain-cand-${c.id}`}
                >
                  <span className="re-explain__cand-radio" />
                  <span className="re-explain__cand-body">
                    <span className="re-explain__cand-tag">{c.tag}</span>
                    <span className="re-explain__cand-text">{c.text}</span>
                  </span>
                </button>
              ))}
            </div>
            <div className="re-explain__actions">
              <button
                type="button"
                className="btn--ghost-sun"
                onClick={handleRegenerate}
                disabled={atCap || reExplainLoading}
                data-testid="re-explain-regen"
              >
                {reExplainLoading
                  ? "生成中…"
                  : atCap
                    ? "再生成（已用满）"
                    : "再生成"}
              </button>
              <button
                type="button"
                className="btn--secondary"
                onClick={handleCancelReExplain}
                data-testid="re-explain-cancel"
              >
                取消（保留原始）
              </button>
            </div>
          </div>

          <div className="review-modal__actions">
            <button
              className="review-modal__btn review-modal__btn--accept"
              onClick={handleAccept}
              disabled={loading}
              data-testid="accept-button"
            >
              采纳
            </button>
            <button
              className="review-modal__btn review-modal__btn--reject"
              onClick={onReject}
              disabled={loading}
              data-testid="reject-button"
            >
              拒绝
            </button>
          </div>

          {totalCount > 1 && (
            <div className="review-modal__nav">
              <button
                onClick={onPrev}
                disabled={currentIndex === 0}
                data-testid="prev-button"
              >
                &larr; 上一张
              </button>
              <button
                onClick={onNext}
                disabled={currentIndex === totalCount - 1}
                data-testid="next-button"
              >
                下一张 &rarr;
              </button>
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
