# -*- coding: utf-8 -*-
"""③ 知识库联调 —— 纯函数 + 临时库，不开窗口、不联网、不花钱。

跑起来：
    python "Desktop\\问了吗\\冒烟测试\\知识库联调.py"

⚠ **全程用临时目录里的库**（见 fresh_db），绝不碰 `app\\kb.db`。
  那个库里是用户自己的东西。

本文件盯的是三件事：
  1. canon() 的归一化边界 —— 它现在是知识库主键，算错就是钱和正确性
  2. 三个策略 × 命中/未命中 的全部组合，以及排除名单的优先级
  3. 「前后端归一化只能有一份」这条规矩有没有被违反
"""
import os
import re
import sys
import sqlite3
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

# ⚠ 本文件第一版就是栽在这儿的：`▸` (U+25B8) GBK 编不出来，
#   输出被重定向时 Python 默认走 GBK，print 当场抛 UnicodeEncodeError。
#   和 app/main.py 开头那段是同一个坑 —— 诊断代码得比被测代码更不容易出错。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import kb                                                        # noqa: E402
import pipeline                                                  # noqa: E402

# 「出口自检」那把共用的尺子（见 `尺子.py`）。
# ⚠ HERE 得自己插进 sys.path —— 本文件被 `importlib` 装载时
#   （`验工具自检.py` 就是这么干的）sys.path[0] 不一定是这儿。
sys.path.insert(0, HERE)
import 尺子                                                       # noqa: E402

PASS, FAIL = [], []
_TMPDIRS = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [通过] {name}")
    else:
        FAIL.append((name, detail or "断言为假"))
        print(f"  [失败] {name}　→ {detail}")


def fresh_db():
    """换一个全新的临时库。每次调用都连同连接一起重置。"""
    kb.close()
    d = tempfile.mkdtemp(prefix="wenlema_kb_")
    _TMPDIRS.append(d)
    kb.kb_path = lambda: os.path.join(d, "kb.db")
    return d


def cand(*terms):
    """按 ① 的方式造候选（必须经过 prefilter，别手搓 —— 手搓的测不出 ① 的变化）。"""
    return pipeline.prefilter([f"笔记里提到 {' 和 '.join(terms)} 这些概念"])


# ══════════════════════════════════════════════════════════
# 1. canon() —— 归一化的边界
# ══════════════════════════════════════════════════════════

