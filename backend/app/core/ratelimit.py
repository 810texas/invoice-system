"""公开接口限流（注册 / 忘记密码）。

需求 5.2 要求「写接口限流」，此前一直是缺项（`项目总览.md` §6.1 第 3 项）。
这里先覆盖**两个匿名可调用的接口** —— 它们最容易被刷：批量注册、反复触发重置邮件。

实现是**单进程内存滑动窗口**，够本项目单机部署用。
⚠️ 多实例部署时必须换成 Redis 之类的共享计数，否则每个实例各算一份，等于把限额放宽了 N 倍。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

# key → 命中时间戳队列
_hits: dict[str, deque[float]] = defaultdict(deque)

# 兜底：避免大量不同 IP 把内存撑爆（超出后整体清空，宁可短暂放宽也不让进程爆内存）
_MAX_KEYS = 10000


def client_ip(request: Request) -> str:
    """取客户端 IP。反向代理后面取 X-Forwarded-For 的第一段。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def hit(key: str, limit: int, window_seconds: int = 60) -> None:
    """记一次访问；超限抛 429（带 Retry-After）。limit <= 0 表示不限制。"""
    if limit is None or limit <= 0:
        return  # 配 0 表示关闭限流（测试或内网可这样配）
    if len(_hits) > _MAX_KEYS:
        _hits.clear()

    now = time.monotonic()
    bucket = _hits[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = int(window_seconds - (now - bucket[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"操作过于频繁，请 {retry_after} 秒后再试",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


def limit_public(request: Request, action: str, limit: int) -> None:
    """公开接口限流：按「动作 + 客户端 IP」计数。"""
    hit(f"{action}:{client_ip(request)}", limit=limit)


def reset() -> None:
    """清空计数（测试用）。"""
    _hits.clear()
