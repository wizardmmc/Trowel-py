from pydantic import BaseModel, Field


class FeynmanQuestionSchema(BaseModel):
    """费曼提问的结构化输出；``hint`` 可为空。"""

    question: str = Field(min_length=1)
    hint: str | None = None


class FeynmanEvaluationSchema(BaseModel):
    """费曼回答评估的结构化输出。"""

    accuracy: int = Field(ge=0, le=100)
    completeness: int = Field(ge=0, le=100)
    feedback: str = Field(min_length=1)
    missed_points: list[str] = Field(default_factory=list)
