"""交互式写入 LLM_API_KEY 到 backend/.env。

为什么单独做这个脚本：手改 .env 最容易出三类错 ——
  ① 改到了别的副本 / 忘了保存；② 中文输入法打出全角等号；③ Key 被引号或空格包住。
本脚本只做一件事，且输入**不回显**，Key 不会出现在命令行参数、PowerShell 历史或日志里。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\set_llm_key.py
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"


def masked(value: str) -> str:
    if len(value) <= 10:
        return f"****（长度 {len(value)}）"
    return f"{value[:5]}…{value[-4:]}（长度 {len(value)}）"


def main() -> int:
    if not ENV.exists():
        print(f"找不到 {ENV}")
        print("请确认项目路径，或先从 .env.example 复制一份 .env。")
        return 1

    print(f"目标文件: {ENV}")
    print("（如果这个路径跟你编辑器里打开的不是同一个，那就是之前为什么没生效）\n")

    # 交互终端用 getpass（不回显）；管道/IDE 等非交互场景退回读标准输入。
    # 不做这个分支的话，在没有 TTY 的终端里 getpass 会去读 Windows 控制台 CONIN$ 而卡住。
    if sys.stdin.isatty():
        key = getpass.getpass("请粘贴 API Key（输入不回显，粘贴后按回车）: ").strip()
    else:
        print("（检测到非交互输入，从标准输入读取一行）")
        key = sys.stdin.readline().strip()

    if not key:
        print("\n没有输入任何内容，已取消。")
        return 1
    if any(ch.isspace() for ch in key):
        print("\nKey 里含空白字符，疑似粘贴出错，已取消（没有改动文件）。")
        return 1
    if key.startswith(("'", '"')) or key.endswith(("'", '"')):
        print("\nKey 被引号包住了，请去掉引号后重试，已取消（没有改动文件）。")
        return 1

    lines = ENV.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and stripped.startswith("LLM_API_KEY"):
            out.append("LLM_API_KEY=" + key)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append("LLM_API_KEY=" + key)

    # 用 \n 写回，保持文件原有的 LF 与「无 BOM」
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")

    print(f"\n已写入: LLM_API_KEY={masked(key)}")
    print(f"  {'替换了原有行' if replaced else '新增了一行'}")

    # 顺手检查 provider，避免「填了 Key 但 provider 还是 mock」白忙一场
    provider = ""
    base_url = ""
    for line in out:
        s = line.strip()
        if s.startswith("LLM_PROVIDER="):
            provider = s.split("=", 1)[1].strip().lower()
        elif s.startswith("LLM_BASE_URL="):
            base_url = s.split("=", 1)[1].strip()

    print("\n当前配置：")
    print(f"  LLM_PROVIDER = {provider or '(未设置)'}")
    print(f"  LLM_BASE_URL = {base_url or '(用 provider 默认)'}")
    if provider in ("", "mock"):
        print("\n  !! LLM_PROVIDER 还是 mock —— mock 不联网，填了 Key 也不会生效。")
        print("     要接 DeepSeek 请把 LLM_PROVIDER 改成 deepseek。")

    print("\n下一步：")
    print("  1) 重启后端（.env 只在启动时读一次）")
    print("  2) .venv\\Scripts\\python.exe scripts\\check_llm_provider.py   # 验证链路")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
