"""FastAPI 应用入口。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="智能发票报销审核系统",
    description="OCR + 大模型语义理解的智能报销审核系统 API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 列表接口把总数放在这个响应头里，浏览器跨源读取需要显式暴露
    expose_headers=["X-Total-Count"],
)

app.include_router(api_router)


@app.get("/")
def root():
    return {"name": "智能发票报销审核系统", "docs": "/docs"}