def t_canon():
    print("\n▸ canon() 归一化边界")
    fresh_db()

    same = [
        (["RAG", "Rag", "rag"], "rag"),
        (["Top-K", "TopK", "top_k"], "topk"),
        (["CI/CD", "CICD"], "cicd"),
        (["GPT-4", "GPT4"], "gpt4"),
        (["o1-mini", "o1mini"], "o1mini"),
        (["multi-agent", "Multi-Agent"], "multiagent"),
        (["RAG（检索增强生成）", "RAG (Retrieval-Augmented Generation)", "rag"], "rag"),
    ]
    for group, want in same:
        got = {kb.canon(x) for x in group}
        check(f"这些写法归一到同一个 key：{group}", got == {want}, f"得到 {got}，期望 {{{want!r}}}")

    # ⚠ C++ 和 C# 必须【分开】。按「只留字母数字」算，两个都是 "c"，
    #   两种语言撞成一个 key —— 所以 canon 先把符号转成词再归一。
    check("C++ / C# / C 不会撞成一个 key",
          len({kb.canon("C++"), kb.canon("C#"), kb.canon("C")}) == 3,
          f"{kb.canon('C++')} / {kb.canon('C#')} / {kb.canon('C')}")
    check("C++ → cplusplus", kb.canon("C++") == "cplusplus", kb.canon("C++"))
    check("C# → csharp", kb.canon("C#") == "csharp", kb.canon("C#"))

    # ⚠ 这个撞车是【已知且接受】的，不是 bug。
    #   写成断言，是为了将来有人改归一化规则时，能立刻在这里看到影响面。
    check("已知撞车：A/B 与英文词 ab 同 key（接受，别改）",
          kb.canon("A/B") == kb.canon("ab") == "ab")

    # ⚠ v0.20 改了这条的期望值（决策 51）。原来是 `canon("（）") == "（）"`。
    #
    #   那条旧期望当时看着是「防主键污染」，其实 **`"（）"` 本身就是污染** ——
    #   它是非空的，record_entries 的 `if not k` 拦不住，会当成一条正经主键入库。
    #   真正防污染的做法是让它归一到空串，由 `if not k` 挡在库外。
    #
    #   改这条的同时 canon 也修了中文的三个破口（括号前缀 `【`、标点不归一、
    #   全角字母），见 app/kb.py 里 canon() 的说明。纯 ASCII 路径逐条等价。
    check("空串 / 纯标点都归一到空 key（由 record_entries 的 if not k 挡在库外）",
          kb.canon("") == "" and kb.canon("（）") == "",
          f"{kb.canon('')!r} / {kb.canon('（）')!r}")

    # ── v0.20：中文主键归一（决策 51）──
    #   这一组钉的是「同一个中文词换个写法必须还是同一个 key」——
    #   不成立的话，用户第二次选中同一个词查不到知识库，
    #   会**重新问一次模型、重新花一次钱**，而且完全不报错。
    check("中文：各种写法的「抓手」归一成同一个 key",
          kb.canon("抓手") == kb.canon("【抓手】") == kb.canon("「抓手」")
          == kb.canon("抓手。") == kb.canon(" 抓手") == kb.canon("抓手（黑话）")
          == "抓手",
          f"{kb.canon('【抓手】')!r} / {kb.canon('抓手。')!r} / {kb.canon(' 抓手')!r}")
    check("中文：括号前缀不再自成一个主键（曾经是 '【'）",
          kb.canon("【抓手】") != "【" and kb.canon("【闭环】") != kb.canon("【抓手】"),
          f"{kb.canon('【抓手】')!r} / {kb.canon('【闭环】')!r}")
    check("全角字母归一（ＲＡＧ → rag），不是被删成空 key",
          kb.canon("ＲＡＧ") == "rag", kb.canon("ＲＡＧ"))


# ══════════════════════════════════════════════════════════
# 2. 三个策略 × 命中/未命中
# ══════════════════════════════════════════════════════════

def t_policies():
    print("\n▸ 三个策略 × 库里命中 / 没命中")
    fresh_db()

    kb.record_entries([{"term": "RAG", "key": "rag", "explanation": "检索增强生成。"}],
                      "dedupe")
    c = cand("RAG", "Chunking")

    r = kb.filter_candidates(c, "dedupe")
    check("dedupe：命中的进 kb_hits，不进 ai_todo",
          [h["term"] for h in r["kb_hits"]] == ["RAG"]
          and [t["term"] for t in r["ai_todo"]] == ["Chunking"],
          f"hits={[h['term'] for h in r['kb_hits']]} todo={[t['term'] for t in r['ai_todo']]}")
    check("dedupe：命中的解释是库里存的那条",
          r["kb_hits"][0]["explanation"] == "检索增强生成。")

    r = kb.filter_candidates(c, "repeat")
    check("repeat：全部交给 ②，库里的解释这次不用",
          not r["kb_hits"] and sorted(t["term"] for t in r["ai_todo"]) == ["Chunking", "RAG"],
          f"hits={len(r['kb_hits'])} todo={[t['term'] for t in r['ai_todo']]}")

    r = kb.filter_candidates(c, "whitelist")
    check("whitelist：库外的整个不出现，且不算「挡住」也不算「要问」",
          [h["term"] for h in r["kb_hits"]] == ["RAG"]
          and not r["ai_todo"]
          and [b["why"] for b in r["blocked"]] == ["not_in_kb"],
          f"todo={r['ai_todo']} blocked={r['blocked']}")


# ══════════════════════════════════════════════════════════
# 3. 排除名单
# ══════════════════════════════════════════════════════════

