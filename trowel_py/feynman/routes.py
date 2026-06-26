"""
feynman HTTP routes: question generation, answer evaluation
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from trowel_py.cards.repository import CardRepository, create_card_repository
from trowel_py.db.connection import create_db
from trowel_py.feynman.repository import FeynmanRepository, create_feynman_repository
from trowel_py.feynman.service import evaluate_answer, generate_question
from trowel_py.llm.client import LLMService, create_llm_service

logger = logging.getLogger(__name__)

router = APIRouter()


class GenerateRequest(BaseModel):
    """
    body for Post /generate
    """

    card_id: str = Field(min_length=1)


class EvaluateRequest(BaseModel):
    """
    body for Post /evaluate
    """

    session_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)


def _get_conn():
    conn = create_db()
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def _get_card_repo(conn: sqlite3.Connection = Depends(_get_conn)) -> CardRepository:
    return create_card_repository(conn)


def _get_feynman_repo(
    conn: sqlite3.Connection = Depends(_get_conn),
) -> FeynmanRepository:
    return create_feynman_repository(conn)


def _get_llm_service() -> LLMService:
    from trowel_py.config import load_llm_config

    return create_llm_service(load_llm_config())


@router.post("/generate")
def generate(
    request: GenerateRequest,
    card_repo: CardRepository = Depends(_get_card_repo),
    feynman_repo: FeynmanRepository = Depends(_get_feynman_repo),
    llm_service: LLMService = Depends(_get_llm_service),
) -> dict:
    """
    generate a feynman question for a card
    """
    logger.info("feynman generate for card: %s", request.card_id)
    result = generate_question(request.card_id, card_repo, feynman_repo, llm_service)
    if result is None:
        logger.warning("feynman generate failed, card not found: %s", request.card_id)
        return {"success": False, "data": None, "error": "Card not found"}
    return {"success": True, "data": asdict(result), "error": None}


@router.post("/evaluate")
def evaluate(
    request: EvaluateRequest,
    card_repo: CardRepository = Depends(_get_card_repo),
    feynman_repo: FeynmanRepository = Depends(_get_feynman_repo),
    llm_service: LLMService = Depends(_get_llm_service),
) -> dict:
    """evaluate a user's answer for a feynman session"""
    logger.info("feynman evaluate for session: %s", request.session_id)
    result = evaluate_answer(
        request.session_id, request.answer, card_repo, feynman_repo, llm_service
    )
    if result is None:
        logger.warning(
            "feynman evaluate failed, session not found: %s", request.session_id
        )
        return {"success": False, "data": None, "error": "Session not found"}
    return {"success": True, "data": asdict(result), "error": None}


@router.get("/history/{card_id}")
def history(
    card_id: str,
    feynman_repo: FeynmanRepository = Depends(_get_feynman_repo),
) -> dict:
    """
    return all feynman sessions for a card, newest first
    """
    logger.info("feynman history for card: %s", card_id)
    sessions = feynman_repo.find_by_card_id(card_id)
    return {
        "success": True,
        "data": [asdict(session) for session in sessions],
        "error": None,
    }
