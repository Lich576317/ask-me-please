# -*- coding: utf-8 -*-
"""② AI 调用联调 —— 本地假服务器顶替 DeepSeek。

跑起来：
    python "Desktop\\问了吗\\冒烟测试\\AI接入联调.py"

────────────────────────────────────────────────────────
为什么不用真 Key 测：

  1. 用户的 Key 要等出成品时才给（「apikey我会在你出成品的时候输入测试」）。
  2. 就算有 Key，真 Key 也测不了失败路径 —— 401、超时、模型吐半个 JSON，
     这些恰恰是最容易写错、又最难在正常使用中碰到的分支。

所以这里起一个本地 HTTP 服务器冒充 DeepSeek，把 ai.BASE_URL 指过去。
发出去的是**真请求、真 JSON、真 HTTP 状态码**，只有「对面是谁」是假的。
✅ 全程不联网，不花一分钱。

⚠ 反过来说清楚它测不到什么：
   真实 DeepSeek 的限流行为、真实模型对提示词的服从度、真实 token 计费 ——
   这些只能等真 Key 到手再验。见本文件末尾「未覆盖」。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)

# 本地回环别走代理 —— 国内机器上 HTTP_PROXY 常常是设着的，
# 请求 127.0.0.1 也可能被塞给代理，然后莫名其妙地连不上。
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

# ⚠ v0.8 起，Api.explain() 拿到模型回答后会【写知识库】（③ 的入口），
#   而本脚本正好直接调 Api.explain（见 t_api_wrapper）。
#   不把库指开的话，它写的就是用户一点一点攒出来的 app\kb.db ——
#   而且塞进去的全是下面这些假解释。
#   和 AI端到端联调.py 一个办法：WENLEMA_KB 是留给联调脚本的接口，
#   不是给用户的功能开关，正常启动时它不存在。
KB_DB = os.path.join(HERE, "_aiunit_kb.db")
os.environ["WENLEMA_KB"] = KB_DB
for _p in (KB_DB, KB_DB + "-wal", KB_DB + "-shm"):
    if os.path.exists(_p):
        os.remove(_p)

import requests                                                  # noqa: E402
import ai                                                        # noqa: E402

# 「出口自检」那把共用的尺子（见 `尺子.py`）。
sys.path.insert(0, HERE)
import 尺子                                                       # noqa: E402


# ══════════════════════════════════════════════════════════
# 假 DeepSeek
# ══════════════════════════════════════════════════════════

SCEN = {            # 服务器【该怎么答】
    "status": 200,
    "content": "",
    "delay": 0.0,
}
GOT = {             # 服务器【收到了什么】
    "hits": 0,
    "last": None,
    "auth": None,
}


def _body(content, usage=None):
    """OpenAI 兼容的回复壳。字段名要和 DeepSeek 真的一模一样，
    不然测的是「我编的协议」，不是「DeepSeek 的协议」。"""
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": "deepseek-chat",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20,
                           "total_tokens": 120},
    }


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                    # 别往 stderr 刷访问日志，测试输出要能一眼看完

    def _send(self, code, text):
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self.path.endswith("/models"):
            return self._send(404, "{}")
        if SCEN["status"] != 200:
            return self._send(SCEN["status"], '{"error":{"message":"fake"}}')
        self._send(200, json.dumps({"object": "list",
                                    "data": [{"id": "deepseek-chat"}]}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            GOT["last"] = json.loads(raw)
        except Exception:
            GOT["last"] = {"__unparsed__": raw}
        GOT["auth"] = self.headers.get("Authorization")
        GOT["hits"] += 1

        if SCEN["delay"]:
            time.sleep(SCEN["delay"])
        if SCEN["status"] != 200:
            return self._send(SCEN["status"],
                              '{"error":{"message":"Authentication Fails"}}')
        self._send(200, json.dumps(_body(SCEN["content"]), ensure_ascii=False))


# ══════════════════════════════════════════════════════════
# 断言小工具
# ══════════════════════════════════════════════════════════

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [OK] " + name)
    else:
        FAIL.append((name, detail))
        print("  [!!] " + name + ("　→ " + str(detail) if detail else ""))


def answer(content, status=200, delay=0.0):
    """让假服务器下一次这么答。"""
    SCEN.update(status=status, content=content, delay=delay)


def J(obj):
    return json.dumps(obj, ensure_ascii=False)


CAND = {"原文": ["RAG 是先把资料查出来再回答", "还要做 Chunking"],
        "候选词": ["RAG", "Chunking", "OK"]}


# ══════════════════════════════════════════════════════════
# 各分支
# ══════════════════════════════════════════════════════════

def t_happy():
    print("\n【1】正常路径")
    answer(J({"条目": [{"术语": "RAG", "解释": "检索增强生成：先查资料再让模型回答。"},
                       {"术语": "Chunking", "解释": "把长文档切成小块，方便检索。"}]}))
    GOT["hits"] = 0
    r = ai.explain("sk-test", "系统提示词占位", CAND)

    check("返回 ok", r.get("ok") is True, r)
    check("解析出 2 条", len(r.get("entries") or []) == 2, r.get("entries"))
    check("术语取对了", (r["entries"][0]["term"] == "RAG"))
    check("解释取对了", "检索增强生成" in r["entries"][0]["explanation"])
    check("带了 usage", (r.get("usage") or {}).get("total") == 120, r.get("usage"))
    check("带了耗时", isinstance(r.get("seconds"), float))
    check("没多打请求", GOT["hits"] == 1, GOT["hits"])

    # 前端只认 term / explanation 这两个键（app.js 的 renderStage2）。
    # 后端哪天改了名字而前端没跟着改，卡片会静默变空 —— 所以钉死。
    check("条目字段名 = term/explanation",
          all(set(e) == {"term", "explanation"} for e in r["entries"]),
          [sorted(e) for e in r["entries"]])


def t_request_shape():
    print("\n【2】发出去的请求长什么样（前端和后端的分工全在这）")
    req = GOT["last"] or {}
    check("打到 /chat/completions", req.get("model") == "deepseek-chat")
    check("要求 JSON 输出", req.get("response_format") == {"type": "json_object"})
    check("temperature 压到 0.2", req.get("temperature") == 0.2,
          "要的是一致性，不是文采 —— 同一个词两天解释得不一样，用户会困惑")
    check("不流式", not req.get("stream"))

    msgs = req.get("messages") or []
    check("两条消息", len(msgs) == 2, msgs)
    check("messages[0] 是 system 且用了传进来的提示词",
          msgs and msgs[0].get("role") == "system"
          and msgs[0].get("content") == "系统提示词占位", msgs[:1])
    check("messages[1] 是 user", msgs and msgs[1].get("role") == "user")

    u = msgs[1].get("content", "") if len(msgs) > 1 else ""
    check("原文进 user", "RAG 是先把资料查出来再回答" in u)
    check("候选词进 user", "RAG、Chunking、OK" in u, u[:120])
    check("Authorization 带 Bearer", GOT["auth"] == "Bearer sk-test", GOT["auth"])

    # ── v0.20：clip 通道的 user message 长什么样（决策 50）────────────
    # 这段头文字是 **① 和 ② 之间关于「候选从哪来」的书面约定**：
    #   region 那条说「英文候选词」，因为 ① 的正则只认英文；
    #   clip 那条不能说英文 —— 它中英不限，而且候选【经常是空的】。
    #
    # ⚠ 英文那一句一个字都不许动：`AI端到端联调.py` 是按
    #   `【规则初筛捞出的英文候选词】` 切段来查候选的，改了那边全红。
    u_region = ai.build_messages("s", CAND, "region")[1]["content"]
    check("★ region 那份头文字【原样】还是「英文候选词」",
          "【规则初筛捞出的英文候选词】" in u_region,
          "这是现有测试切段的判据，动它会让一整批断言红掉")

    u_clip = ai.build_messages("s", CAND, "clip")[1]["content"]
    check("clip 那份不说「英文」",
          "英文候选词" not in u_clip, u_clip[:80])
    check("clip 那份说「术语候选词」（中英不限）",
          "【规则初筛捞出的术语候选词】" in u_clip, u_clip[:80])

    # ★ 候选为空时的措辞。clip 模式下这是【常态】不是异常 ——
    #   用户选一段纯中文，① 那条只认英文字母的正则一个词都给不出来，
    #   术语只能由 ② 从原文里挑。这里必须把「空是正常的」说出来，
    #   否则模型会以为上游出错了，回一句「候选为空无法处理」。
    empty = dict(CAND)
    empty["候选词"] = []
    u_empty = ai.build_messages("s", empty, "clip")[1]["content"]
    check("★ clip 候选为空时，明说「空是正常的，请从原文里挑」",
          "空" in u_empty and "用不着你操心" not in u_empty,
          u_empty[-120:])
    check("clip 候选为空时【没有】把空列表原样丢给模型",
          "候选词】\n\n" not in u_empty, repr(u_empty[-60:]))

    # 反向：region 那条【不许】出现「空是正常的」——
    # 它那边的空候选是「全被知识库回答了」，压根不该走到 ②。
    u_region_empty = ai.build_messages("s", empty, "region")[1]["content"]
    check("region 候选为空时不给「空是正常的」那句（那边走不到这儿）",
          u_empty != u_region_empty)


def t_tolerate():
    print("\n【3】模型输出不听话时，解析器要能兜住")

    # 3a markdown 围栏 —— 最常见的一种，提示词里明说了不要，它还是会加
    answer("```json\n" + J({"条目": [{"术语": "MVP", "解释": "最小可用产品。"}]}) + "\n```")
    r = ai.explain("sk-test", "s", CAND)
    check("围栏 JSON 能解析", r.get("ok") and r["entries"][0]["term"] == "MVP", r)

    # 3b 前后带废话
    answer("好的，以下是筛选结果：\n" +
           J({"条目": [{"术语": "Prompt", "解释": "提示词。"}]}) +
           "\n希望有帮助！")
    r = ai.explain("sk-test", "s", CAND)
    check("带前言后语能解析", r.get("ok") and r["entries"][0]["term"] == "Prompt", r)

    # 3c 直接给数组（提示词要求对象，它不一定听）
    answer(J([{"术语": "Embedding", "解释": "向量化。"}]))
    r = ai.explain("sk-test", "s", CAND)
    check("裸数组能解析", r.get("ok") and r["entries"][0]["term"] == "Embedding", r)

    # 3d 英文键名
    answer(J([{"term": "Recall", "explanation": "召回率。"}]))
    r = ai.explain("sk-test", "s", CAND)
    check("英文键名能认", r.get("ok") and r["entries"][0]["term"] == "Recall", r)

    # 3e 换了个外壳的数组
    answer(J({"items": [{"术语": "Top-K", "解释": "取前 K 个。"}]}))
    r = ai.explain("sk-test", "s", CAND)
    check("换个外层键也能捞出来", r.get("ok") and r["entries"][0]["term"] == "Top-K", r)


def t_empty_and_bad():
    print("\n【4】空结果 与 解析不出来")

    answer(J({"条目": []}))
    r = ai.explain("sk-test", "s", CAND)
    check("空列表算成功（不是失败）", r.get("ok") is True and r["entries"] == [], r)

    answer("抱歉，我无法完成这个请求。")
    r = ai.explain("sk-test", "s", CAND)
    check("非 JSON → ok=False", r.get("ok") is False, r)
    check("非 JSON → 说的是人话", "JSON" in (r.get("msg") or ""), r.get("msg"))
    check("非 JSON → 带上原始输出好排查",
          "无法完成" in (r.get("detail") or ""), r.get("detail"))


def t_http():
    print("\n【5】HTTP 错误要说人话（用户看不懂 401，看得懂「Key 不对」）")
    for status, want, label in (
        (401, "Key", "401 → Key 不对"),
        (402, "余额", "402 → 余额不足"),
        (429, "限流", "429 → 被限流"),
        (500, "服务端", "500 → 服务端出错"),
    ):
        answer("", status=status)
        r = ai.explain("sk-test", "s", CAND)
        check(label, r.get("ok") is False and want in (r.get("msg") or ""),
              r.get("msg"))


def t_timeout():
    print("\n【6】超时")
    old = ai.TIMEOUT
    ai.TIMEOUT = 2                       # 真等 60 秒太久了，临时压到 2 秒
    answer("", delay=5.0)
    t0 = time.perf_counter()
    try:
        r = ai.explain("sk-test", "s", CAND)
    finally:
        ai.TIMEOUT = old
    dt = time.perf_counter() - t0
    check("超时 → ok=False", r.get("ok") is False, r)
    check("超时 → 说的是人话", "超时" in (r.get("msg") or ""), r.get("msg"))
    check("超时 → 真的按 TIMEOUT 断了", dt < 4, f"{dt:.1f}s")


def t_no_key():
    print("\n【7】没填 Key 时不该发请求")
    GOT["hits"] = 0
    r = ai.explain("", "s", CAND)
    check("直接失败", r.get("ok") is False, r)
    check("说清楚原因", "API Key" in (r.get("msg") or ""), r.get("msg"))
    check("一个包都没发出去", GOT["hits"] == 0, GOT["hits"])


def t_truncate():
    print("\n【8】喂给模型的量有上限（防着 OCR 出几百行）")
    lines = [f"line {i} alpha" for i in range(1, 201)]          # 200 行
    cands = [f"T{i}" for i in range(100)]

    answer(J({"条目": []}))
    ai.explain("sk-test", "s", {"原文": lines, "候选词": cands})
    u = GOT["last"]["messages"][1]["content"]

    check(f"原文截到 {ai.MAX_LINES} 行", "line 120 alpha" in u and "line 121 alpha" not in u)

    # ⚠ 这一条 v0.8 起【反过来】了：候选一个都不许在 build_messages 里截。
    #   以前截断发生在发请求那一刻，于是 recognize() 和界面上所有算账的地方
    #   看到的都是没截断的完整列表 —— 第 61 个之后的候选既没发给 AI，
    #   也不在任何「剔除了几个」的清单里，就是凭空消失了。
    #   界面上「初筛 10 · 库里已有 3 · 问模型 6」的算术会直接对不上。
    #   现在截断归 cap_candidates 管，由 recognize() 在 ③ 之后调，丢掉的进 blocked。
    #   所以这里要钉的是【两边各管各的】，不是「截了多少」。
    check("build_messages 不截候选（截断归 cap_candidates 管）",
          "、".join(cands) in u,
          "候选被偷偷截了的话，recognize() 就算不出「丢掉了几个」")

    kept, dropped = ai.cap_candidates(cands)
    check(f"cap_candidates 截到 {ai.MAX_CANDIDATES} 个", len(kept) == ai.MAX_CANDIDATES,
          len(kept))
    check("并且如实报出丢掉几个（这个数要能摆到界面上）",
          dropped == len(cands) - ai.MAX_CANDIDATES, dropped)
    check("留的是前 N 个（和界面上「初筛 N」的顺序对得上）",
          kept[:1] == ["T0"] and kept[-1] == f"T{ai.MAX_CANDIDATES - 1}", kept[-1:])


def t_check_key():
    print("\n【9】接入测试（用 /models，不花钱）")
    answer("")
    r = ai.check_key("sk-test")
    check("有效 Key → 接上了", r.get("ok") is True and r.get("msg") == "接上了", r)

    answer("", status=401)
    r = ai.check_key("sk-bad")
    check("无效 Key → Key 不对", r.get("ok") is False and "Key" in (r.get("msg") or ""), r)

    r = ai.check_key("")
    check("空 Key → 拦在前面", r.get("ok") is False, r)


def t_api_wrapper():
    print("\n【10】main.Api 那一层：默认提示词 vs 用户自定义")

    # ⚠ 用 __new__ 绕开 __init__，否则会读写用户真实的 settings.json ——
    #   测试不该碰用户的数据。
    import main                                                   # noqa: E402
    import prompt as prompt_mod                                   # noqa: E402

    api = main.Api.__new__(main.Api)
    api.settings = {"api_key": "sk-test", "system_prompt": ""}

    # ⚠ __init__ 还会设这一个（最近一轮 ③ 判定「要交给 ②」的候选）。
    #   绕开 __init__ 就得自己补上 —— 漏了的话 explain() 会在
    #   `cands = self._last_todo` 上抛 AttributeError（v0.8 真踩过一次）。
    #   explain() 里【故意】不用 getattr 兜底：正常构造出来的 Api
    #   一定有这个属性，为测试加兜底会把「真的漏了初始化」也一起盖住。
    #
    # ⚠ v0.20 起它是【按通道分槽的字典】，不再是单个值（决策 50）。
    #   两条通道会先后跑（自动那条随时可能插进来），共用一个槽的话
    #   clip 会捡到框选留下的陈旧候选，而且完全不报错。
    #   这里补的必须和 main.Api.__init__ 里那个字面量一致。
    api._last_todo = {"region": None, "clip": None}

    answer(J({"条目": [{"术语": "RAG", "解释": "x"}]}))

    r = api.explain({"原文": ["a"], "候选词": ["RAG"]})
    check("包装层透传成功", r.get("ok") is True, r)
    check("没自定义 → 用后端默认提示词",
          GOT["last"]["messages"][0]["content"] == prompt_mod.DEFAULT_PROMPT)
    check("默认提示词里明确说了候选中会有多余的",
          "一定有多余的" in prompt_mod.DEFAULT_PROMPT,
          "这句是 ① 和 ② 分工的书面约定，删了模型就会开始怀疑候选表")

    # 同理：这句是 ① 和 ② 关于「整条」的书面约定。删了它，模型就退回
    # 逐词解释 —— 而 ① 的网本来就是按单词撒的，面板上会重新冒出一堆
    # 「长 + 短期 + 记忆 + 网络」。
    check("默认提示词里明确说了「整条不拆」（词组/整句）",
          "作为【一整条】解释，不许拆成单词" in prompt_mod.DEFAULT_PROMPT,
          "删了这句，词组和整句会被重新拆成单个单词")
    check("默认提示词里也写了那条防误合并的边界",
          "碰巧挨着的两个英文词" in prompt_mod.DEFAULT_PROMPT,
          "只写「不许拆」不写边界，模型会把不相干的两个词也捏成一条")

    api.settings["system_prompt"] = "我的自定义提示词"
    api.explain({"原文": ["a"], "候选词": ["RAG"]})
    check("有自定义 → 用用户那份",
          GOT["last"]["messages"][0]["content"] == "我的自定义提示词")


# ══════════════════════════════════════════════════════════
# 【11】快捷键取词通道（clip）在 Api 这一层的两处【故意破例】
# ══════════════════════════════════════════════════════════

class _FakeHotkey:
    """顶替真的热键线程。只提供 _recognize_locked 读的那一个口子。

    ⚠ 这不是「mock 掉被测对象」——被测对象是 Api 的通道分叉，
      文字从哪来本来就不是这一段要验的（那段在 快捷键联调.py）。
    """

    def __init__(self, text=""):
        self._text = text

    def last_text(self):
        return self._text


def _mk_api(settings):
    import main                                                   # noqa: E402

    api = main.Api.__new__(main.Api)
    api.settings = settings
    api._last_todo = {"region": None, "clip": None}
    return api


def t_clip_channel():
    print("\n【11】快捷键取词通道：两处故意破例（决策 50 / 破例一、二）")
    import main                                                   # noqa: E402
    import prompt as prompt_mod                                   # noqa: E402

    # ── 堵点 6：区域守卫只对 region 生效 ──────────────────────────
    # ★ 这行是 _recognize_locked 的第一句。漏了的话 clip 通道
    #   一进门就被「还没框选区域」挡住 —— 用户明明只是在飞书里选了段字，
    #   报的却是一句驴唇不对马嘴的话，而且这条通道【一出生就是死的】。
    api = _mk_api({"api_key": "sk-test", "region": None, "kb_policy": "dedupe"})
    api.hotkey = _FakeHotkey("RAG 是先把资料查出来再回答")

    r_region = api._recognize_locked("region")
    check("region 没框选时照旧报「还没框选区域」（原行为没被改宽）",
          r_region.get("ok") is False and "还没框选" in (r_region.get("msg") or ""),
          r_region)

    r_clip = api._recognize_locked("clip")
    check("★ region 为空时 clip 照样能跑（没有报「还没框选区域」）",
          "还没框选" not in (r_clip.get("msg") or ""), r_clip)
    check("★ clip 真的跑出了结果", r_clip.get("ok") is True, r_clip)
    check("clip 结果自带 mode（前端 statLine/空态靠它分叉）",
          r_clip.get("mode") == "clip", r_clip.get("mode"))
    check("clip 不报截屏/OCR 耗时（它压根没截屏）",
          not r_clip.get("t_capture") and not r_clip.get("t_ocr"),
          f"t_capture={r_clip.get('t_capture')} t_ocr={r_clip.get('t_ocr')}")

    # ── 没抓到文字 → 必须 not-ok，不能是「空的 ok」 ────────────────
    # 空 ok 会让前端走到「没有需要问的词」那条静默分支：
    # 用户按了快捷键，面板上什么都不出现，也没有任何解释。
    api.hotkey = _FakeHotkey("   ")
    r = api._recognize_locked("clip")
    check("★ 没抓到文字时返回 not-ok（不是静悄悄的空 ok）",
          r.get("ok") is False, r)
    check("没抓到文字时说的话是「没抓到文字」，不是「还没框选」",
          "没抓到文字" in (r.get("msg") or ""), r.get("msg"))

    # ── 两份 System Prompt 各管各的 ──────────────────────────────
    api = _mk_api({"api_key": "sk-test", "system_prompt": "",
                   "system_prompt_any": ""})
    check("clip 没自定义 → 用后端默认的【中英不限】那份",
          api._system_prompt("clip") == prompt_mod.DEFAULT_PROMPT_ANY)
    check("region 没自定义 → 还是原来那份【只解释英文】的",
          api._system_prompt("region") == prompt_mod.DEFAULT_PROMPT)
    check("★ 两份默认提示词确实是两份（不是同一个字面量）",
          prompt_mod.DEFAULT_PROMPT_ANY != prompt_mod.DEFAULT_PROMPT)

    api.settings["system_prompt_any"] = "我的中文提示词"
    check("clip 有自定义 → 用用户那份",
          api._system_prompt("clip") == "我的中文提示词")
    check("★ 改了 clip 那份【不影响】region 那份",
          api._system_prompt("region") == prompt_mod.DEFAULT_PROMPT,
          "两份提示词是独立的，串了的话自动通道会被悄悄带跑偏")

    # ── 堵点 3：_last_todo 按通道分槽 ────────────────────────────
    # 共用一个槽的话，clip 会捡到上一轮框选留下的陈旧候选，
    # **而且完全不报错** —— 钱花了，解释挂在不相干的词上。
    #
    # ⚠ 判据打在【候选的拼法】上，不是 user message 的候选段 ——
    #   那一段来自前端传上来的 payload（`ai.build_messages` 读的就是 payload），
    #   `_last_todo` 根本不参与，打在它上面是【恒绿】的假断言。
    #   `_last_todo` 真正的去处是 `attach_keys()`：模型返回的术语靠
    #   哪一批候选认领，认领上了 `term` 就换成**候选那个拼法**
    #   （kb.py:378，注释写明「用户框的是 RAG，面板上写着 Rag 会让人愣一下」）。
    #   所以两槽给同一个 key、不同的拼法，拼法就是判据。
    #   （第一版打在候选段上、第二版打在 key 上，两版都红 —— 两版都没打对地方。）
    api = _mk_api({"api_key": "sk-test", "kb_policy": "dedupe"})
    api._last_todo = {
        "region": [{"term": "WIDGET（框选那槽）", "key": "widget"}],
        "clip":   [{"term": "Widget（取词那槽）", "key": "widget"}],
    }

    answer(J({"条目": [{"术语": "widget", "解释": "x"}]}))
    r = api.explain({"原文": ["a"], "候选词": ["widget"]}, "clip")
    t = (r.get("entries") or [{}])[0].get("term")
    check("★ clip 认领的是【自己那槽】的候选", t == "Widget（取词那槽）", repr(t))
    check("★ clip 没有捡到框选留下的那槽（钱花了，解释挂在不相干的词上）",
          t != "WIDGET（框选那槽）", repr(t))

    r = api.explain({"原文": ["a"], "候选词": ["widget"]}, "region")
    t = (r.get("entries") or [{}])[0].get("term")
    check("反向：region 认领的也是自己那槽", t == "WIDGET（框选那槽）", repr(t))
    check("反向：region 没用到 clip 那槽", t != "Widget（取词那槽）", repr(t))

    # 分槽的另一个可观察面：region 那道空候选早退，只看【region 那槽】。
    # 共用一个槽的话，clip 跑过一轮留下的候选会把 region 的早退「救活」——
    # 本该零成本的一次，变成白花一次钱。
    api._last_todo = {"region": None, "clip": [{"term": "X", "key": "x"}]}
    GOT["hits"] = 0
    r = api.explain({"原文": ["a"], "候选词": []}, "region")
    check("★ region 空候选没有借 clip 那槽的候选「复活」",
          GOT["hits"] == 0 and r.get("skipped") == "没有需要问模型的词",
          f"假的模型被调了 {GOT['hits']} 次；返回 {r}")

    # ── 破例二：空候选早退，只对 region 生效 ─────────────────────
    # ★ 这是本轮最要紧的一条。纯中文时 ①（只认英文字母的正则）
    #   一个候选都给不出来，术语只能由 ② 从原文里挑 ——
    #   在这里早退 = 用户按了 Ctrl+E，面板上什么都不出现。
    answer(J({"条目": [{"术语": "抓手", "解释": "切入角度。"}]}))

    GOT["hits"] = 0
    r = api.explain({"原文": ["对齐颗粒度"], "候选词": []}, "clip")
    check("★ clip 候选为空【照样调模型】（没有被早退挡住）",
          GOT["hits"] == 1, f"假服务器收到 {GOT['hits']} 次请求，应该是 1")
    check("★ 中文条目从 clip 通道回来了",
          r.get("ok") and any(e.get("term") == "抓手" for e in r["entries"]), r)

    GOT["hits"] = 0
    api._last_todo = {"region": None, "clip": None}
    r = api.explain({"原文": ["a"], "候选词": []}, "region")
    check("region 候选为空【仍然不调模型】（那道闸门没被拆掉）",
          GOT["hits"] == 0, f"假服务器收到 {GOT['hits']} 次请求，应该是 0")
    check("region 空候选照旧走「没有需要问模型的词」那条",
          r.get("ok") is True and r.get("skipped") == "没有需要问模型的词", r)


# ══════════════════════════════════════════════════════════

def main():
    # ★ 先验尺子：本文件的 `check()` 是它全部 78 项断言的唯一出口，
    #   它坏成「无条件记通过」的话 78 项一起变绿，项数对账还照样通过。
    # ⚠️ 摆在起假服务器【之前】，也摆在任何 try 之外 ——
    #   尺子坏了就别开服务器了，跑出来的数没有意义。
    if not 尺子.gate(check, *尺子.list_snapshot(PASS, FAIL), label="AI接入联调"):
        return 2

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    ai.BASE_URL = f"http://127.0.0.1:{port}"       # ★ 只改这一处，就换掉了对面
    print(f"假 DeepSeek 起在 127.0.0.1:{port}　（不联网、不花钱）")

    try:
        for fn in (t_happy, t_request_shape, t_tolerate, t_empty_and_bad,
                   t_http, t_timeout, t_no_key, t_truncate, t_check_key,
                   t_api_wrapper, t_clip_channel):
            fn()
    finally:
        srv.shutdown()

    print("\n" + "═" * 52)
    print(f"通过 {len(PASS)} 项　失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败明细：")
        for name, detail in FAIL:
            print(f"  · {name}　→ {detail}")
        return 1

    print("""
未覆盖（要等真 Key 才能验）：
  · 真实 DeepSeek 对那份 System Prompt 的服从度 —— 到底筛得准不准
  · 真实的限流阈值、余额扣费
  · 真实网络下的延迟（这里全是毫秒级回环，感觉不到「等下十几秒」是什么体验）
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
