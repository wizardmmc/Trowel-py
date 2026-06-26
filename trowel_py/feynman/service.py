"""
feynman service: orchestrate question generation and answer evaluation
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from trowel_py.cards.repository import CardRepository
from trowel_py.feynman.repository import FeynmanRepository, FeynmanSession
from trowel_py.llm.client import LLMService
from trowel_py.schemas.feynman import FeynmanQuestionSchema, FeynmanEvaluationSchema


@dataclass(frozen=True)
class GenerateResult:
    """
    the question-generation outcome handed back to the route

     Attributes:
        session_id: the newly created feynman_sessions row id.
    """

    session_id: str
    question: str
    hint: str | None


@dataclass(frozen=True)
class EvaluateResult:
    """
    the evaluation outcome handed back to the route
    """

    session_id: str
    accuracy: int
    completeness: int
    feedback: str
    missed_points: list[str]


def generate_question(
    card_id: str,
    card_repo: CardRepository,
    feynman_repo: FeynmanRepository,
    llm_service: LLMService,
) -> GenerateResult | None:
    """
    pose a feynman question for a card and store a new session

    Args:
        card_id: the card to drill.
        card_repo: to read the card content.
        feynman_repo: to persist the new session.
        llm_service: to call the question-generation LLM.

    Returns:
        the generated question + session id, or None when the card does not
        exist (the route turns this into an error envelope).
    """
    card = card_repo.find_by_id(card_id)
    if card is None:
        return None
    lines = [f"卡片标题：{card.title}", f"卡片解释：{card.explanation}"]
    if card.example:
        lines.append(f"示例： {card.example}")
    user_prompt = "\n".join(lines)

    result = llm_service.structured_call(
        user_prompt, FeynmanQuestionSchema, call_type="feynman-question"
    )
    session = FeynmanSession(
        id=uuid.uuid4().hex[:12], card_id=card_id, question=result.question
    )
    feynman_repo.create(session)
    return GenerateResult(
        session_id=session.id, question=result.question, hint=result.hint
    )


def evaluate_answer(
    session_id: str,
    user_answer: str,
    card_repo: CardRepository,
    feynman_repo: FeynmanRepository,
    llm_service: LLMService,
) -> EvaluateResult | None:
    """
    judge a user's answer against the card and persist the scores

    Args:
        session_id: the session to evaluate (must already have a question).
        user_answer: the user's explanation.
        card_repo: to read the card's standard explanation.
        feynman_repo: to load the session and persist the evaluation.
        llm_service: to call the evaluation LLM.

    Returns:
        the scores, or None when the session (or its card) does not exist.
    """
    session = feynman_repo.find_by_id(session_id)
    if session is None:
        return None

    card = card_repo.find_by_id(session.card_id)
    if card is None:
        return None

    user_prompt = (
        f"问题：{session.question}\n"
        f"卡片解释：{card.explanation}\n"
        f"用户回答：{user_answer}"
    )
    result = llm_service.structured_call(
        user_prompt, FeynmanEvaluationSchema, call_type="feynman-eval"
    )
    feynman_repo.update_with_evaluation(
        session_id=session_id,
        user_answer=user_answer,
        accuracy=result.accuracy,
        completeness=result.completeness,
        feedback=result.feedback,
        missed_points=result.missed_points,
    )
    return EvaluateResult(
        session_id=session_id,
        accuracy=result.accuracy,
        completeness=result.completeness,
        feedback=result.feedback,
        missed_points=result.missed_points,
    )
