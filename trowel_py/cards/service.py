"""编排卡片提取、审核、查重和重新解释。"""

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
    """把候选卡片转成草稿，并生成新 ID 和复制来源标识。"""

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
    """用草稿和字段修改创建一张新卡片，但不写入数据库。

    ``edits`` 会覆盖草稿中的同名字段。新卡片使用新生成的 ID，状态固定为
    ``"active"``。

    Args:
        draft: 本次审核的草稿。
        edits: 审核时要覆盖的草稿字段；没有修改时为 ``None``。

    Returns:
        等待仓储写入的新卡片。
    """

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
    """让模型从文本中提取候选卡片，并转换为待审核草稿。

    Args:
        content: 原样提交给模型的待提取文本。
        llm_service: 执行结构化卡片提取的模型服务。

    Returns:
        待审核草稿；模型没有提取到卡片时为空列表。
    """

    result = llm_service.structured_call(content, ExtractOutput)
    return [_draft_from_extracted(extracted) for extracted in result.cards]


def extract_from_conversation(
    messages: list[ChatMessage], llm_service: LLMService
) -> list[CardDraft]:
    """把对话消息展开为带角色前缀的文本，再提取卡片草稿。

    每条消息写成 ``"role: content"``，消息之间用换行符分隔。

    Args:
        messages: 按原始顺序排列的用户和助手文本消息。
        llm_service: 执行结构化卡片提取的模型服务。

    Returns:
        从整段对话中提取的待审核草稿。
    """

    text = "\n".join(f"{m.role}: {m.content}" for m in messages)
    return extract_cards(text, llm_service)


def review_card(
    draft: CardDraft,
    request: ReviewRequest,
    card_repo: CardRepository,
    review_repo: ReviewRepository,
) -> Card | None:
    """处理草稿审核，并在接受或编辑时保存卡片和初始复习状态。

    拒绝时不调用仓储并返回 ``None``。接受时忽略 ``request.edits``；编辑时先
    应用修改。保存顺序固定为先写卡片，再写 ``state=0``、到期时间为当前时间的
    初始复习状态。

    Args:
        draft: 要审核的卡片草稿。
        request: 审核动作和可选字段修改。
        card_repo: 保存新卡片的仓储。
        review_repo: 保存初始复习状态的仓储。

    Returns:
        接受或编辑后保存的卡片；拒绝时为 ``None``。
    """

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
    """先返回标题完全相同的卡片，再追加不重复的全文搜索结果。

    标题比较区分大小写。全文搜索使用 ``title`` 作为 FTS5 查询表达式，结果按
    卡片 ID 去重。

    Args:
        title: 待审核草稿的标题。
        card_repo: 提供全部卡片和全文搜索的仓储。

    Returns:
        精确标题结果在前、其余全文搜索结果在后的疑似重复卡片。
    """

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
    """让模型重新生成候选解释，不修改草稿或数据库。

    Args:
        explanation: 当前解释。
        title: 当前草稿的标题。
        category: 当前草稿的分类。
        llm_service: 执行重新解释请求的模型服务。
        user_hint: 用户希望采用的解释方向；为 ``None`` 或空字符串时不添加
            方向要求。

    Returns:
        模型生成的候选解释；选中后仍需由审核流程保存。
    """
    user_prompt = f"标题：{title}\n分类：{category}\n当前解释：{explanation}\n"
    if user_hint:
        user_prompt += f"用户希望的方向：{user_hint}\n"

    result = llm_service.structured_call(
        user_prompt, ReExplainResultSchema, call_type="re-explain"
    )
    return result.explanation
