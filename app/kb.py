# -*- coding: utf-8 -*-
"""③ 知识库过滤 —— 三段式管线的第三段，全程本地，绝不为此调模型（硬约束 3）。

三张表
    terms     知识库本尊。key = canon() 归一化后的词，解释存这里
    history   解释流水。每答一条写一行，供「收纳历史」挑选
    excluded  排除名单（决策 21）。被排除的词永远不出现、也永远不发给 AI

⚠ **history 表里【不要】存原文行或上下文**。
  存了这个库就变成用户笔记的副本：体积爆炸，隐私风险翻倍。
  它只服务于「收纳历史」这一件事，不是审计日志。后来的人别顺手加字段。

⚠ 三条路径必须走同一个 canon()：库的写入、库的查询、① 出来的候选。
  差一个字符，命中率就凭空掉一半，而且不报错 —— 只是变贵。
"""
import os
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

# ⚠ 数据库路径只在这一个函数里出现。
#   将来要挪到 %APPDATA%（打包到 Program Files 下时程序目录是只读的，建不了库），
#   只改这里一处。settings.json 也有同样的隐患，等它一起收。
#
# ⚠ WENLEMA_KB 是【留给联调脚本的接口】，和 ai.py 里那个 WENLEMA_BASE_URL 一个性质，
#   不是给用户的功能开关 —— 正常启动时它不存在，行为与写死路径完全一致。
#   有它，端到端脚本才能在【完全不碰用户真实库】的前提下跑完整条链路：
#   用户的 kb.db 是攒出来的，用「备份→跑→还原」的方式围着它转，
#   脚本一旦中途被强杀，用户看到的就是一个空库。
def kb_path():
    return os.environ.get("WENLEMA_KB") or os.path.join(HERE, "kb.db")


SCHEMA_VERSION = 1

# 排除名单的判定：除了 key 完全相等，还要挡住「同一个词的碎片」。
# 起因：① 的 prefilter 会把 `Top-K` 拆成 `Top`（K 太短被丢）和 `Top-K` 两个候选。
# 用户排除了 `Top-K` 之后，`Top` 照样会被解释 —— 看起来就像排除没生效。
# 但也不能无限放宽（排除了 `rag` 就不该连带封掉 `ragflow`），
# 所以只认「长度差 ≤ 2 的前缀关系」。
_PREFIX_SLACK = 2

_conn = None
_lock = threading.RLock()

# ══════════ 归一化 ══════════

# 模型经常把术语连解释一起返回：「RAG（检索增强生成）」「Chunking (chunk)」。
# 拿这种串直接当 key，就会出现「这次存成 rag检索增强生成，下次 OCR 到 RAG 永远查不到、
# 永远重问」—— 一个只在长期使用后才暴露、且完全不报错的 bug。
# ⚠ 截断字符集里【没有】中文了 —— v0.20 删掉的，理由见 canon()。
_CUT = re.compile("[(（:：,，、=]")

# `+` 和 `#` 必须转成词，不能直接丢掉 —— 否则 C++ 和 C# 的 key 都是 "c"，两个语言撞一起。
_SYMBOLS = (("++", "plusplus"), ("#", "sharp"), ("+", "plus"))

# 归一化最后留下的字符：数字、小写字母、CJK 汉字。其余全删。
# ⚠ 用 "\\u4e00" 而不是 r"一" —— raw string 里 \u 不会被解析成字符，
#   会变成「反斜杠 + u + 4 + e + 0 + 0」六个字符，正则直接错。
_KEEP = re.compile("[^0-9a-z\\u4e00-\\u9fff]")


