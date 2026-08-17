"""AI 审核建议留痕（第 13 张表）。

为什么必须入库：本项目的核心目标之一是「全程留痕」。AI 给出的建议如果只存在于
一次 HTTP 响应里，就无法回答「当时系统建议了什么、审核人是否采纳」——
而「采纳率」是后续评估 AI 效果唯一客观的指标。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiSuggestion(Base):
    """一次 AI 建议的完整快照：谁在什么时候、用哪个 provider/模型/提示词版本生成的。"""

    __tablename__ = "ai_suggestion"

    id: Mapped[int] = mapped_column(primary_key=True)
    reimbursement_id: Mapped[int] = mapped_column(
        ForeignKey("reimbursement.id"), index=True
    )
    # 发起分析的人（财务/管理员）
    requester_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)

    provider: Mapped[str] = mapped_column(String(20), default="mock")
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")

    # APPROVE / REJECT / NEED_MORE_INFO
    conclusion: Mapped[str] = mapped_column(String(20), default="")
    confidence: Mapped[str] = mapped_column(String(10), default="")
    # 校验通过后的建议正文（JSON，已还原销售方真名）
    payload: Mapped[str] = mapped_column(Text, default="")
    # 模型原始输出，便于排查
    raw: Mapped[str] = mapped_column(Text, default="")

    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    # 1 表示走了降级（LLM 不可用，返回的是规则摘要），此时 provider 仍记录为配置值
    degraded: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(255), default="")
    # 审核人最终决定是否与建议一致：1 一致 / 0 不一致 / NULL 尚未审核
    adopted: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
