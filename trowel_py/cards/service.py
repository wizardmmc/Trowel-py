import uuid
from datetime import datetime

from trowel_py.cards.jsonl_parser import ChatMessage
from trowel_py.cards.repository import CardRepository
from trowel_py.llm.client import LLMService
from trowel_py.review.repository import ReviewRepository
from trowel_py.cards.schemas import CardDraft, ReviewRequest
from trowel_py.cards.models import Card
from trowel_py.cards.schemas import ExtractedCard, ExtractOutput
from trowel_py.cards.schemas import ReExplainResultSchema
from trowel_py.review.models import FSRSState


def _draft_from_extracted(extracted: ExtractedCard) -> CardDraft:
    return CardDraft(
        id=uuid.uuid4().hex[:12],
        title=extracted.title,
        category=extracted.category,
        explanation=extracted.explanation,
        example=extracted.example,
        difficulty=extracted.difficulty,
        tags=extracted.tags,
        confidence=extracted.confidence,
        source_type=extracted.source_type,
        source=extracted.source_type,
    )


def _card_from_review(
    draft: CardDraft,
    edits: dict | None,
) -> Card:
    card_data = draft.model_dump()
    if edits:
        card_data.update(edits)

    return Card(
        id=uuid.uuid4().hex[:12],
        title=card_data["title"],
        category=card_data["category"],
        explanation=card_data["explanation"],
        example=card_data.get("example"),
        difficulty=card_data["difficulty"],
        source=card_data.get("source"),
        tags=card_data.get("tags", []),
        status="active",
    )


def extract_cards(content: str, llm_service: LLMService) -> list[CardDraft]:
    result = llm_service.structured_call(content, ExtractOutput)
    return [_draft_from_extracted(extracted) for extracted in result.cards]


def extract_from_conversation(
    messages: list[ChatMessage], llm_service: LLMService
) -> list[CardDraft]:
    text = "\n".join(f"{m.role}: {m.content}" for m in messages)
    return extract_cards(text, llm_service)


def review_card(
    draft: CardDraft,
    request: ReviewRequest,
    card_repo: CardRepository,
    review_repo: ReviewRepository,
) -> Card | None:
    if request.action == "reject":
        return None

    edits = request.edits if request.action == "edit" else None
    card = _card_from_review(draft, edits)
    card_repo.create(card)
    review_repo.save_fsrs_state(
        FSRSState(
            card_id=card.id,
            state=0,
            due=datetime.now(),
        )
    )
    return card


def find_duplicates(title: str, card_repo: CardRepository) -> list[Card]:
    duplicates: list[Card] = []
    seen_ids: set[str] = set()

    for card in card_repo.find_all():
        if card.title == title:
            duplicates.append(card)
            seen_ids.add(card.id)

    for card in card_repo.search_by_fts5(title):
        if card.id not in seen_ids:
            duplicates.append(card)
            seen_ids.add(card.id)

    return duplicates


def re_explain(
    explanation: str,
    title: str,
    category: str,
    llm_service: LLMService,
    user_hint: str | None = None,
) -> str:
    """只生成候选解释；选中版本仍由审核接口持久化。"""
    user_prompt = f"标题：{title}\n分类：{category}\n当前解释：{explanation}\n"
    if user_hint:
        user_prompt += f"用户希望的方向：{user_hint}\n"

    result = llm_service.structured_call(
        user_prompt, ReExplainResultSchema, call_type="re-explain"
    )
    return result.explanation