def t_excluded():
    print("\n▸ 排除名单（决策 21）")
    fresh_db()

    kb.record_entries([{"term": "RAG", "key": "rag", "explanation": "x"},
                       {"term": "KPI", "key": "kpi", "explanation": "y"}], "dedupe")
    kb.exclude("KPI")
    c = cand("RAG", "KPI")

    for policy in ("dedupe", "repeat", "whitelist"):
        r = kb.filter_candidates(c, policy)
        names = ([h["term"] for h in r["kb_hits"]]
                 + [t["term"] for t in r["ai_todo"]]
                 + [b["term"] for b in r["blocked"] if b["why"] != "excluded"])
        check(f"{policy}：被排除的词在哪儿都不出现", "KPI" not in names, str(names))
        check(f"{policy}：被排除的词明确出现在 blocked 里",
              any(b["why"] == "excluded" and b["term"] == "KPI" for b in r["blocked"]))

    # ⚠ 顺序：排除判定必须在知识库命中【之前】。
    #   反过来的话，一个用户明确排除掉的词会从库里被贴出来。
    r = kb.filter_candidates(c, "dedupe")
    check("排除优先于知识库命中（KPI 在库里，但被排除，所以不出现）",
          "KPI" not in [h["term"] for h in r["kb_hits"]])

    # 碎片兜底：① 会把 Top-K 拆成 Top 和 Top-K，排除了 Top-K 之后 Top 也该消失
    kb.exclude("Top-K")
    r = kb.filter_candidates(cand("Top", "Top-K"), "dedupe")
    left = [t["term"] for t in r["ai_todo"]] + [h["term"] for h in r["kb_hits"]]
    check("排除 Top-K 之后，碎片 Top 也一起被挡住", "Top" not in left, str(left))

    # 但不能无限放宽
    kb.exclude("rag")
    r = kb.filter_candidates(cand("RAGFlow"), "dedupe")
    left = [t["term"] for t in r["ai_todo"]] + [b["term"] for b in r["blocked"]]
    check("排除 rag 不连带封掉 ragflow（前缀放宽要有度）",
          "RAGFlow" in left, str(left))

    kb.unexclude("kpi")
    check("unexclude 能把词放回来",
          "KPI" in [h["term"] for h in kb.filter_candidates(c, "dedupe")["kb_hits"]])
    check("list_excluded 列得出来", len(kb.list_excluded()) >= 1)


# ══════════════════════════════════════════════════════════
# 4. ① → ③ 的接缝
# ══════════════════════════════════════════════════════════

def t_seam():
    print("\n▸ ① → ③ 的接缝")
    fresh_db()

    # ① 按 w.lower() 去重，不做 canon —— 所以这两个会【各自】成为一条候选
    c = pipeline.prefilter(["Top-K 很重要，TopK 也很重要"])
    terms = [x["term"] for x in c]
    check("① 不合并同义写法（这是故意的，合并是 ③ 的活）",
          terms == ["Top-K", "TopK"], str(terms))

    # ③ 必须把它俩合起来，否则库里命中两次、贴出两张一模一样的卡
    kb.record_entries([{"term": "Top-K", "key": "topk", "explanation": "取前 K 个。"}], "dedupe")
    r = kb.filter_candidates(c, "dedupe")
    check("③ 按 key 二次合并，同一个词只出一条",
          len(r["kb_hits"]) == 1, f"{[h['term'] for h in r['kb_hits']]}")
    check("③ 合并后保留【首次出现】的那个写法（和 ① 的取义一致）",
          r["kb_hits"][0]["term"] == "Top-K", r["kb_hits"][0]["term"])

    # 候选必须自带 key（前端靠它做字符串比较，不再自己算归一化）
    check("① 的候选自带 key 字段", all("key" in x for x in c),
          str(c[:1]))
    check("① 认得 C++ / C# / T+1",
          [x["term"] for x in pipeline.prefilter(["用 C++ 和 C# 写，T+1 结算"])]
          == ["C++", "C#", "T+1"])