def canon(s):
    """术语 → 知识库主键。全项目只有这一份实现。

    规则（顺序不能换）：
        0. NFKC 归一 —— 全角字母/数字/标点先折成半角
        1. 在第一个左括号 / 冒号 / 逗号 / 顿号 / 等号处截断
        2. `++` → plusplus，`#` → sharp，`+` → plus
        3. 转小写，只留数字、小写字母、中文
        4. 全空就返回 ""，由调用方的 `if not k` 接住

    ⚠ 撞车是【已知且接受】的：`A/B` 和英文词 `ab` 都归一化成 `ab`。
      实测确认过，失败模式温和（两个词共用一条解释），
      且 `ab` 作为独立英文词几乎不会被单独抓到。**别当成 bug 去改。**

    ══ v0.20 改了三处，全是为了中文（决策 51）。改之前先看这三条 ══

    · **NFKC**：中文输入法全角模式下，剪贴板里可能是 `ＲＡＧ`。
      不归一的话第 3 步会把它整条删成空 key —— 那是真把词丢了。
      顺带 `（）、：` 全角标点也折成半角，_CUT 一套就够。

    · **_CUT 里删掉了 `\\u4e00-\\u9fff`**。以前是「碰到第一个中文字就截断」，
      对纯 ASCII 逐条等价（`RAG 检索增强` 两条路都得到 `rag` —— 第 3 步本来
      就会把中文删光，那个分支是冗余的），但对中文是灾难：
      `【抓手】` 在 `抓` 处切断、留下 `【` 非空，于是 **`【` 自己成了一个主键**。
      所有 `【…】` 开头的词共用它，`record_entries` 的 INSERT OR IGNORE
      只留第一条，之后每遇到一个新词就返回一条**毫不相干的旧解释** ——
      是「给错答案」，不是「少给答案」。标点开头的中文同理
      （`「抓手」`→`「`、` 抓手`→` `，一个空格当主键）。

    · **兜底从 `s.lower()` 改成删干净后的结果**。以前 `抓手。` 的 key 就是
      `抓手。`，换个标点就变成一个全新的词 —— 永远查不到、永远重问、永远花钱。

    改完的效果：`【抓手】` / `「抓手」` / `抓手。` / ` 抓手` / `抓手（黑话）`
    全部归一成 `抓手`。同一个词第二次被选中就能命中知识库、不再花钱，
    这是这条通道最值钱的一条性质。
    """
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s)
    s = _CUT.split(s, 1)[0] or s
    for a, b in _SYMBOLS:
        s = s.replace(a, b)
    return _KEEP.sub("", s.lower())


# ══════════ 连接 ══════════
#
# ⚠ 为什么是「模块级单连接 + 锁」，而不是每个线程各拿一个连接：
#   pywebview 的 js_api 调用【每一次都新开一条线程】（webview/util.py:335）。
#   用 threading.local() 缓存连接的话，等于每次调用都新建一个 SQLite 连接且永不关闭，
#   WAL 模式下 -wal / -shm 文件会被反复创建，Windows 上还会残留句柄锁住 kb.db。
#
#   sqlite3.threadsafety == 3（本机实测），共享连接本身是安全的，
#   只需要手动关掉 Python 那层 check_same_thread 检查，再自己加锁把写操作串起来。

