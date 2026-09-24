"""Jev typed-choice 客户端（FR-005）。

请求协议复用 jev-ultrafast/jev_ultrafast/model.py 的网关适配：
- POST TYPESAFE_ENDPOINT（endpoint/model/key 同源解析：进程环境 > jev-ultrafast/.env；
  指向 ai-gateway 时追加协议头，如 .env 的 https://ai-gateway.vercel.sh + typesafe-ai/jev 组合）
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
from pathlib import Path

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _resolve_env_file() -> Path:
    """解析 jev-ultrafast 的 .env 路径（导入时求值一次）。

    按序取首个存在者：`JEV_ULTRAFAST_ENV` 显式指定 > 本机 `~/project/jev-ultrafast/.env`
    > `~/.config/jev-ultrafast/.env` > **本仓库同级兄弟目录** `<workspace>/jev-ultrafast/.env`
    （自动适配不同机器的 checkout 布局，不写死用户名/绝对路径）。都不存在时返回本机默认
    位置，由 `_env_file_value` 判空处理（等价于"未配置"，走 fail-open 兜底）。
    """
    explicit = os.environ.get("JEV_ULTRAFAST_ENV")
    candidates = [
        Path(explicit) if explicit else None,
        Path.home() / "project" / "jev-ultrafast" / ".env",
        Path.home() / ".config" / "jev-ultrafast" / ".env",
        # 兄弟目录：jev-ultrafast 与本仓库同处一个 workspace（家机 ~/project、
        # 公司机 ~/workspace/github）——换机器/换用户名均自动成立
        Path(__file__).resolve().parents[1].parent / "jev-ultrafast" / ".env",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return candidate
    return candidates[1]


JEV_ULTRAFAST_ENV_FILE = _resolve_env_file()


class JevError(Exception):
    pass


def _endpoint() -> str:
    return _env("TYPESAFE_ENDPOINT") or DEFAULT_ENDPOINT


def _model_id() -> str:
    return _env("TYPESAFE_MODEL") or "jev-latest"


def _env_file_value(name: str, path: Path) -> str:
    """从 .env 读取指定变量（容忍空行/注释行/引号，同名取首个）。"""
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        var, _, value = line.partition("=")
        if var.strip() != name:
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            return value
    return ""


def _env(name: str) -> str:
    """endpoint/model 统一解析：进程环境变量优先，回落 jev-ultrafast/.env。

    endpoint/model/key 必须来自同一套凭据体系（.env 的 gateway 组合是配套的），
    避免"gateway key 打原生端点"式错配（S3 401 根因）。
    """
    return (os.environ.get(name) or _env_file_value(name, JEV_ULTRAFAST_ENV_FILE) or "").strip()


def _api_key() -> str:
    """key 解析：TYPESAFE_API_KEY → AI_GATEWAY_API_KEY → jev-ultrafast/.env；

    全空则抛 JevError（由 decide.py fail-open 降级），避免发空 Bearer 吃 401。
    """
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip() \
        or (os.environ.get("AI_GATEWAY_API_KEY") or "").strip()
    if not key:
        key = _env_file_value("TYPESAFE_API_KEY", JEV_ULTRAFAST_ENV_FILE)
    if not key:
        raise JevError("Jev key 未配置")
    return key


def _headers(model_id: str) -> dict:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
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
    # 注意：_headers 在构造请求前解析 key，缺失即在此抛出（不发任何 HTTP 请求）
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


def _normalize_complexity(answer: dict, criteria: dict) -> str:
    """complexity 宽容归一：越界/缺失值只降级该字段为 high（宁高勿低），不抛 JevError。

    task_class/implement_ref/reviewer_ref 仍走 _validate_choice 严格校验——那些是硬派发键，
    非法值会让整条决策不可信；complexity 仅影响档位提示，降级比整体 fail-open 代价小。
    """
    choice = answer.get("choice")
    if choice not in criteria:
        return "high"
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
            "criteria": {"low": "低风险、边界清晰", "high": "高风险或跨模块影响大"},
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
        "model": _model_id(),
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
        # complexity 宽容归一（宁高勿低）：越界/缺失不整体 JevError，只降级该字段为 high
        "complexity": _normalize_complexity(answers.get("complexity", {}),
                                            questions["complexity"]["criteria"]),
    }
    if implement_candidates:
        out["implement_ref"] = _validate_choice(
            answers.get("implement_model", {}), questions["implement_model"]["criteria"]
        )
    if reviewer_candidates:
        out["reviewer_ref"] = _validate_choice(answers.get("reviewer", {}), questions["reviewer"]["criteria"])
    return out
