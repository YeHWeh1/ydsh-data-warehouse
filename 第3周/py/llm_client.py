# -*- coding: utf-8 -*-
"""
llm_client.py —— 大模型 API 接入与密钥管理（任务书子任务 3）

设计要点：
  1. 密钥只从环境变量 / .env 读取，绝不硬编码、绝不写进日志
  2. .env 已被 .gitignore 排除（见仓库根 .gitignore）
  3. 支持 DeepSeek / 通义千问 / OpenAI 三种 OpenAI 兼容端点
  4. 无 Key 时自动进入「离线模式」：返回结构化占位内容，
     保证整条流水线（NL2SQL 校验、报告生成）在没有网络时也能跑通并自证逻辑

安全约定（对应手册 1.3 "密钥泄露"难点）：
  - mask_key() 只暴露前 6 位与后 4 位
  - 任何异常信息在抛出前先过 sanitize()，防止 Key 随报错泄露到日志
"""
import json
import os
import re
import time
from pathlib import Path

from config import ENV_FILE, LLM_MAX_RETRY, LLM_PROVIDER, LLM_PROVIDERS, LLM_TIMEOUT


# ============================================================
# 密钥管理
# ============================================================
def load_env():
    """
    读取 .env（不覆盖已存在的环境变量）。
    不依赖 python-dotenv 的自动加载，避免误加载到别的目录的 .env。
    """
    if not ENV_FILE.exists():
        return {}
    loaded = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
        loaded[k] = v
    return loaded


def mask_key(key):
    """只露前 6 后 4，用于日志与文档展示。"""
    if not key:
        return "(未设置)"
    if len(key) <= 12:
        return key[:2] + "*" * (len(key) - 2)
    return f"{key[:6]}{'*' * 8}{key[-4:]}"


def sanitize(text, key=None):
    """把文本里出现的真实 Key 替换成掩码，防止随报错泄露。"""
    if not text:
        return text
    if key and len(key) > 8:
        text = text.replace(key, mask_key(key))
    # 兜底：任何形如 sk-xxxx 的长串都打码
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{8,})", lambda m: mask_key(m.group(1)), text)
    return text


def get_api_key(provider=None):
    """按优先级取 Key：环境变量 → .env。返回 (key, provider_name, meta)。"""
    load_env()
    provider = provider or LLM_PROVIDER
    cfg = LLM_PROVIDERS.get(provider)
    if not cfg:
        return None, provider, {"error": f"未知 provider: {provider}"}
    key = os.environ.get(cfg["key_env"], "").strip()
    if not key:
        # 尝试其它已配置的 provider（用户可能只配了一家）
        for name, c in LLM_PROVIDERS.items():
            k = os.environ.get(c["key_env"], "").strip()
            if k:
                return k, name, LLM_PROVIDERS[name]
        return None, provider, cfg
    return key, provider, cfg


# ============================================================
# 调用
# ============================================================
class LLMResult:
    """统一返回结构，便于上层判断是否走了离线兜底。"""

    def __init__(self, text, ok, provider, model, elapsed, error=None, offline=False):
        self.text = text
        self.ok = ok
        self.provider = provider
        self.model = model
        self.elapsed = elapsed
        self.error = error
        self.offline = offline

    def to_dict(self):
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "elapsed_s": round(self.elapsed, 2),
            "offline": self.offline,
            "error": self.error,
            "chars": len(self.text or ""),
        }


def chat(messages, provider=None, temperature=0.2, max_tokens=2000,
         offline_fallback=None, json_mode=False):
    """
    调用大模型。

    参数：
      messages        : [{"role": "...", "content": "..."}]
      offline_fallback: 无 Key / 调用失败时返回的文本（保证流水线不中断）
      json_mode       : 是否要求返回 JSON（OpenAI 兼容接口支持 response_format）

    返回：LLMResult
    """
    key, prov, cfg = get_api_key(provider)
    t0 = time.time()

    if not key:
        text = offline_fallback if offline_fallback is not None else ""
        return LLMResult(text, ok=False, provider=prov, model="(offline)",
                         elapsed=time.time() - t0, offline=True,
                         error=f"未配置 {cfg.get('key_env', 'API Key')}，已使用离线兜底")

    try:
        from openai import OpenAI
    except ImportError:
        return LLMResult(offline_fallback or "", False, prov, cfg["model"],
                         time.time() - t0, offline=True,
                         error="未安装 openai 包，已使用离线兜底")

    client = OpenAI(api_key=key, base_url=cfg["base_url"], timeout=LLM_TIMEOUT)
    last_err = None

    for attempt in range(LLM_MAX_RETRY + 1):
        try:
            kwargs = dict(model=cfg["model"], messages=messages,
                          temperature=temperature, max_tokens=max_tokens)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            text = (resp.choices[0].message.content or "").strip()
            return LLMResult(text, True, prov, cfg["model"], time.time() - t0)
        except Exception as e:
            last_err = sanitize(f"{type(e).__name__}: {e}", key)
            if attempt < LLM_MAX_RETRY:
                time.sleep(1.5 * (attempt + 1))

    text = offline_fallback if offline_fallback is not None else ""
    return LLMResult(text, False, prov, cfg["model"], time.time() - t0,
                     error=last_err, offline=True)


def chat_json(messages, provider=None, temperature=0.1, offline_fallback=None):
    """
    要求模型返回 JSON，并做一次容错解析（模型常带 ```json 包裹）。
    解析失败时返回 (None, LLMResult)。
    """
    res = chat(messages, provider=provider, temperature=temperature,
               offline_fallback=offline_fallback, json_mode=True)
    if not res.ok or not res.text:
        return None, res
    raw = res.text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw), res
    except json.JSONDecodeError:
        # 尝试截取第一个 { 到最后一个 }
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0)), res
            except json.JSONDecodeError:
                pass
    res.error = "返回内容不是合法 JSON"
    res.ok = False
    return None, res


def ensure_env_file():
    """
    首次运行时生成 .env 模板（不含真实 Key），并确认 .gitignore 已排除。
    返回 (created, gitignored)。
    """
    created = False
    if not ENV_FILE.exists():
        ENV_FILE.write_text(
            "# 第 3 周 大模型配置（本文件已被 .gitignore 排除，切勿提交）\n"
            "# 任选其一填写，留空则自动进入离线模式\n"
            "DEEPSEEK_API_KEY=\n"
            "DASHSCOPE_API_KEY=\n"
            "OPENAI_API_KEY=\n"
            "LLM_PROVIDER=deepseek\n",
            encoding="utf-8",
        )
        created = True

    gitignore = ENV_FILE.parent / ".gitignore"
    ignored = False
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8", errors="ignore")
        ignored = ".env" in content.split()
    return created, ignored


if __name__ == "__main__":
    from console import init_console
    init_console()
    print("=" * 60)
    print("  大模型接入自检")
    print("=" * 60)
    created, ignored = ensure_env_file()
    print(f"  .env 存在：{ENV_FILE}")
    print(f"  .env 新建：{created}")
    print(f"  .gitignore 已排除 .env：{ignored}")

    key, prov, cfg = get_api_key()
    print(f"  provider：{prov}")
    print(f"  model   ：{cfg.get('model')}")
    print(f"  API Key ：{mask_key(key)}")
    print(f"  模式    ：{'在线调用' if key else '离线兜底（未配置 Key）'}")
