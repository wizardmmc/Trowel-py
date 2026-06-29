import type { CardDraft } from "../../api/client";

interface CardDetailProps {
  draft: CardDraft;
}

export function CardDetail({ draft }: CardDetailProps) {
  return (
    <div className="card-detail">
      <h3 className="card-detail__title">{draft.title}</h3>
      <span className="card-detail__category">{draft.category}</span>
      <p className="card-detail__explanation">{draft.explanation}</p>
      {draft.example && (
        <p className="card-detail__example">{draft.example}</p>
      )}
      <div className="card-detail__tags">
        {draft.tags.map((tag) => (
          <span key={tag} className="card-detail__tag">{tag}</span>
        ))}
      </div>
      <div className="card-detail__meta">
        置信度: {draft.confidence}/5 · 难度: {draft.difficulty}/5
      </div>
    </div>
  );
}