# ══════════════════════════════════════════════════════════
# 5. attach_keys —— 把模型的回答认领回候选
# ══════════════════════════════════════════════════════════

def t_attach():
    print("\n▸ attach_keys：模型拼写和候选对不上时")
    fresh_db()

    cands = [{"term": "Chunking", "key": "chunking"},
             {"term": "RAG", "key": "rag"},
             {"term": "Prompt", "key": "prompt"}]

    out = kb.attach_keys([
        {"term": "Chunk", "explanation": "切块。"},          # 缩写形式
        {"term": "RAG (Retrieval-Augmented Generation)", "explanation": "检索增强。"},
        {"term": "Prompt", "explanation": "提示词。"},
    ], cands)
    check("模型把 Chunking 写成 Chunk，仍能认领回原候选",
          out[0]["key"] == "chunking", out[0])
    check("模型带上括号解释也能认领（canon 在中文字符/括号处截断）",
          out[1]["key"] == "rag", out[1])
    check("展示用 term 换成【候选】的拼法（用户框的是 RAG，不该显示 Rag）",
          out[1]["term"] == "RAG", out[1]["term"])

    # ⚠ 模型的拼写每轮都可能变，拿它当主键的话同一个词永远查不到、永远重问。
    #   这条断言就是钉死这件事的。
    kb.record_entries(out, "dedupe")
    r = kb.filter_candidates([{"term": "Chunking", "key": "chunking", "line": "", "at": 0}],
                             "dedupe")
    check("★ 认领回候选之后，下一轮能命中知识库（省钱那条链闭合）",
          len(r["kb_hits"]) == 1 and r["kb_hits"][0]["key"] == "chunking",
          str(r["kb_hits"]))

    out = kb.attach_keys([{"term": "VectorDB", "explanation": "向量库。"}], cands)
    check("候选里没有的词，用它自己的 canon 当 key（不算错）",
          out[0]["key"] == "vectordb", out[0])


# ══════════════════════════════════════════════════════════
# 5b. 词组 / 整句 —— 作为【一整条】解释（v0.10）
# ══════════════════════════════════════════════════════════

def t_phrase():
    print("\n▸ attach_keys：词组与整句（整条解释，不许拆成单词）")
    fresh_db()

    pool = [{"term": t, "key": kb.canon(t)} for t in
            ["Long", "Short-Term", "Memory", "Prompt", "Engineering",
             "vector", "RAG", "Storage", "layer"]]

    out = kb.attach_keys([
        {"term": "Long Short-Term Memory", "explanation": "长短期记忆网络。"},
        {"term": "Prompt Engineering",     "explanation": "提示词工程。"},
        {"term": "Vector DB",              "explanation": "向量数据库。"},
        {"term": "Storage layer",          "explanation": "存储层。"},
    ], pool)
    by_term = {e["term"]: e for e in out}

    # ── 多词术语不许被前缀兜底认领到【单个词】候选上 ──
    #
    # 前缀兜底（_TERM_SLACK=6）本是为 `Chunk` → `Chunking` 写的，
    # 但 `Vector DB` 的 canon `vectordb`(8) 对上候选 `vector`(6)
    # 长度差 2、前缀也成立 —— 于是整整一条词组被认领成单词 `vector`。
    # 后果不止是「显示成一个单词」：展示 term 被改写成 vector，
    # 解释挂到 vector 的 key 上、再以 vector 的身份写进库 —— 库里存错。
    check("★ 多词术语不被前缀兜底认领成单词（Vector DB 不是 vector）",
          by_term["Vector DB"]["key"] == "vectordb",
          by_term["Vector DB"])
    check("★ 认领之后展示的 term 也没被改写成单词",
          by_term["Vector DB"]["term"] == "Vector DB",
          by_term["Vector DB"]["term"])

    # ── covers：这一整条把哪些候选收进去了 ──
    check("★ 整条 cover 住它的组成词（Long Short-Term Memory → 三个词）",
          sorted(by_term["Long Short-Term Memory"]["covers"])
          == ["long", "memory", "shortterm"],
          by_term["Long Short-Term Memory"]["covers"])
    check("空格分出来的词组同样 cover 住它的词（Prompt Engineering）",
          sorted(by_term["Prompt Engineering"]["covers"]) == ["engineering", "prompt"],
          by_term["Prompt Engineering"]["covers"])
    check("covers 里不含它自己",
          by_term["Storage layer"]["key"] not in by_term["Storage layer"]["covers"],
          by_term["Storage layer"]["covers"])

    # ⚠ 子串匹配是这条规则最容易写错的地方：`rag` 是 `sto**rag**e` 的子串、
    #   `age` 也是。按子串算的话，一整条 `Storage layer` 会把毫不相干的 RAG
    #   吞掉 —— 用户那个词再也不出现在面板上，而且完全不报错。
    check("★ 反例：Storage layer 不许把 RAG 吞进去（只按整词，不按子串）",
          "rag" not in by_term["Storage layer"]["covers"],
          by_term["Storage layer"]["covers"])

    # ── 认领走的候选不算覆盖 ──
    # 那条候选自己的卡片要负责显示那条解释，收掉就把解释弄丢了。
    out2 = kb.attach_keys([
        {"term": "RAG", "explanation": "检索增强。"},
        {"term": "RAG pipeline", "explanation": "检索增强的整条链路。"},
    ], pool)
    check("已被别的条认领的候选，不算被这条 cover（否则那张卡片的解释会丢）",
          "rag" not in out2[1]["covers"], out2[1]["covers"])
    check("认领照旧生效（RAG 还是认到了候选上）",
          out2[0]["key"] == "rag" and out2[0]["term"] == "RAG", out2[0])


