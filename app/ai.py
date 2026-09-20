# -*- coding: utf-8 -*-
"""② AI 单次调用 —— 候选词交给 DeepSeek，拿回 [{术语, 解释}]。

三条硬约束（CLAUDE.md，改这里之前先想清楚）：

1. **只支持 DeepSeek**（第一期）。BASE_URL 是常量，不给用户填。
2. **必须一次调用**完成「筛选 + 写解释」。不许拆成「先挑术语、再逐个解释」两阶段 ——
   那样 N 个词就是 N 次调用，费用和延迟都失控。
3. **输出固定 [{术语, 解释}]**，上层只认这个形状。

另外两条是本文件自己的原则：

· **模型输出永远当不可信输入**。它会加 markdown 围栏、会在前面写「好的，以下是…」、
  会在被截断时给半个 JSON。解析器必须容忍这些，而不是崩掉。
· **错误要说人话**。失败时返回给界面的是「Key 不对」「余额不足」这种，
  不是 `HTTPError 401`。用户看不懂后者，也修不了。
"""
import json
import os
import re
import time

import requests

# ⚠ 这个环境变量是【留给联调脚本的接口】，不是给用户的功能开关。
#   有它，整条界面链路才能在不联网、不用真 Key 的情况下跑通一遍 ——
#   而 ② 的失败分支（401 / 超时 / 模型吐半个 JSON）拿真 Key 根本测不出来。
#   正常启动时这个变量不存在，行为与写死常量完全一致。
BASE_URL = os.environ.get("WENLEMA_BASE_URL") or "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"          # reasoner 会先吐一长串思维链，慢且贵；
                                 # 我们要的只是一句解释，用不上。
TIMEOUT = 60

# 喂给模型的量要设上限 —— OCR 出来几百行、上百个候选，prompt 会又贵又慢，
# 而且候选一多，模型漏看的概率明显上升。
MAX_LINES = 120
MAX_CANDIDATES = 60


def cap_candidates(cands, limit=None):
    """候选词截断 → (留下的, 丢掉的个数)。

    ⚠ 这件事【必须】发生在调用方看得见的地方，不能藏在 build_messages() 里。
      以前截断发生在发请求那一刻，于是 recognize() 和界面上所有算账的地方
      看到的都是【没截断】的完整列表 —— 第 61 个之后的候选
      既没发给 AI，也不在任何「剔除了几个」的清单里，
      就是凭空消失了。界面上「初筛 10 · 库里已有 3 · 问模型 6」的算术会直接对不上。
      现在截断挪到 ③ 之后由 recognize() 调，丢掉的那些会明确进 blocked。
    """
    cands = list(cands or [])
    n = MAX_CANDIDATES if limit is None else limit
    return cands[:n], max(0, len(cands) - n)


class AiError(Exception):
    """带上「能展示给用户的中文说明」的错误。"""

    def __init__(self, msg, detail=""):
        super().__init__(msg)
        self.msg = msg
        self.detail = detail


# ══════════ 组装请求 ══════════

# 通道 → user message 里那句候选表的表头。
#
# ⚠ **region 那份一个字都不许动。** 它是现有断言（AI端到端联调.py）切分
#   「原文 / 候选」两段的判据，改了它那些用例会整段失配。
_HEAD_CAND = {
    "region": "【规则初筛捞出的英文候选词】",
    "clip":   "【规则初筛捞出的术语候选词】",
}

# clip 通道在纯中文时，候选【必然】是空的 —— 规则初筛（pipeline.WORD）只认
# 英文字母。这时如果按老样子留个空行，模型看到的是「一个表头 + 什么都没有」，
# 很容易理解成「请求出问题了 / 这次没活干」，于是它一条都不挑。
# 那正是 v0.20 要防的那个静默失败（见 CLAUDE.md 决策 50），所以这里显式说清楚。
_EMPTY_CAND_HINT = "（空 —— 这段文字里机器没认出候选，请直接从原文里挑术语）"


def build_messages(system_prompt, payload, mode="region"):
    """payload 就是 pipeline.build_ai_payload() 的产物。

    ⚠ 这里【只】截原文行数（prompt 体积问题，属于本文件内部的事）。
      候选词一个都不截 —— 那是产品决策，要能被界面说清楚，
      由调用方用 cap_candidates() 做完再传进来。别在这里偷偷再截一次。

    mode: "region"（框选 / 自动通道）| "clip"（快捷键取词）。
    只影响那一句表头文字，**不影响英文路径的任何字节**（默认值就是 region）。
    """
    lines = list(payload.get("原文") or [])[:MAX_LINES]
    cands = list(payload.get("候选词") or [])

    head = _HEAD_CAND.get(mode) or _HEAD_CAND["region"]
    if cands:
        body = "、".join(cands)
    elif mode == "clip":
        body = _EMPTY_CAND_HINT
    else:
        # ⚠ 保持原样：region 通道空候选时就是什么都不写。
        #   这段以前是 "、".join([]) 的结果，现在显式写出来，字节完全一致。
        body = ""

    user = "【笔记原文】\n" + "\n".join(lines) + "\n\n" + head + "\n" + body
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


