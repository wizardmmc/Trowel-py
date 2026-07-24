"""费曼练习的问题生成与回答评估编排。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from trowel_py.cards.repository import CardRepository
from trowel_py.feynman.repository import FeynmanRepository, FeynmanSession
from trowel_py.llm.client import LLMService
from trowel_py.schemas.card import Card
from trowel_py.schemas.feynman import FeynmanEvaluationSchema, FeynmanQuestionSchema


@dataclass(frozen=True)
class GenerateResult:
    """返回给路由层的问题生成结果。"""

    session_id: str
    question: str
    hint: str | None


@dataclass(frozen=True)
class EvaluateResult:
    """返回给路由层的回答评估结果。"""

    session_id: str
    accuracy: int
    completeness: int
    feedback: str
    missed_points: list[str]


def _question_prompt(card: Card) -> str:
    lines = [f"卡片标题：{card.title}", f"卡片解释：{card.explanation}"]
    if card.example:
        lines.append(f"示例： {card.example}")
    return "\n".join(lines)


def _evaluation_prompt(
    session: FeynmanSession,
    card: Card,
    user_answer: str,
) -> str:
    return (
        f"问题：{session.question}\n"
        f"卡片解释：{card.explanation}\n"
        f"用户回答：{user_answer}"
    )


def generate_question(
    card_id: str,
    card_repo: CardRepository,
    feynman_repo: FeynmanRepository,
    llm_service: LLMService,
) -> GenerateResult | None:
    """为卡片生成问题并创建练习会话；卡片不存在时返回 ``None``。"""
    card = card_repo.find_by_id(card_id)
    if card is None:
        return None

    result = cast(
        FeynmanQuestionSchema,
        llm_service.structured_call(
            _question_prompt(card),
            FeynmanQuestionSchema,
            call_type="feynman-question",
        ),
    )
    session = FeynmanSession(
        id=uuid.uuid4().hex[:12], card_id=card_id, question=result.question
    )
    feynman_repo.create(session)
    return GenerateResult(
        session_id=session.id,
        question=result.question,
        hint=result.hint,
    )


def evaluate_answer(
    session_id: str,
    user_answer: str,
    card_repo: CardRepository,
    feynman_repo: FeynmanRepository,
    llm_service: LLMService,
) -> EvaluateResult | None:
    """评估用户回答并持久化分数；会话或卡片不存在时返回 ``None``。"""
    session = feynman_repo.find_by_id(session_id)
    if session is None:
        return None

    card = card_repo.find_by_id(session.card_id)
    if card is None:
        return None

    result = cast(
        FeynmanEvaluationSchema,
        llm_service.structured_call(
            _evaluation_prompt(session, card, user_answer),
            FeynmanEvaluationSchema,
            call_type="feynman-eval",
        ),
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