def t_phrase_excluded():
    print("\n▸ 排除名单对【整条】也要生效（v0.10）")
    fresh_db()

    # ⚠ 排除判定以前只是 filter_candidates 里的一个闭包，只有 ① 的候选表用得上。
    #   词组/整句是 ② 从原文里拎出来的整条 —— 根本不在候选表里，
    #   于是右键「不再解释该词」对它们静默失效：toast 说「已不再解释」，
    #   下一轮那个整条照常回来。
    long_key = kb.canon("Long Short-Term Memory")
    kb.exclude(long_key)

    r = kb.filter_candidates(
        [{"term": "Long", "key": "long", "line": "", "at": 0}], "dedupe")
    check("候选表那边照旧（Long 本身没被排除）", len(r["ai_todo"]) == 1, r["ai_todo"])

    kept = kb.drop_excluded([
        {"term": "Long Short-Term Memory", "key": long_key, "explanation": "x"},
        {"term": "RAG", "key": "rag", "explanation": "y"},
    ])
    check("★ 被排除的整条在 ② 之后被挡掉",
          [e["term"] for e in kept] == ["RAG"], [e["term"] for e in kept])

    kb.unexclude(long_key)
    kept = kb.drop_excluded([
        {"term": "Long Short-Term Memory", "key": long_key, "explanation": "x"}])
    check("撤销排除之后又回来了（这个操作是可逆的）", len(kept) == 1, kept)


# ══════════════════════════════════════════════════════════
# 5c. 前缀认领对中文要收手（v0.20 · 决策 51 的第三处）
# ══════════════════════════════════════════════════════════