# ══════════ 解析模型输出 ══════════

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)
_TERM_KEYS = ("术语", "term", "单词", "名称")
_EXPL_KEYS = ("解释", "explanation", "释义", "说明")


def _as_list(obj):
    """模型可能给数组本身，也可能包一层 {"条目": [...]} / {"items": [...]}。"""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("条目", "items", "terms", "结果", "result", "data", "list"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
        # 再退一步：只有一个值时，取那个值
        vals = [v for v in obj.values() if isinstance(v, list)]
        if len(vals) == 1:
            return vals[0]
    return []


def _pick(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def parse_entries(text):
    """模型输出原文 → [{术语, 解释}]。

    容忍：markdown 围栏、「好的，以下是…」这类前言、数组/对象两种外壳。
    解析不出来就抛 AiError，让上层把原始输出展示出来 —— 别假装成功。
    """
    s = _FENCE.sub("", (text or "").strip())

    obj = None
    try:
        obj = json.loads(s)
    except Exception:
        # 前后有废话时，把第一个 { 或 [ 到最后一个 } 或 ] 之间抠出来再试
        for a, b in (("{", "}"), ("[", "]")):
            i, j = s.find(a), s.rfind(b)
            if i >= 0 and j > i:
                try:
                    obj = json.loads(s[i:j + 1])
                    break
                except Exception:
                    pass

    if obj is None:
        raise AiError("模型的回复不是 JSON，没法解析", (text or "")[:500])

    entries = []
    for it in _as_list(obj):
        if not isinstance(it, dict):
            continue
        term = _pick(it, _TERM_KEYS)
        expl = _pick(it, _EXPL_KEYS)
        if term and expl:
            entries.append({"term": term, "explanation": expl})
    return entries


# ══════════ 调用 ══════════

def _explain_http(resp):
    """把 HTTP 状态码翻译成人话。用户看不懂 401，但看得懂「Key 不对」。"""
    code = resp.status_code
    if code == 401:
        return AiError("API Key 不对，或者已经失效了")
    if code == 402:
        return AiError("DeepSeek 账户余额不足")
    if code == 429:
        return AiError("请求太频繁，被限流了，等一下再试")
    if code >= 500:
        return AiError(f"DeepSeek 服务端出错（HTTP {code}）", resp.text[:300])
    return AiError(f"请求失败（HTTP {code}）", resp.text[:300])


def explain(api_key, system_prompt, payload, mode="region"):
    """跑 ②。

    返回 {"ok", "entries", "usage", "seconds"}，
    失败时 {"ok": False, "msg": 人话, "detail": 原始信息}。

    注意 entries 里【没有】key 字段 —— 模型返回的术语拼写每轮都可能变
    （`Chunk` / `Chunking`、`RAG` / `RAG (Retrieval-Augmented Generation)`），
    拿它当知识库主键的话，同一个词永远查不到、永远重问。
    key 一律由调用方按【候选词】派生（见 kb.canon 与 Api.explain）。
    """
    if not api_key:
        return {"ok": False, "msg": "还没填 API Key"}

    body = {
        "model": MODEL,
        "messages": build_messages(system_prompt, payload, mode),
        "temperature": 0.2,      # 要的是稳定和一致，不是文采。
                                 # 同一个词今天解释成这样、明天解释成那样，用户会困惑。
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }

    t0 = time.perf_counter()
    try:
        resp = requests.post(
            BASE_URL + "/chat/completions",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=TIMEOUT,
        )
    except requests.Timeout:
        return {"ok": False, "msg": f"等了 {TIMEOUT} 秒还没回，超时了"}
    except requests.RequestException as e:
        return {"ok": False, "msg": "连不上 DeepSeek，检查一下网络", "detail": str(e)}

    if resp.status_code != 200:
        err = _explain_http(resp)
        return {"ok": False, "msg": err.msg, "detail": err.detail}

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        return {"ok": False, "msg": "回复的结构和预期对不上", "detail": f"{e}｜{resp.text[:300]}"}

    try:
        entries = parse_entries(content)
    except AiError as e:
        return {"ok": False, "msg": e.msg, "detail": e.detail}

    usage = data.get("usage") or {}
    return {
        "ok": True,
        "entries": entries,
        "seconds": round(time.perf_counter() - t0, 2),
        "usage": {
            "in": usage.get("prompt_tokens"),
            "out": usage.get("completion_tokens"),
            "total": usage.get("total_tokens"),
        },
    }


def check_key(api_key):
    """测连通性。

    用 GET /models —— 它验证的正是「这个 Key 有没有效」，
    而且不花钱。发一条真对话去测会白白多一次调用。
    """
    if not api_key:
        return {"ok": False, "msg": "还没填 API Key"}
    try:
        resp = requests.get(
            BASE_URL + "/models",
            headers={"Authorization": "Bearer " + api_key},
            timeout=15,
        )
    except requests.RequestException as e:
        return {"ok": False, "msg": "连不上 DeepSeek，检查一下网络", "detail": str(e)}

    if resp.status_code == 200:
        return {"ok": True, "msg": "接上了"}
    return {"ok": False, "msg": _explain_http(resp).msg}
