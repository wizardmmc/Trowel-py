"""Tests for feynman service (slice 019).

Middle of the pyramid. The LLM is mocked (MagicMock returning fixed schemas);
card_repo and feynman_repo use a real in-memory db so we assert the session
really lands in / updates the table — not just that the service returns.
"""
import sqlite3
from unittest.mock import MagicMock

from trowel_py.cards.repository import create_card_repository
from trowel_py.db.migrate import run_migrations
from trowel_py.feynman.repository import create_feynman_repository
from trowel_py.feynman.service import evaluate_answer, generate_question
from trowel_py.schemas.feynman import FeynmanEvaluationSchema, FeynmanQuestionSchema


def _seed_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    """insert a card with title/explanation/example for prompt-building tests."""
    conn.execute(
        "insert into cards (id, title, category, explanation, example, tags) "
        "values (?, ?, ?, ?, ?, ?)",
        (
            card_id,
            "useEffect",
            "React",
            "runs side effects after render",
            "useEffect(() => { ... }, [])",
            '["hooks"]',
        ),
    )


def _fake_question_llm() -> MagicMock:
    """an LLMService mock that returns a fixed feynman question."""
    llm = MagicMock()
    llm.structured_call.return_value = FeynmanQuestionSchema(
        question="when does the cleanup function run?",
        hint="think about unmount",
    )
    return llm


def _fake_eval_llm() -> MagicMock:
    """an LLMService mock that returns a fixed evaluation."""
    llm = MagicMock()
    llm.structured_call.return_value = FeynmanEvaluationSchema(
        accuracy=80,
        completeness=60,
        feedback="missed the unmount case",
        missed_points=["cleanup on unmount"],
    )
    return llm


# --- generate_question ---


def test_generate_question_returns_result_and_persists_session(
    db_connection: sqlite3.Connection,
):
    """generate builds a question, stores a session, returns sessionId+question+hint."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_question_llm()

    result = generate_question("card-1", card_repo, feynman_repo, llm)

    assert result is not None
    assert result.question == "when does the cleanup function run?"
    assert result.hint == "think about unmount"
    # session really landed in the DB
    session = feynman_repo.find_by_id(result.session_id)
    assert session is not None
    assert session.card_id == "card-1"
    assert session.question == "when does the cleanup function run?"


def test_generate_question_passes_card_content_and_call_type(
    db_connection: sqlite3.Connection,
):
    """the user_prompt carries the card content; call_type is feynman-question."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_question_llm()

    generate_question("card-1", card_repo, feynman_repo, llm)

    assert llm.structured_call.called
    args, kwargs = llm.structured_call.call_args
    user_prompt = args[0]
    assert "useEffect" in user_prompt  # title
    assert "runs side effects after render" in user_prompt  # explanation
    assert "useEffect(() => { ... }, [])" in user_prompt  # example
    assert kwargs.get("call_type") == "feynman-question"


def test_generate_question_none_when_card_missing(db_connection: sqlite3.Connection):
    """a non-existent card: generate returns None (route turns this into an error)."""
    run_migrations(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_question_llm()

    result = generate_question("ghost-card", card_repo, feynman_repo, llm)

    assert result is None
    assert not llm.structured_call.called  # never reached the LLM


# --- evaluate_answer ---


def test_evaluate_answer_returns_result_and_updates_session(
    db_connection: sqlite3.Connection,
):
    """evaluate scores the answer, updates the session, returns the scores."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    # first generate a session to evaluate
    gen = generate_question("card-1", card_repo, feynman_repo, _fake_question_llm())
    assert gen is not None
    llm = _fake_eval_llm()

    result = evaluate_answer(
        gen.session_id, "my explanation", card_repo, feynman_repo, llm
    )

    assert result is not None
    assert result.session_id == gen.session_id
    assert result.accuracy == 80
    assert result.completeness == 60
    assert result.feedback == "missed the unmount case"
    assert result.missed_points == ["cleanup on unmount"]
    # session really updated in the DB
    session = feynman_repo.find_by_id(gen.session_id)
    assert session is not None
    assert session.user_answer == "my explanation"
    assert session.accuracy == 80


def test_evaluate_answer_passes_three_parts_and_call_type(
    db_connection: sqlite3.Connection,
):
    """user_prompt carries question + card explanation + user answer; call_type feynman-eval."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    gen = generate_question("card-1", card_repo, feynman_repo, _fake_question_llm())
    assert gen is not None
    llm = _fake_eval_llm()

    evaluate_answer(gen.session_id, "my explanation", card_repo, feynman_repo, llm)

    args, kwargs = llm.structured_call.call_args
    user_prompt = args[0]
    assert gen.question in user_prompt  # the question
    assert "runs side effects after render" in user_prompt  # card explanation
    assert "my explanation" in user_prompt  # user answer
    assert kwargs.get("call_type") == "feynman-eval"


def test_evaluate_answer_none_when_session_missing(
    db_connection: sqlite3.Connection,
):
    """a non-existent session: evaluate returns None."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_eval_llm()

    result = evaluate_answer("ghost-session", "x", card_repo, feynman_repo, llm)

    assert result is None
    assert not llm.structured_call.called