def t_claim_cjk():
    print("\n▸ _claim 的前缀认领只对英文生效（中文不许被短词认领走）")
    fresh_db()

    # ⚠ 这段兜底是为【英文构词】写的：`Chunk` → `Chunking`（长度差 3）。
    #   中文没有这种构词法，而 _TERM_SLACK 的「差 6」对 2~4 个字的中文词
    #   等于放行一切。失控起来和 `Vector DB` 被认领成 `vector` 是同一个形状：
    #   展示 term 被改写成短词、解释挂到短词的 key 上、再以短词的身份写进库 ——
    #   下一轮短词命中知识库，永远贴出这条长词的解释。而且全程不报错。
    pool = [{"term": "闭环思维", "key": kb.canon("闭环思维")},
            {"term": "对齐颗粒度", "key": kb.canon("对齐颗粒度")}]

    out = kb.attach_keys([{"term": "闭环思维模型", "explanation": "指…"}], pool)
    check("★ 中文长词【不】被前缀更短的候选认领走（英文那条兜底不许外溢到中文）",
          out[0]["term"] == "闭环思维模型" and out[0]["key"] != kb.canon("闭环思维"),
          f'term={out[0]["term"]!r}　key={out[0]["key"]!r}'
          f'（认领成 {kb.canon("闭环思维")!r} 就是把 5 个字的词改写成 4 个字的）')

    out = kb.attach_keys([{"term": "对齐颗粒度模型", "explanation": "指…"}], pool)
    check("★ 同上（换个词再来一次，防的是「碰巧这一个词是对的」）",
          out[0]["term"] == "对齐颗粒度模型", out[0]["term"])

    # ★ 反向：英文那条兜底【必须】原样活着。只钉中文不钉英文的话，
    #   把整个兜底删掉也能全绿 —— 那就把 `Chunk` → `Chunking` 一起弄丢了。
    out = kb.attach_keys([{"term": "Chunking", "explanation": "切块。"}],
                         [{"term": "Chunk", "key": "chunk"}])
    check("★ 反向：英文的拼写兜底照旧活着（Chunking 仍认领回 Chunk）",
          out[0]["term"] == "Chunk" and out[0]["key"] == "chunk", out[0])

    # ★ 反向之二：完全相同的词当然还认领得上（别把精确匹配也一起拦了）
    pool3 = [{"term": "抓手", "key": kb.canon("抓手")}]
    out = kb.attach_keys([{"term": "抓手", "explanation": "指…"}], pool3)
    check("★ 反向：精确同名的中文候选照旧认领（拦的只是「前缀兜底」）",
          out[0]["key"] == kb.canon("抓手"), out[0]["key"])


# ══════════════════════════════════════════════════════════
# 6. 写库 / 自动收录 / 收纳历史
# ══════════════════════════════════════════════════════════

def t_write():
    print("\n▸ 自动收录、流水去重、收纳历史")
    fresh_db()

    e = [{"term": "RAG", "key": "rag", "explanation": "版本一"}]

    # ★ 用户 2026-09-14 拍板：AI 答完就自动进库，不等手动收纳。
    #   否则「不重复解释」的省钱承诺要等用户手动收一次才生效。
    kb.record_entries(e, "dedupe")
    check("★ dedupe 模式下 AI 答完自动进库（不用等收纳历史）",
          kb.stats()["terms"] == 1, str(kb.stats()))

    fresh_db()
    kb.record_entries(e, "repeat")
    check("repeat 模式【不】自动覆盖库里的旧解释",
          kb.stats()["terms"] == 0, str(kb.stats()))
    check("repeat 模式仍然记流水（收纳历史的原料）",
          kb.stats()["history"] == 1, str(kb.stats()))

    # 收纳历史把 repeat 那版收编
    r = kb.harvest()
    check("收纳历史：把流水里最新的那条收进库",
          r["added"] == 1 and kb.stats()["terms"] == 1, str(r))

    # 重复写同一个解释不产生新流水
    fresh_db()
    for _ in range(5):
        kb.record_entries(e, "dedupe")
    check("同一个解释重复写 5 次，流水只留 1 条（砍量靠这招）",
          kb.stats()["history"] == 1, str(kb.stats()))

    kb.record_entries([{"term": "RAG", "key": "rag", "explanation": "版本二"}], "dedupe")
    check("解释变了才写新流水", kb.stats()["history"] == 2, str(kb.stats()))
    check("第一条不被覆盖（首次写进来的赢）",
          kb.filter_candidates([{"term": "RAG", "key": "rag"}], "dedupe")
            ["kb_hits"][0]["explanation"] == "版本一")

    kb.record_entries([{"term": "RAG", "key": "rag", "explanation": "版本三"}], "dedupe")
    r = kb.harvest()
    check("收纳历史取【最新】那条，不是最旧的",
          kb.filter_candidates([{"term": "RAG", "key": "rag"}], "dedupe")
            ["kb_hits"][0]["explanation"] == "版本三", str(r))

    # 收纳历史不能冲掉将来从 md 导进来的手工条目
    with sqlite3.connect(kb.kb_path()) as c:
        c.execute("UPDATE terms SET source='import' WHERE key='rag'")
    kb.record_entries([{"term": "RAG", "key": "rag", "explanation": "版本四"}], "repeat")
    kb.harvest()
    check("收纳历史不覆盖 source='import' 的手工条目（给下一版的导入留路）",
          kb.filter_candidates([{"term": "RAG", "key": "rag"}], "dedupe")
            ["kb_hits"][0]["explanation"] == "版本三")

    # 被排除的词不进库
    fresh_db()
    kb.exclude("KPI")
    kb.record_entries([{"term": "KPI", "key": "kpi", "explanation": "z"}], "repeat")
    r = kb.harvest()
    check("收纳历史跳过被排除的词（否则库里堆一批永远看不见的）",
          kb.stats()["terms"] == 0 and r["skipped"] == 1, str(r))

    # 超期流水自动清
    fresh_db()
    kb.record_entries(e, "dedupe")
    old = 91 * 86400
    with sqlite3.connect(kb.kb_path()) as c:
        c.execute("UPDATE history SET at = at - ?", (old,))
    kb.record_entries([{"term": "X", "key": "x", "explanation": "n"}], "dedupe")
    check("超过 90 天的流水会被清掉", kb.stats()["history"] == 1, str(kb.stats()))

    fresh_db()
    n = kb.touch_used(["a", "b"])
    check("touch_used 对不存在的 key 不炸", n == 2, str(n))


