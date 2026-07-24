import sqlite3
from unittest.mock import MagicMock

from trowel_py.cards.repository import CardRepository, create_card_repository
from trowel_py.db.migrate import run_migrations
from trowel_py.feynman.repository import (
    FeynmanRepository,
    FeynmanSession,
    create_feynman_repository,
)
from trowel_py.feynman.service import evaluate_answer, generate_question
from trowel_py.schemas.feynman import FeynmanEvaluationSchema, FeynmanQuestionSchema


def _seed_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
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
    llm = MagicMock()
    llm.structured_call.return_value = FeynmanQuestionSchema(
        question="when does the cleanup function run?",
        hint="think about unmount",
    )
    return llm


def _fake_eval_llm() -> MagicMock:
    llm = MagicMock()
    llm.structured_call.return_value = FeynmanEvaluationSchema(
        accuracy=80,
        completeness=60,
        feedback="missed the unmount case",
        missed_points=["cleanup on unmount"],
    )
    return llm


def test_generate_question_returns_result_and_persists_session(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_question_llm()

    result = generate_question("card-1", card_repo, feynman_repo, llm)

    assert result is not None
    assert result.question == "when does the cleanup function run?"
    assert result.hint == "think about unmount"
    session = feynman_repo.find_by_id(result.session_id)
    assert session is not None
    assert session.card_id == "card-1"
    assert session.question == "when does the cleanup function run?"


def test_generate_question_passes_card_content_and_call_type(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_question_llm()

    generate_question("card-1", card_repo, feynman_repo, llm)

    assert llm.structured_call.called
    args, kwargs = llm.structured_call.call_args
    assert args[0] == (
        "卡片标题：useEffect\n"
        "卡片解释：runs side effects after render\n"
        "示例： useEffect(() => { ... }, [])"
    )
    assert kwargs.get("call_type") == "feynman-question"


def test_generate_question_omits_empty_example_from_prompt(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_card(db_connection)
    db_connection.execute("update cards set example = null where id = ?", ("card-1",))
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_question_llm()

    generate_question("card-1", card_repo, feynman_repo, llm)

    args, _ = llm.structured_call.call_args
    assert args[0] == ("卡片标题：useEffect\n卡片解释：runs side effects after render")


def test_generate_question_none_when_card_missing(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_question_llm()

    result = generate_question("ghost-card", card_repo, feynman_repo, llm)

    assert result is None
    assert not llm.structured_call.called


def test_evaluate_answer_returns_result_and_updates_session(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
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
    session = feynman_repo.find_by_id(gen.session_id)
    assert session is not None
    assert session.user_answer == "my explanation"
    assert session.accuracy == 80


def test_evaluate_answer_passes_three_parts_and_call_type(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    gen = generate_question("card-1", card_repo, feynman_repo, _fake_question_llm())
    assert gen is not None
    llm = _fake_eval_llm()

    evaluate_answer(gen.session_id, "my explanation", card_repo, feynman_repo, llm)

    args, kwargs = llm.structured_call.call_args
    assert args[0] == (
        "问题：when does the cleanup function run?\n"
        "卡片解释：runs side effects after render\n"
        "用户回答：my explanation"
    )
    assert kwargs.get("call_type") == "feynman-eval"


def test_evaluate_answer_none_when_session_missing(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_card(db_connection)
    card_repo = create_card_repository(db_connection)
    feynman_repo = create_feynman_repository(db_connection)
    llm = _fake_eval_llm()

    result = evaluate_answer("ghost-session", "x", card_repo, feynman_repo, llm)

    assert result is None
    assert not llm.structured_call.called


def test_evaluate_answer_none_when_session_card_missing():
    card_repo = MagicMock(spec=CardRepository)
    card_repo.find_by_id.return_value = None
    feynman_repo = MagicMock(spec=FeynmanRepository)
    feynman_repo.find_by_id.return_value = FeynmanSession(
        id="session-1",
        card_id="missing-card",
        question="what happens?",
    )
    llm = _fake_eval_llm()

    result = evaluate_answer(
        "session-1", "my explanation", card_repo, feynman_repo, llm
    )

    assert result is None
    llm.structured_call.assert_not_called()
    feynman_repo.update_with_evaluation.assert_not_called()
