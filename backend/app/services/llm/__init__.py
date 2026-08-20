"""LLM 服务包：provider 抽象（mock / OpenAI 兼容云 API）与提示词。"""
from app.services.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.services.llm.provider import (
    CONCLUSIONS,
    CONFIDENCES,
    LlmProvider,
    LlmResult,
    MockLlmProvider,
    OpenAiCompatLlmProvider,
    get_provider,
    provider_label,
)

__all__ = [
    "CONCLUSIONS",
    "CONFIDENCES",
    "LlmProvider",
    "LlmResult",
    "MockLlmProvider",
    "OpenAiCompatLlmProvider",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "get_provider",
    "provider_label",
]