# ══════════════════════════════════════════════════════════
# 7. 导出 md
# ══════════════════════════════════════════════════════════

def split_md_row(ln):
    """把一行 md 表格拆成格子，处理转义。

    ⚠ 首尾各有一个 `|`，直接切会多出两个【空】格子 ——
      第一版就是栽在这儿的，四条断言一起红，看起来像导出坏了，
      其实导出是对的、解析器是错的。"""
    cells, cur, i = [], "", 0
    body = ln.rstrip("\n")
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            cur += body[i + 1]; i += 2; continue
        if ch == "|":
            cells.append(cur); cur = ""; i += 1; continue
        cur += ch; i += 1
    cells.append(cur)
    return [c.strip() for c in cells[1:-1]]      # 掐掉首尾竖线带来的空格子


def parse_md(path):
    """把自己导出的 md 读回来。下一版的「导入」就靠这个形状 ——
    现在先钉住，免得到时候发现导出的格式根本读不回来。"""
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if not ln.startswith("| ") or ln.startswith("| ---"):
                continue
            cells = split_md_row(ln)
            if len(cells) == 2 and cells[0] != "术语":
                out.append(tuple(cells))
    return out


def cols_of(ln):
    """一行 md 表格有几列。【数未转义的竖线】——
    直接 ln.count('|') 会把解释里转义出来的 `\\|` 也数进去。"""
    n, i = 0, 0
    while i < len(ln):
        if ln[i] == "\\":
            i += 2; continue
        if ln[i] == "|":
            n += 1
        i += 1
    return n - 1


