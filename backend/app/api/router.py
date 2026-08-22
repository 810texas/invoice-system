"""汇总 /api/v1 各子路由。"""
from fastapi import APIRouter

from app.api.v1 import admin, agent, auth, health, invoices, meta, reimb, review, upload

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(meta.router, tags=["meta"])
api_router.include_router(upload.router, tags=["invoice"])
api_router.include_router(invoices.router, tags=["invoice"])
api_router.include_router(reimb.router, tags=["reimbursement"])
api_router.include_router(review.router, tags=["review"])
api_router.include_router(agent.router, tags=["agent"])
api_router.include_router(admin.router, tags=["admin"])