def _connect():
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        c = sqlite3.connect(kb_path(), check_same_thread=False, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")      # 自动通道上线后读多写多
        c.execute("PRAGMA busy_timeout=3000")
        c.execute("PRAGMA synchronous=NORMAL")
        _migrate(c)
        _conn = c
        return _conn


def _migrate(c):
    """schema 版本。现在只有 v1，但 migration 的架子现在就搭上 ——
    下一版要做「导入自定义知识库」和条目删除，schema 一定会改。
    现在加是三行代码，那时候加就是数据迁移事故。"""
    v = c.execute("PRAGMA user_version").fetchone()[0]
    if v >= SCHEMA_VERSION:
        return
    if v < 1:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS terms (
            key          TEXT PRIMARY KEY,   -- canon() 的产物
            term         TEXT NOT NULL,      -- 展示用的拼写，首次写进来的那个赢
            explanation  TEXT NOT NULL,
            source       TEXT NOT NULL DEFAULT 'ai',   -- ai | harvest | import | manual
            created_at   INTEGER NOT NULL,
            updated_at   INTEGER NOT NULL,
            last_used_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS history (
            id          INTEGER PRIMARY KEY,
            key         TEXT NOT NULL,
            term        TEXT NOT NULL,
            explanation TEXT NOT NULL,
            source      TEXT NOT NULL,   -- ai | kb
            at          INTEGER NOT NULL,-- epoch 秒
            day         TEXT NOT NULL    -- 'YYYY-MM-DD' 本地日期。决策 11 的「今日」靠它，
                                         -- 不在 SQL 里做时区换算（那个依赖系统设置）
        );
        CREATE INDEX IF NOT EXISTS ix_history_key_at ON history(key, at DESC);
        CREATE INDEX IF NOT EXISTS ix_history_day    ON history(day);

        CREATE TABLE IF NOT EXISTS excluded (
            key  TEXT PRIMARY KEY,
            term TEXT NOT NULL,
            at   INTEGER NOT NULL
        );
        """)
        c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    c.commit()


def close():
    """退出前显式关掉，让 WAL 落盘。close_app() 里会调。"""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.commit()
                _conn.close()
            except Exception:
                pass
            _conn = None


# 兼容：对外只暴露这个别名，免得两处拼写不一样
def norm(s):
    return canon(s)


def _now():
    return int(time.time())


def _today():
    return date.today().isoformat()


# ══════════ 排除名单 ══════════

def excluded_keys():
    """排除名单里的全部 key。"""
    c = _connect()
    with _lock:
        return [r["key"] for r in c.execute("SELECT key FROM excluded").fetchall()]


def is_excluded(key, ex_keys):
    """这个 key 在不在排除名单里。**排除判定的唯一实现处。**

    ⚠ 它必须能被两处调用：① 的候选表（filter_candidates），
      以及 ② 回来的整条（drop_excluded）。
      以前它只是 filter_candidates 里的一个闭包，只有候选表用得上 ——
      于是「不再解释该词」对词组/整句静默失效：它们根本不在候选表里，
      右键点了、toast 照样说「已不再解释」，下一轮那个整条照常回来。
    """
    if key in ex_keys:
        return True
    # 碎片兜底：见 _PREFIX_SLACK 的说明
    #
    # ⚠ 只对【两侧都是 ASCII】的 key 生效（v0.20 / 决策 51）。
    #   这条兜底存在的理由是**英文分词器的产物**：pipeline.WORD 会把 `Top-K`
    #   拆出一个 `Top`，于是排除了 `Top-K` 还得连坐 `Top`，否则看起来像没生效。
    #   中文选段不产生这种碎片 —— 而中文词普遍 2~4 个字，
    #   _PREFIX_SLACK 的「差 2 字」等于松弛 50%~100%：
    #   排除「执行力模型」会让「执行力」一起消失，排除「对齐颗粒度」连坐「对齐」。
    #   ★ 被排除的词永远不出现，而界面上【没有任何地方】说它为什么不见了 ——
    #     这正是这个项目最不能接受的一类失败（静默 + 看起来像没做）。
    #   把机制收回到它被写出来的那个场景里，是缩小行为面，不是扩大。
    for ek in ex_keys:
        if not (key.isascii() and ek.isascii()):
            continue
        if len(key) >= 3 and abs(len(key) - len(ek)) <= _PREFIX_SLACK:
            if key.startswith(ek) or ek.startswith(key):
                return True
    return False


def drop_excluded(entries):
    """② 回来的解释里，把用户排除过的整条去掉。

    ⚠ 排除判定【必须】也跑一遍 ② 的产物。filter_candidates 只看得到 ① 的候选表，
      而词组/整句是 ② 从原文里拎出来的整条 —— 不在候选表里，
      光靠 ③ 永远拦不住它。不跑这一遍的话，右键那个「不再解释该词」
      对这个功能新引入的整条就是【静默失效】。
    """
    ex = excluded_keys()
    if not ex:
        return list(entries or [])
    return [e for e in (entries or []) if not is_excluded(e.get("key") or "", ex)]


# ══════════ ③ 主入口 ══════════

def filter_candidates(candidates, policy="dedupe"):
    """① 的候选 → 三个桶。**③ 的唯一实现处**，自动和手动通道共用。

        kb_hits  [{term, key, line, at, explanation, source}]  直接贴出来，不花钱
        ai_todo  [{term, key, line, at}]                       要交给 ② 的
        blocked  [{term, key, why}]  why: excluded | not_in_kb | over_limit

    顺序很关键：**排除名单必须在知识库命中之前判**。
    反过来的话，一个用户明确排除掉的词会从库里被贴出来。

    ⚠ 入参 candidates 里可能同时有 `Top-K` 和 `TopK` ——
      ① 的 seen 是按 w.lower() 去重的（pipeline.py），不做 canon，
      所以这两个会各自成为一条候选。这里必须按 key 再合一次，
      否则同一个词会命中两次、贴出两张一模一样的卡。
      合并时保留【首次出现】的那条（和 ① 的取义一致）。
    """
    merged, seen = [], set()
    for c in candidates or []:
        k = c.get("key") or canon(c.get("term"))
        if k in seen:
            continue
        seen.add(k)
        c = dict(c)
        c["key"] = k
        merged.append(c)

    if not merged:
        return {"kb_hits": [], "ai_todo": [], "blocked": []}

    c = _connect()
    with _lock:
        ex_rows = c.execute("SELECT key FROM excluded").fetchall()
        keys = [m["key"] for m in merged]
        qmarks = ",".join("?" * len(keys))
        term_rows = {
            r["key"]: r
            for r in c.execute(
                f"SELECT key, term, explanation, source FROM terms WHERE key IN ({qmarks})",
                keys,
            ).fetchall()
        }

    ex_keys = [r["key"] for r in ex_rows]

    hits, todo, blocked = [], [], []
    for m in merged:
        k = m["key"]
        if is_excluded(k, ex_keys):
            blocked.append({"term": m["term"], "key": k, "why": "excluded"})
            continue

        row = term_rows.get(k)
        if row:
            if policy in ("dedupe", "whitelist"):
                hits.append({
                    "term": row["term"],          # 展示库里那个拼法，不用这轮的
                    "key": k,
                    "line": m.get("line", ""),
                    "at": m.get("at"),
                    "explanation": row["explanation"],
                    "source": row["source"],
                })
                continue
            # policy == "repeat"：用户明确要求【重新问一遍】。
            # 库里那条旧解释这次不用（但也不删 —— 这是个可逆操作）。

        if policy == "whitelist":
            blocked.append({"term": m["term"], "key": k, "why": "not_in_kb"})
            continue

        todo.append({"term": m["term"], "key": k,
                     "line": m.get("line", ""), "at": m.get("at")})

    return {"kb_hits": hits, "ai_todo": todo, "blocked": blocked}


# ══════════ 把模型的回答认领回候选 ══════════

# 模型返回的术语和候选不完全一致时，允许差多少个字符还算同一个词。
# `Chunk` vs `Chunking` 差 3，`Retriever` vs `Retrieval` 差 3 —— 够用了。
# 放太宽会把 `rag` 和 `ragflow` 认成一个。
_TERM_SLACK = 6


def attach_keys(entries, candidates):
    """给模型返回的每条解释认领一个候选 key。

    ⚠ **key 一律从【候选】派生，不从模型返回的术语派生。**
      候选来自用户笔记的 OCR，稳定、可复现；模型返回的拼写每轮都可能变
      （这轮 `Chunking`、下轮 `Chunk`，或者带上括号写成
      `RAG (Retrieval-Augmented Generation)`）。
      拿模型那个拼写当知识库主键的话，同一个词下次永远查不到、永远重问 ——
      一个只在长期使用后才暴露、而且完全不报错的 bug。

    顺带把展示用的 `term` 也换成候选那个拼法：用户框的是 `RAG`，
    面板上写着 `Rag` 会让人愣一下。

    认不上的（模型自己从原文拎出来的、① 没捞到的词）就用它自己的 canon 当 key，
    照常入库 —— 这不算错，下次 OCR 到同样的拼写就能命中。
    **词组和整句走的就是这一条**（原文里的 `Long Short-Term Memory` 不在候选表里）。

    返回的每条上还带一个 `covers` —— 这一条【作为一个整条】把哪些候选收进去了，
    前端据此不再给那些词单独成卡。见 _covered()。
    """
    pool = [c for c in (candidates or []) if c.get("key")]
    used = set()               # 已经被某一条【认领】掉的候选 key
    out = []

    for e in entries or []:
        raw = (e.get("term") or "").strip()
        k = canon(raw)
        hit = _claim(k, raw, pool, used)

        if hit is not None:
            used.add(hit["key"])
            out.append({"term": hit["term"], "key": hit["key"],
                        "explanation": e.get("explanation") or ""})
        else:
            out.append({"term": raw, "key": k or raw.lower(),
                        "explanation": e.get("explanation") or ""})

    # ⚠ 「覆盖了哪些候选」必须等【全部认领完】再算：
    #   一条整条覆盖到的候选，要是被别的条认领走了，那张卡片得留着 ——
    #   收掉它等于把那条解释弄丢。所以这里看的是最终的 used。
    for e, rec in zip(entries or [], out):
        rec["covers"] = _covered((e.get("term") or "").strip(), rec["key"], pool, used)

    return out


def _claim(k, raw, pool, used):
    """给一条解释找一个候选当它的 key。找不到返回 None。"""
    for c in pool:
        if c["key"] == k and c["key"] not in used:
            return c

    # 拼写有出入：退一步做前缀认领，取最接近的那个
    #
    # ★ 多词术语【不许】被认领到单个词候选上。
    #   这段兜底是为 `Chunk` → `Chunking` 写的（长度差 3），
    #   但它对词组同样成立：`Vector DB` 的 canon `vectordb`(8) 对上候选 `vector`(6)，
    #   长度差 2 ≤ 6、前缀也成立 —— 于是整整一条词组被认领成单词 `vector`。
    #   后果不止是「显示成一个单词」：展示 term 被改写成 `vector`，
    #   解释挂到 `vector` 的 key 上，再以 `vector` 的身份写进库 ——
    #   下一轮这个单词命中知识库，永远贴出这条词组级的解释，库里存错了。
    multiword = " " in raw
    hit = None
    for c in pool:
        ck = c["key"]
        if ck in used or len(ck) < 3 or len(k) < 3:
            continue
        if multiword and " " not in (c["term"] or ""):
            continue
        if abs(len(ck) - len(k)) <= _TERM_SLACK and (
                ck.startswith(k) or k.startswith(ck)):
            if hit is None or len(ck) > len(hit["key"]):
                hit = c
    return hit


# 把术语切成「词」用的。和 pipeline.WORD 保持同样的字符集（`C++` / `Top-K` / `CI/CD`
# 要留在同一个词里），区别只是这里还允许空格 —— 空格正是词组的分界。
_TERM_WORDS = re.compile(r"[^A-Za-z0-9+#/.\-]+")


def _covered(raw, own_key, pool, used):
    """这一条整条把哪些候选包进去了 → `[key, ...]`。

    ⚠ **只按整词比，不做子串。** 子串看着更省事，但会误伤得很厉害：
      候选 `rag` 是 `sto**rag**e` 的子串、`age` 也是 ——
      一整条 `Storage layer` 会把毫不相干的 RAG 吞掉，
      用户那个词就再也不出现在面板上了，而且完全不报错。
    ⚠ 被别的条认领走的候选不算覆盖（见 attach_keys 里的说明）。
    """
    words = {canon(w) for w in _TERM_WORDS.split(raw) if w}
    words.discard(own_key or "")

    keys, seen = [], set()
    for c in pool:
        ck = c["key"]
        if ck in seen or ck in used or ck not in words:
            continue
        seen.add(ck)
        keys.append(ck)
    return keys


# ══════════ 写库 ══════════

def record_entries(entries, policy="dedupe"):
    """② 回来后落库。

    **自动收录（用户 2026-09-14 拍板）**：dedupe 模式下模型答一条就直接进库，
    不等用户手动点「收纳历史」——
    否则「不重复解释」的省钱承诺要等用户手动收一次才开始生效，
    在那之前同一个词每轮都在重新花钱。

    repeat 模式【不】自动覆盖库里的旧解释：用户切这个模式的意思是
    「这次我想看看新答案」，不是「把旧的换掉」。
    看完想留下，再点「收纳历史」把它收编 —— 两个按钮的语义因此不重叠。
    """
    if not entries:
        return 0
    now, day = _now(), _today()
    c = _connect()
    n = 0
    with _lock:
        with c:                                     # 事务
            for e in entries:
                k = e.get("key") or canon(e.get("term"))
                term = e.get("term") or ""
                expl = e.get("explanation") or ""
                if not k or not expl:
                    continue

                # history 写前去重：同一个 key 的解释和上一条一模一样就不写。
                # temperature=0.2 保证同一个词的解释高度一致，
                # 这一条能把 history 的量砍掉 95% 以上，对 repeat 完全无损。
                last = c.execute(
                    "SELECT explanation FROM history WHERE key=? ORDER BY at DESC LIMIT 1",
                    (k,),
                ).fetchone()
                if not last or last["explanation"] != expl:
                    c.execute(
                        "INSERT INTO history(key, term, explanation, source, at, day)"
                        " VALUES(?,?,?,?,?,?)",
                        (k, term, expl, "ai", now, day),
                    )
                n += 1

                if policy == "dedupe":
                    # INSERT OR IGNORE：首次写进来的拼法赢，后面不同大小写不覆盖展示。
                    c.execute(
                        "INSERT OR IGNORE INTO terms"
                        "(key, term, explanation, source, created_at, updated_at, last_used_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (k, term, expl, "ai", now, now, now),
                    )
            # 顺手清掉过期的流水（保留 90 天）。
            # history 只服务于「收纳历史」，不需要一字不落。
            c.execute("DELETE FROM history WHERE at < ?", (now - 90 * 86400,))
    return n


def touch_used(keys):
    """知识库命中的词，顺手记一下「什么时候用过」。

    ⚠ 这里【只】更新时间戳，不写 history。
      库命中的词本来就在库里，再往流水里灌一遍没有意义，
      只会让 history 白白翻倍。流水留给「新解释出来的东西」，
      它的唯一用途是「收纳历史」。
      last_used_at 现在没人读，但将来「白名单模式下先显示常用的」
      和「按常用排序」都要它，现在攒着几乎零成本。
    """
    ks = [k for k in (keys or []) if k]
    if not ks:
        return 0
    now = _now()
    c = _connect()
    with _lock:
        with c:
            c.executemany("UPDATE terms SET last_used_at=? WHERE key=?",
                          [(now, k) for k in ks])
    return len(ks)


def harvest():
    """收纳历史：把 history 里每个词【最新】的那条解释正式收编进 terms。

    主要用途是 repeat 模式 —— 用户让它重答了一版，看完觉得好，点这个收编。
    被排除的词跳过（否则库里会堆一批永远看不见的条目）。
    """
    now = _now()
    c = _connect()
    added = updated = skipped = 0
    with _lock:
        with c:
            ex = {r["key"] for r in c.execute("SELECT key FROM excluded").fetchall()}
            rows = c.execute("""
                SELECT h.key, h.term, h.explanation,
                       (SELECT source FROM terms t WHERE t.key = h.key) AS src
                  FROM history h
                  JOIN (SELECT key, MAX(at) AS mx FROM history GROUP BY key) m
                    ON h.key = m.key AND h.at = m.mx
            """).fetchall()
            for r in rows:
                if r["key"] in ex:
                    skipped += 1
                    continue
                if r["src"] is None:
                    c.execute(
                        "INSERT INTO terms"
                        "(key, term, explanation, source, created_at, updated_at, last_used_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (r["key"], r["term"], r["explanation"], "harvest", now, now, now),
                    )
                    added += 1
                elif r["src"] in ("ai", "harvest"):
                    # 只覆盖自动收的和以前收的。
                    # 将来从 md 导进来的手工条目（source='import'/'manual'）
                    # 不能被一次「收纳历史」全部冲掉。
                    c.execute(
                        "UPDATE terms SET explanation=?, updated_at=?, source='harvest'"
                        " WHERE key=?",
                        (r["explanation"], now, r["key"]),
                    )
                    updated += 1
                else:
                    skipped += 1
    return {"added": added, "updated": updated, "skipped": skipped}


# ══════════ 排除名单（决策 21） ══════════
#
# ⚠ 这是个【单向门】，必须有回来的路。
#   用户手一滑右键一个常用词，它就从所有地方消失了，而且界面上没有任何地方
#   能看见它、恢复它 —— 那不叫功能，叫数据丢失。所以 unexclude / list_excluded
#   和 exclude 一样是必需的，不是「以后再说」。

def exclude(term):
    k = canon(term)
    if not k:
        return {"ok": False, "msg": "这个词归一化之后是空的，没法排除"}
    with _lock:
        c = _connect()
        with c:
            c.execute("INSERT OR REPLACE INTO excluded(key, term, at) VALUES(?,?,?)",
                      (k, term, _now()))
    return {"ok": True, "key": k, "term": term}


def unexclude(key):
    with _lock:
        c = _connect()
        with c:
            cur = c.execute("DELETE FROM excluded WHERE key=?", (key,))
    return {"ok": True, "removed": cur.rowcount}


def list_excluded():
    with _lock:
        c = _connect()
        return [{"key": r["key"], "term": r["term"], "at": r["at"]}
                for r in c.execute("SELECT key, term, at FROM excluded ORDER BY at DESC")]


# ══════════ 导出 md（决策 14：两栏、可编辑、将来要能再导回） ══════════

def _esc_cell(s):
    """表格单元格转义。

    ⚠ 顺序不能换：先把反斜杠自己转义，再转义竖线。
      反过来的话，原文里本来以 `\\` 结尾时会产生 `\\\\|`，反而把列撑破。

    ⚠ 术语列也要转义，别只转义解释列。prefilter 的正则确实给不出带 `|` 的词，
      但下一版是「可导入的自定义知识库」，手写的 md 里什么都有。
      两列走同一个函数，将来才对得上。
    """
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\").replace("|", "\\|")
    return re.sub(r"\s+", " ", s).strip()      # 换行/多空格压成一个空格


def export_md(scope="all", out_dir=None):
    """导出成 md。scope: 'all' 导整库，'today' 导今天动过的（决策 11）。

    ⚠ 这是【快照】。文件里改的东西不会回到库里 —— 导入功能排在下一版。
      这个事实要写进文件头，否则下一版用户会拿着编辑过的文件来问为什么没生效。

    ⚠ 严格两栏。想标「来自知识库 / AI」只能放在表格外 ——
      多一列，下一版的导入就没法按列对齐了。
    """
    day = _today()
    with _lock:
        c = _connect()
        if scope == "today":
            rows = c.execute("""
                SELECT h.term, h.explanation FROM history h
                  JOIN (SELECT key, MAX(at) AS mx FROM history
                         WHERE day=? GROUP BY key) m
                    ON h.key = m.key AND h.at = m.mx
                 ORDER BY h.key
            """, (day,)).fetchall()
            title = f"问了吗 · 今日检索内容（{day}）"
        else:
            rows = c.execute(
                "SELECT term, explanation FROM terms ORDER BY key").fetchall()
            title = "问了吗 · 知识库全量"

    out = [
        f"<!-- {title} -->",
        "<!-- 这是导出当时的快照。在这里改的内容不会回到「问了吗」的库里。 -->",
        "",
        f"# {title}",
        "",
        "| 术语 | 解释 |",
        "| --- | --- |",
    ]
    for r in rows:
        out.append(f"| {_esc_cell(r['term'])} | {_esc_cell(r['explanation'])} |")
    text = "\n".join(out) + "\n"

    if out_dir is None:
        out_dir = default_dir()
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": f"建不了目录：{out_dir}", "detail": str(e)}

    base = f"问了吗-{'今日' if scope == 'today' else '知识库'}-{day}.md"
    path, i = os.path.join(out_dir, base), 1
    while os.path.exists(path):                      # 同一天导第二次别静默覆盖
        path = os.path.join(out_dir, f"问了吗-{'今日' if scope == 'today' else '知识库'}-{day}-{i}.md")
        i += 1
    try:
        # ⚠ 必须显式 encoding。本机 locale 是中文，不写就会用 ANSI 代码页，
        #   中文解释直接变乱码。main.py 开头 reconfigure stdout 是同一个坑。
        #   不加 BOM —— Obsidian / 飞书都吃 UTF-8 无 BOM。
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except Exception as e:
        return {"ok": False, "msg": "写文件失败", "detail": str(e)}

    return {"ok": True, "path": path, "count": len(rows)}


def default_dir():
    """导出默认目录。

    ⚠ 这一条是「别把功能压在文件夹对话框上」的落点：
      有了它，就算选择框那天挂了，「导出 md」照样能用。
    """
    d = os.path.join(os.path.expanduser("~"), "Documents", "问了吗")
    return d


# ══════════ 给界面用的统计 ══════════

def stats():
    with _lock:
        c = _connect()
        one = lambda q: c.execute(q).fetchone()[0]
        return {
            "terms": one("SELECT COUNT(*) FROM terms"),
            "history": one("SELECT COUNT(*) FROM history"),
            "excluded": one("SELECT COUNT(*) FROM excluded"),
        }
