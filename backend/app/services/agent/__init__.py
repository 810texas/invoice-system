"""审核助手包：事实块组装（数据最小化）与建议编排。

注意与 OCR 的关系：OCR 是「把影像变成字段」，本包是「把字段变成建议」。
两者都遵守同一条边界 —— **只做建议，不参与硬拦截判定**。
"""
from app.services.agent.facts import collect_facts, unmask_text
from app.services.agent.service import (
    PROMPT_VERSION,
    Advice,
    build_rule_summary,
    generate_advice,
    latest_advice,
    mark_adoption,
    normalize_payload,
    rule_conclusion,
)

__all__ = [
    "Advice",
    "PROMPT_VERSION",
    "build_rule_summary",
    "collect_facts",
    "generate_advice",
    "latest_advice",
    "mark_adoption",
    "normalize_payload",
    "rule_conclusion",
    "unmask_text",
]