def t_export():
    print("\n▸ 导出 md")
    d = fresh_db()

    kb.record_entries([
        {"term": "RAG", "key": "rag", "explanation": "含竖线 | 和反斜杠 \\ 的解释"},
        {"term": "Chunking", "key": "chunking", "explanation": "多行\n解释要压成一行"},
    ], "dedupe")

    r = kb.export_md("all", d)
    check("导出成功且落了文件", r["ok"] and os.path.exists(r["path"]), str(r))

    raw = open(r["path"], encoding="utf-8").read()
    check("文件是 UTF-8 无 BOM（Obsidian / 飞书都吃这个）",
          not raw.startswith("﻿"))
    check("文件头写明了「这是快照，改了不会回库」",
          "快照" in raw and "不会回到" in raw)
    body = [ln for ln in raw.splitlines() if ln.startswith("| ") and not ln.startswith("| ---")]
    check("严格两栏（多一列下一版的导入就没法按列对齐）",
          all(cols_of(ln) == 2 for ln in body), [cols_of(ln) for ln in body])
    check("竖线被转义了（没转义就会把列撑破）",
          "\\|" in raw, [ln for ln in body if "RAG" in ln])

    back = parse_md(r["path"])
    got = dict(back)
    check("含竖线的解释能被读回来（转义正确）",
          got.get("RAG") == "含竖线 | 和反斜杠 \\ 的解释", repr(got.get("RAG")))
    check("解释里的换行被压成空格（否则表格会塌）",
          got.get("Chunking") == "多行 解释要压成一行", repr(got.get("Chunking")))
    check("来回一圈，条目数不变", len(back) == 2, str(back))

    r2 = kb.export_md("all", d)
    check("同一天导第二次不静默覆盖，另存一个文件名",
          r2["path"] != r["path"] and os.path.exists(r2["path"]), r2["path"])

    r3 = kb.export_md("today", d)
    check("今日范围能导出", r3["ok"] and r3["count"] == 2, str(r3))


# ══════════════════════════════════════════════════════════
# 8. 前后端归一化只能有一份
# ══════════════════════════════════════════════════════════

def t_single_source():
    print("\n▸ 「归一化只该有后端一份」有没有被违反")
    appjs = open(os.path.join(APP, "web", "app.js"), encoding="utf-8").read()

    # 去掉注释再找 —— 注释里提到旧写法是允许的（那里正是在解释为什么删掉它）
    code = re.sub(r"/\*.*?\*/", "", appjs, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)

    check("app.js 里没有第二份归一化实现",
          "toLowerCase" not in code and "a-z0-9" not in code,
          "前端又自己算 key 了 —— 这个 key 现在是知识库主键，两份实现一定会分叉")
    check("app.js 按 key 做字符串相等比较（后端给好的）",
          ".key" in code and "kb_hits" in code and "ai_todo" in code)

    r1 = open(os.path.join(APP, "prompt.py"), encoding="utf-8").read()
    check("默认提示词仍然只在后端一份", "DEFAULT_PROMPT" in r1)


def main():
    print("═" * 52)
    print("③ 知识库联调")
    print("═" * 52)

    # ★ 先验尺子。本文件的 `check()` 是它全部 80 项断言的唯一出口 ——
    #   它要是坏成了「无条件记通过」，80 项会一起变绿，**而且项数对账
    #   照样通过**（实到 80 = 期望 80）。这是本项目唯一一种
    #   「没有任何别的机制兜得住」的坏法。
    # ⚠️ 摆在 try 之外 —— 守门的东西不能和被守的代码套在同一个 try 里。
    if not 尺子.gate(check, *尺子.list_snapshot(PASS, FAIL), label="知识库联调"):
        return 2

    try:
        for fn in (t_canon, t_policies, t_excluded, t_seam, t_attach,
                   t_phrase, t_phrase_excluded, t_claim_cjk,
                   t_write, t_export, t_single_source):
            fn()
    finally:
        kb.close()
        for d in _TMPDIRS:
            shutil.rmtree(d, ignore_errors=True)

    print("\n" + "═" * 52)
    print(f"通过 {len(PASS)} 项　失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败明细：")
        for name, detail in FAIL:
            print(f"  · {name}　→ {detail}")
        return 1

    print("""
⚠ 本文件【不碰】app\\kb.db，全程用临时库。用户真实的库是另一件事。

未覆盖（要等端到端脚本或真机）：
  · 白名单模式 + 空库 的那句硬拦截（在 main.Api 里，要起窗口才测得到）
  · 右键菜单、撤销提示、导出目录选择框 这些界面行为
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
