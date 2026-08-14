"""应用配置：从 .env 读取，AI 服务（OCR/LLM）provider 的切换总开关。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 数据库
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "invoice_system"

    # JWT
    JWT_SECRET: str = "dev-insecure-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # AI 服务切换：OCR_PROVIDER=mock|paddle  LLM_PROVIDER=mock|deepseek|qwen|glm|openai
    # LLM 的 mock 与 OCR 的 mock 同理：确定性输出，使联调与自动化验收不依赖网络与密钥。
    # 云端几家都走 OpenAI 兼容协议，只是 base_url 与默认模型不同（见 services/llm/provider.py）。
    OCR_PROVIDER: str = "mock"
    LLM_PROVIDER: str = "mock"

    # 云 LLM 连接（LLM_PROVIDER != mock 时生效）
    LLM_API_KEY: str = ""
    # 留空则按 provider 取默认：deepseek→api.deepseek.com、qwen→DashScope 兼容模式、
    # glm→智谱、openai→官方
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = ""
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_MAX_TOKENS: int = 900
    LLM_TEMPERATURE: float = 0.2
    # JSON Output 模式下部分实现有概率返回空 content（DeepSeek 文档有说明），
    # 拿不到可解析 JSON 时额外重试几次；0 表示不重试
    LLM_JSON_RETRY: int = 1
    # 数据最小化：为 true 时把销售方名称换成「销售方A/B…」（本地保留映射），
    # 购买方/税号/发票号码/影像本来就不外发（语义分析用不到，见 services/agent/facts.py）
    LLM_MASK_PII: bool = True

    # PaddleOCR（OCR_PROVIDER=paddle 时生效）：本地推理，影像与结果都不出本机
    PADDLE_OCR_LANG: str = "ch"
    PADDLE_OCR_USE_GPU: bool = False
    # 关掉 ppocr 的 DEBUG 日志（每次识别都会打一大段参数与进度条）
    PADDLE_OCR_SHOW_LOG: bool = False
    # 离线部署：留空表示首次运行时自动联网下载模型到 ~/.paddleocr；
    # 填了目录则直接用现成模型，完全不联网
    PADDLE_OCR_DET_MODEL_DIR: str = ""
    PADDLE_OCR_REC_MODEL_DIR: str = ""
    PADDLE_OCR_CLS_MODEL_DIR: str = ""

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173"

    # 抬头核验：公司主体（需求 4.4）
    COMPANY_NAME: str = "示例科技有限公司"
    COMPANY_TAX_NO: str = "91330100MA2XXXXXXX"

    # 预审规则参数
    OCR_CONFIDENCE_THRESHOLD: float = 0.85
    AMOUNT_TOLERANCE: float = 0.01
    BUDGET_WARN_RATIO: float = 0.8
    # 抬头核验（购买方名称/税号 vs 公司主体）：公司主体还是占位值时建议关掉，否则真实发票必然误报
    PREAUDIT_BUYER_CHECK: bool = True

    # 上传
    MAX_UPLOAD_MB: int = 10
    UPLOAD_DIR: str = "storage/uploads"

    # PDF 发票：拆页转图的渲染倍数（2.0 ≈ 144dpi，OCR 内部还会缩到 960px 长边）与单份页数上限
    PDF_RENDER_SCALE: float = 2.0
    MAX_PDF_PAGES: int = 20

    # 登录安全：连续 5 次错误锁定 15 分钟
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCK_MINUTES: int = 15

    # ---- 账号生命周期（真实使用场景）----
    # 密码强度：至少 N 位，且必须同时含字母与数字。
    # 注册、自助重置、管理员建号/重置都走同一条规则，避免口径不一致。
    PASSWORD_MIN_LENGTH: int = 8

    # 首次启动引导管理员。BOOTSTRAP_ADMIN_PASSWORD 留空则生成一次性随机密码并打印到后端控制台。
    BOOTSTRAP_ADMIN_USERNAME: str = "admin"
    BOOTSTRAP_ADMIN_PASSWORD: str = ""
    BOOTSTRAP_ADMIN_NAME: str = "系统管理员"

    # 自助注册默认进入的部门。注意：**部门决定预算上限**，所以不允许注册者自选部门。
    # 上限刻意设得很小 —— 不能是 0，因为预审里写的是 `if monthly_limit:`，
    # 上限为 0 会被当成「没设上限」从而完全不检查预算。
    DEFAULT_DEPT_NAME: str = "待分配"
    DEFAULT_DEPT_SINGLE_LIMIT: float = 100.0
    DEFAULT_DEPT_MONTHLY_LIMIT: float = 500.0

    # 财务人员默认所属部门。部门只影响「申请人所在部门的预算」与数据范围，
    # 而财务既没有 reimb:submit、数据范围又是 ALL —— 部门对它纯属展示信息。
    # 既然没有实际作用，就不该让管理员建号时每次手动选一遍。
    FINANCE_DEPT_NAME: str = "财务部"

    # 忘记密码：重置令牌有效期（token 只存哈希、单次有效）
    RESET_TOKEN_EXPIRE_MINUTES: int = 30
    # 是否把重置链接打印到后端控制台。**仅供本地演示/排障**，生产必须保持 False
    # （否则任何能看到日志的人都可能拿到重置链接）。默认走「管理员审批重置」这条路。
    RESET_TOKEN_ECHO: bool = False

    # 公开接口（注册 / 忘记密码）限流。内存计数，单进程有效；多实例部署需换成 Redis。
    # 注册**只按 IP** 且刻意宽松：公司出口 NAT 下几十个员工共用一个 IP，
    # 卡太死会把正常注册的人一起拦住（这一条是防脚本批量刷号，不是防人）。
    REGISTER_RATE_LIMIT_PER_MINUTE: int = 30
    # 忘记密码**按 IP**（防止有人遍历账号触发重置邮件）
    FORGOT_RATE_LIMIT_PER_MINUTE: int = 10
    # 忘记密码**再按账号**收紧（防止拿同一个邮箱反复轰炸对方）
    FORGOT_ACCOUNT_RATE_LIMIT_PER_MINUTE: int = 3
    # 重置密码（凭令牌）：按 IP。令牌本身是一次性的，这里只防暴力猜测令牌
    RESET_RATE_LIMIT_PER_MINUTE: int = 10

    # 邮件（忘记密码发重置链接）。
    # SMTP_HOST 为空时自动降级为「提交重置申请 → 管理员在用户管理里重置」，离线也能跑通。
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_SSL: bool = True
    SMTP_FROM: str = ""
    SMTP_FROM_NAME: str = "智能发票报销审核系统"

    # 拼重置链接用（邮件里的链接指向前端页面）
    FRONTEND_BASE_URL: str = "http://localhost:5173"

    # Cookie 名
    ACCESS_COOKIE: str = "access_token"
    REFRESH_COOKIE: str = "refresh_token"
    CSRF_COOKIE: str = "csrf_token"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
