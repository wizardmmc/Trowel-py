import uuid
from trowel_py.llm.client import LLMService
from trowel_py.schemas.extracted_card import ExtractOutput
from trowel_py.schemas.api import CardDraft, ReviewRequest
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState
from trowel_py.cards.repository import CardRepository
from trowel_py.review.repository import ReviewRepository
from datetime import datetime

def extract_cards(content: str, llm_service: LLMService) -> list[CardDraft]:
    """
    extract draft card from content using llm_service
    """
    result = llm_service.structured_call(content, ExtractOutput)

    # convert ExtractCard into CardDraft
    drafts = []
    for extracted in result.cards:
        draft = CardDraft(
            id=uuid.uuid4().hex[:12],
            title=extracted.title,
            category=extracted.category,
            explanation=extracted.explanation,
            example=extracted.example,
            difficulty=extracted.difficulty,
            tags=extracted.tags,
            confidence=extracted.confidence,
            source_type=extracted.source_type,
            source=extracted.source_type
        )
        drafts.append(draft)
    
    return drafts

def review_card(draft: CardDraft, request: ReviewRequest, card_repo: CardRepository, review_repo: ReviewRepository) -> Card | None:
    """
    process a draft with user's request and return a Card
    """
    if request.action == "reject":
        return None
    
    # convert draft to dict, easily for revise
    card_data = draft.model_dump()  # model_dump is a pydantic method
    card_data["status"] = "active"

    if request.action == "edit" and request.edits:
        card_data.update(request.edits) # update like auto revise, 'card_data["example"] = edits["example"]'

    card = Card(
        id=uuid.uuid4().hex[:12],   # draft card's id is temp in bussiness logic, but Card's id is enduring
        title=card_data["title"],
        category=card_data["category"],
        explanation=card_data["explanation"],
        example=card_data.get("example"),      # use get method, because example might be None
        difficulty=card_data["difficulty"],
        source=card_data.get("source"),
        tags=card_data.get("tags", []),
        status="active",
    )

    # store into database
    card_repo.create(card)

    fsrs_state = FSRSState(
        card_id=card.id,
        state=0,
        due=datetime.now()  # this card can be reivew right now
    )
    review_repo.save_fsrs_state(fsrs_state)

    return card

def find_duplicates(title: str, card_repo: CardRepository) -> list[Card]:
    """
    avoid generated draft is highly dupilicate (check by title, using FTS5 search and precise search)
    """
    duplicates: list[Card] = []
    seen_ids: set[str] = set()  # avoid repeat visit card

    # precise search by title
    all_cards = card_repo.find_all()
    for card in all_cards:
        if card.title == title:
            duplicates.append(card)
            seen_ids.add(card.id)

    # fts5 search
    fts_results = card_repo.search_by_fts5(title)
    for card in fts_results:
        if card.id not in seen_ids:
            duplicates.append(card)
            seen_ids.add(card.id)

    return duplicates