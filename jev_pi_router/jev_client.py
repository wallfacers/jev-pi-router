"""Jev typed-choice 客户端（FR-005）。

请求协议复用 jev-ultrafast/jev_ultrafast/model.py 的网关适配：
- POST TYPESAFE_ENDPOINT（默认 TypeSafe 原生；指向 ai-gateway 时追加协议头）
- body: {model, state, questions:{id: {type:"choice", criteria, instructions}}}
- 响应: {answers:{id:{choice, probabilities, confidence}}}
决策调用是独立 evaluation 请求（max_tokens:0 语义），不进入任何对话上下文。
任何错误抛 JevError，由 decide.py fail-open 回退规则（FR-006）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class JevError(Exception):
    pass


def _endpoint() -> str:
    return os.environ.get("TYPESAFE_ENDPOINT", DEFAULT_ENDPOINT)


def _headers(model_id: str) -> dict:
    headers = {
        "Authorization": f"Bearer {os.environ.get('TYPESAFE_API_KEY') or os.environ.get('AI_GATEWAY_API_KEY', '')}",
        "Content-Type": "application/json",
    }
    if "ai-gateway" in _endpoint():
        headers.update({
            "ai-gateway-protocol-version": "0.0.1",
            "ai-evaluation-model-specification-version": "4",
            "ai-model-id": model_id,
        })
    return headers


def _post(body: dict, timeout_s: float) -> dict:
    request = urllib.request.Request(
        _endpoint(), data=json.dumps(body).encode("utf-8"), headers=_headers(body["model"]), method="POST"
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 529, 503) and attempt < 2:
                time.sleep(0.5 * 2**attempt)
                continue
            raise JevError(f"Jev HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JevError(f"Jev 连接失败: {exc}") from exc
    raise JevError(f"Jev 重试耗尽: {last_error}")


def _validate_choice(answer: dict, criteria: dict) -> str:
    choice = answer.get("choice")
    if choice not in criteria:
        raise JevError(f"Jev 返回非法选项: {choice}")
    return choice


def decide(task_brief: str, risk_tags: list, implement_candidates: list, reviewer_candidates: list,
           timeout_ms: int = 2000) -> dict:
    """一次请求并发四问（task_class / complexity / implement_model / reviewer）。

    candidate 列表元素为 {vendor, model, api_ref}。
    """
    questions = {
        "task_class": {
            "type": "choice",
            "criteria": {
                "design": "需要设计/架构/方案权衡的任务",
                "implement": "设计已定、按明确要求编写代码的任务",
                "chore": "杂务（文档、清理、配置调整等）",
            },
            "instructions": {"goal": "判断子任务类别", "rules": ["依据任务描述，不猜测隐含需求"]},
        },
        "complexity": {
            "type": "choice",
            "criteria": {"low": "低风险、边界清晰", "medium": "一般复杂度", "high": "高风险或跨模块影响大"},
            "instructions": {"goal": "评估复杂度", "rules": ["risk_tags 提示高风险时倾向 high"]},
        },
    }
    if implement_candidates:
        questions["implement_model"] = {
            "type": "choice",
            "criteria": {
                c["api_ref"]: f"{c['vendor']}/{c['model']}（成本权重 {c.get('cost_hint', 1.0)}，缓存 {c.get('cache_passthrough', 'unknown')}）"
                for c in implement_candidates
            },
            "instructions": {"goal": "选择实现模型", "rules": ["低成本优先，缓存亲和优先"]},
        }
    if reviewer_candidates:
        questions["reviewer"] = {
            "type": "choice",
            "criteria": {
                c["api_ref"]: f"{c['vendor']}/{c['model']}（reviewer 候选）"
                for c in reviewer_candidates
            },
            "instructions": {"goal": "选择 reviewer", "rules": ["必须与产出者不同厂商（异源）"]},
        }

    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {"task_brief": task_brief[:2000], "risk_tags": risk_tags or []},
        "questions": questions,
    }
    try:
        result = _post(body, timeout_ms / 1000.0)
    except JevError:
        raise
    except Exception as exc:  # 防御：任何意外都走 fail-open
        raise JevError(str(exc)) from exc

    answers = result.get("answers") or {}
    out = {
        "task_class": _validate_choice(answers.get("task_class", {}), questions["task_class"]["criteria"]),
        "complexity": _validate_choice(answers.get("complexity", {}), questions["complexity"]["criteria"]),
    }
    if implement_candidates:
        out["implement_ref"] = _validate_choice(
            answers.get("implement_model", {}), questions["implement_model"]["criteria"]
        )
    if reviewer_candidates:
        out["reviewer_ref"] = _validate_choice(answers.get("reviewer", {}), questions["reviewer"]["criteria"])
    return out
