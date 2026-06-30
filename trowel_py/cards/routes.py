# intermediate layer between service layer and HTTP request
from fastapi import APIRouter, Depends
from trowel_py.cards.repository import CardRepository, create_card_repository
from trowel_py.cards.service import (
    extract_cards,
    extract_from_conversation,
    find_duplicates,
    re_explain,
    review_card,
)
from trowel_py.cards.jsonl_parser import parse_jsonl
from trowel_py.llm.client import LLMService, create_llm_service
from trowel_py.review.repository import ReviewRepository, create_review_repository
from trowel_py.schemas.api import CardDraft, ExtractRequest, ReviewRequest
from trowel_py.schemas.re_explain import ReExplainRequest
from trowel_py.schemas.card import Card
from trowel_py.db.connection import create_db
import sqlite3
import logging

logger = logging.getLogger(__name__)

router = APIRouter()
_draft_store: dict[str, CardDraft] = {} # {id: CardDraft}


# inject function, convence test
def _get_conn():
    """Yield a DB connection; commit and close after the request."""
    conn = create_db()
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def _get_card_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> CardRepository:
    return create_card_repository(conn)


def _get_review_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> ReviewRepository:
    return create_review_repository(conn)


def _get_llm_service() -> LLMService:
    from trowel_py.config import load_llm_config

    return create_llm_service(load_llm_config())


# route
@router.post("/extract")
def extract(request: ExtractRequest, llm_service: LLMService = Depends(_get_llm_service)) -> dict:
    logger.info("Extract request received, content length: %d", len(request.content))
    drafts = extract_cards(request.content, llm_service)
    logger.info("Extraction complete, %d drafts generated", len(drafts))
    for draft in drafts:
        _draft_store[draft.id] = draft
    return {
        "success": True,
        "data": {
            "drafts": [d.model_dump() for d in drafts]    # convert pydantic to dict, to convert to json
        },
        "error": None
    }


@router.post("/extract-conversation")
def extract_conversation(request: ExtractRequest, llm_service: LLMService = Depends(_get_llm_service)) -> dict:
    """
    extract card drafts from an uploaded CC JSONL conversation log
    """
    logger.info("extract-conversation request received, content length: %d", len(request.content))
    messages = parse_jsonl(request.content)
    logger.info("Parsed %d message from JSONL", len(messages))
    drafts = extract_from_conversation(messages, llm_service)
    logger.info("conversation extraction complete, %d drafts genrated", len(drafts))
    for draft in drafts:
        _draft_store[draft.id] = draft
    return {
        "success": True,
        "data": {
            "drafts": [d.model_dump() for d in drafts],
        },
        "error": None,
    }


@router.post("/re-explain")
def re_explain_card(
    request: ReExplainRequest,
    llm_service: LLMService = Depends(_get_llm_service),
) -> dict:
    """
    regenerate a draft's explanation from a different angle (slice 021).

    Stateless generator: no DB writes. The caller keeps candidate versions in
    frontend state and writes the chosen one back via POST /{draft_id}/review.
    """
    logger.info(
        "re-explain request, title: %s, has hint: %s",
        request.title,
        request.user_hint is not None,
    )
    new_explanation = re_explain(
        explanation=request.explanation,
        title=request.title,
        category=request.category,
        llm_service=llm_service,
        user_hint=request.user_hint,
    )
    return {
        "success": True,
        "data": {"explanation": new_explanation},
        "error": None,
    }


@router.post("/{draft_id}/review")
def review(draft_id: str,
           request: ReviewRequest,
           card_repo: CardRepository = Depends(_get_card_repo),
           review_repo: ReviewRepository = Depends(_get_review_repo)) -> dict:
    logger.info("Review request for draft: %s, action: %s", draft_id, request.action)
    draft = _draft_store.get(draft_id)
    if draft is None:
        logger.warning("Draft not found: %s", draft_id)
        return {
            "success": False,
            "data": None,
            "error": "Draft not found"
        }
    card = review_card(draft, request, card_repo, review_repo)
    if card is None:
        logger.info("Draft %s rejected", draft_id)
        return {
            "success": True,
            "data": {
                "rejected": True
            },
            "error": None
        }
    else:
        logger.info("Draft %s accepted as card: %s", draft_id, card.id)
        return {
            "success": True,
            "data": {
                "card": card.model_dump()
            },
            "error": None
        }
    

@router.get("/{card_id}/dedup")
def de_duplicate(card_id: str,
                 card_repo: CardRepository = Depends(_get_card_repo),):
    """
    find duplicated card
    """
    draft = _draft_store.get(card_id)
    if draft is None:
        logger.warning("Dedup request for unknown draft: %s", card_id)
        return {
            "success": False, 
            "data": None, 
            "error": "Draft not found"
        }
    duplicates = find_duplicates(draft.title, card_repo)
    return {
        "success": True,
        "data": {"duplicates": [d.model_dump() for d in duplicates]},
        "error": None,
    }


@router.get("/search")
def search_cards(q: str,
                 card_repo: CardRepository = Depends(_get_card_repo)) -> dict:
    """FTS5 full-text search for cards."""
    logger.info("Search cards, query: %s", q)
    cards = card_repo.search_by_fts5(q)
    return {
        "success": True,
        "data": [c.model_dump() for c in cards],
        "error": None,
    }


@router.get("/")
def get_all_cards(page: int = 1,
            limit: int = 20,
            card_repo: CardRepository = Depends(_get_card_repo),) -> dict:
    cards = card_repo.find_all()
    logger.info("Get all cards, page: %d, limit: %d, total: %d", page, limit, len(cards))
    # manual pagination
    start = (page - 1) * limit
    end = start + limit
    page_cards = cards[start:end]
    return {
        "success": True,
        "data": {
            "cards": [c.model_dump() for c in page_cards],
            "total": len(cards),
            "page": page,
            "limit": limit,
        },
        "error": None,
    }
