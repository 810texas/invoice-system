"""财务审核 AI 助手接口（需求第七章二期「LLM 语义分析」）。

刻意只提供**只读**能力：

- ``POST /agent/advice/{id}`` 生成一次建议（会写 `ai_suggestion` 留痕，但不改单据状态）
- ``GET  /agent/advice/{id}`` 读取最近一次建议（含是否被采纳）

「生成」之所以用 POST，是因为它会产生一次外部调用与一条留痕记录（非幂等）；
但它**不触碰状态机**，也不参与提交预审的 blocked 判定。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import guard, guard_read
from app.core.database import get_db
from app.core.permissions import AGENT_ADVICE
from app.models.document import Reimbursement
from app.models.user import User
from app.services import agent as agent_service
from app.services.llm.provider import provider_label

router = APIRouter()


def _advice_payload(advice, reimbursement_id: int) -> dict:
    return {
        "reimbursement_id": reimbursement_id,
        "suggestion_id": advice.suggestion_id,
        "provider": advice.provider,
        "model": advice.model,
        "prompt_version": advice.prompt_version,
        "elapsed_ms": advice.elapsed_ms,
        "degraded": advice.degraded,
        "error": advice.error,
        "masked": advice.masked,
        "created_at": advice.created_at,
        "advice": advice.payload,
    }


@router.get("/agent/status")
def status(user: User = Depends(guard_read(AGENT_ADVICE))):
    """当前 AI 助手的接入状态，供前端提示「mock 模式」还是「真实模型」。

    特意把 ``api_key_configured`` 单独返回：只报告 provider 是不够的 ——
    provider=deepseek 但没填 Key 时，界面会显示成「已接真实模型」，而实际每次调用都降级，
    这种"看起来配好了其实没配"的状态必须能被一眼看出来。
    """
    from app.core.config import get_settings

    settings = get_settings()
    provider = (settings.LLM_PROVIDER or "mock").lower()
    return {
        "provider": settings.LLM_PROVIDER,
        "label": provider_label(),
        "is_mock": provider == "mock",
        "api_key_configured": bool(settings.LLM_API_KEY),
        "mask_pii": settings.LLM_MASK_PII,
        "timeout_seconds": settings.LLM_TIMEOUT_SECONDS,
    }


@router.post("/agent/advice/{reimbursement_id}")
def create_advice(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard(AGENT_ADVICE)),
):
    """生成审核建议。LLM 不可用时降级为规则摘要（HTTP 仍为 200，degraded=true）。"""
    reimb = db.get(Reimbursement, reimbursement_id)
    if reimb is None:
        raise HTTPException(status_code=404, detail="报销单不存在")
    advice = agent_service.generate_advice(db, reimb, user.id)
    return _advice_payload(advice, reimbursement_id)


@router.get("/agent/advice/{reimbursement_id}")
def get_advice(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(AGENT_ADVICE)),
):
    """读取最近一次建议；从未生成过则 404（前端据此显示「尚未分析」）。"""
    reimb = db.get(Reimbursement, reimbursement_id)
    if reimb is None:
        raise HTTPException(status_code=404, detail="报销单不存在")
    row = agent_service.latest_advice(db, reimbursement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="该单据还没有 AI 建议记录")

    import json

    try:
        payload = json.loads(row.payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    payload["adopted"] = row.adopted
    return {
        "reimbursement_id": reimbursement_id,
        "suggestion_id": row.id,
        "provider": row.provider,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "elapsed_ms": row.elapsed_ms,
        "degraded": bool(row.degraded),
        "error": row.error,
        "created_at": row.created_at,
        "advice": payload,
    }
