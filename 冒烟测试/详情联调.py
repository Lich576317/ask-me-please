# -*- coding: utf-8 -*-
"""「查看详情」多轮对话联调 —— 纯状态机，不开窗口、不联网、不花钱。

跑起来：
    python "Desktop\\问了吗\\冒烟测试\\详情联调.py"

本文件盯的是 `app/detail.py`（多轮会话的状态与上下文拼装）和
「这件事只有一份实现」这条规矩。它**不起窗口**，因为那一半
（点卡片 → 弹框 → 内容上屏 → 缓存命中不重复花钱）在
`AI端到端联调.py` 的 **⑨** 那一相里，用的是那边已有的开窗 + 假 DeepSeek 设施。

────────────────────────────────────────────────────────
⚠️ 这里**验不了**的三件事，如实写在下面，别当成验过了：

  ① 「对话框盖在面板上、不动窗口尺寸」—— 那是 pywebview 的窗口几何，
     这个文件里连一个窗口都没有。它靠 `AI端到端联调` 的几何断言 +
     `框选联调` 那套「窗口已就位」判据兜。
  ② 「同一个词第二次点，界面上真的没花钱」—— 缓存决策在
     `main.Api.detail_open` 里，那需要一个真的 `Api` 实例（它会起自动通道
     和热键线程）。真正的判据是**假服务器收到几次请求**，那在端到端那边。
     这里只验到「`Chats.start` 不重置」这一层机制。
  ③ 「模型真的照着第三份提示词答」—— 提示词服从度这个项目一贯测不了，
     只能等用户拿真 Key 跑（会产生真费用）。这里验的是**文本本身写对了没有**。
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

# ⚠ G 节会 `import main` 并造一个 `Api.__new__` 出来。虽然那个实例碰不到库和
#   设置，但**指开这两个是零成本的保险**（同 `密钥落盘联调.py` 的做法）：
#   万一将来谁在 `_detail_turn` 里顺手加了一句读 settings 的代码，
#   这里不会去动用户那份唯一的 Key。必须在 import main 之前设。
#
#   ⚠ 这两份都是**联调专用**的临时文件，不是用户的 `app\settings.json` /
#     `app\kb.db` —— 和 `WENLEMA_BASE_URL` 一个性质，正常启动时它们不存在。
os.environ.setdefault("WENLEMA_KB", os.path.join(HERE, "_detail_kb.db"))
os.environ.setdefault("WENLEMA_SETTINGS", os.path.join(HERE, "_e2e_settings.json"))

# ⚠ `▸` 这类字符 GBK 编不出来，输出被重定向时 print 当场抛 UnicodeEncodeError。
#   （`知识库联调.py` 第一版就栽在这儿，app/main.py 开头也有同一段。）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import detail as dm                                                # noqa: E402
import prompt as pm                                                # noqa: E402

# 「出口自检」那把共用的尺子（见 `尺子.py`）。
sys.path.insert(0, HERE)
import 尺子                                                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [通过] {name}")
    else:
        FAIL.append((name, detail or "断言为假"))
        print(f"  [失败] {name}　→ {detail}")


def read_app(fn):
    with open(os.path.join(APP, fn), encoding="utf-8") as f:
        return f.read()


def _strip_docstrings(tree):
    """把每处 docstring 从 AST 里摘掉（就地改，返回同一个 tree）。"""
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:]
    return tree


# ⚠⚠ 下面这两个函数是【必须】的，不是洁癖。
#
#   2026-09-21 第一版 F.1 / F.2 直接拿 `"sqlite3" in 源码` 判，
#   当场红了 —— 因为 `detail.py` 的**文件头注释里写着**「所以这里没有任何
#   `json.dump` / sqlite / 文件读写」，`ai.chat` 的**docstring 里写着**
#   「⚠ 不要 `response_format: json_object`」。两条判据都命中了
#   **它们自己那句话的说明文字**。
#
#   ★ 这正是本项目那条老教训的第六个变体：「判据不能建在一句话上」。
#     前五次是【两个生产者打出同一句话】；这一次是
#     **【同一句话既是判据、又是判据要防的东西的名字】**。
#     而且方向更阴：写注释越认真，判据越容易红 ——
#     于是下一个人会把注释删掉去迁就测试。
#
#   修法不是把那两句话从注释里删掉（那两句话说得很对，该留着），
#   是**换判据**：比的是【代码】，不是【文件里出现过的字】。
#   走 AST 就自然做到这件事 —— 注释根本不进 AST，docstring 摘掉即可。
#   （同源做法见 `扫描detail文案.py`：它也是走 AST 不走正则。）
def code_only(src):
    """源码 → 只剩代码（注释与 docstring 都去掉）。"""
    return ast.unparse(_strip_docstrings(ast.parse(src)))


def func_code(src, name):
    """源码里某个函数的【代码】。找不到就返回空串。"""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = ast.Module(body=list(node.body), type_ignores=[])
            return ast.unparse(_strip_docstrings(body))
    return ""


# ══════════════════════════════════════════════════════════
# A. 第一句（seed_question）
# ══════════════════════════════════════════════════════════

def t_seed():
    print("\n▸ A. 打开详情时自动发出去的第一句")

    q = dm.seed_question("RAG")
    check("A.1 第一句里带着术语原文", "RAG" in q, f"实际是 {q!r}")

    # ⚠ 这条不是「文案好不好看」：`line` 是【只有前端才知道】的东西
    #   （`terms` 表里没存上下文，出了这一屏就再也拿不到）。
    #   第一句不带，模型这辈子不知道这个词是在什么语境里出现的。
    q2 = dm.seed_question("RAG", "我们用 RAG 做检索增强。")
    check("A.2 ★ 有上下文时，那句话原样进了第一句",
          "我们用 RAG 做检索增强。" in q2, f"实际是 {q2!r}")

    # 正向对照的另一半：没上下文时【不该】凭空多出一段。
    check("A.3 没有上下文时，第一句里不带那个句子",
          q2 != q and "出现在这段话里" not in q, f"实际是 {q!r}")

    # 术语是空串（理论上不该发生，但 seed_question 是公开函数）
    q3 = dm.seed_question("", "")
    check("A.4 术语为空也不炸，仍然是完整的一句",
          isinstance(q3, str) and len(q3) > 5, f"实际是 {q3!r}")


# ══════════════════════════════════════════════════════════
# B. 上下文截断（build_context）
# ══════════════════════════════════════════════════════════

def turns(n):
    """造 n 轮对话（每轮 user + assistant 两条）。"""
    out = []
    for i in range(n):
        out.append({"role": "user", "text": f"问{i}"})
        out.append({"role": "assistant", "text": f"答{i}"})
    return out


def at_cap(rounds=dm.MAX_TURNS, extra=True):
    """会话【刚好顶到上限】时，`main._detail_turn` 真正会交给
    `build_context` 的那个列表。

    ★★ 为什么要专门造这一个形状（2026-09-21 才发现，原以为 B.4 是「够不着的兜底」）：

      会话里的 msgs 永远是【偶数条】（每一轮成功才追加 user+assistant 两条，
      失败不写进去），所以从下标 0 开始的 user/assistant 对齐是好的。
      但 `_detail_turn` 在截断【之前】先把这一句追问接了上去：
        `pending = list(rec["msgs"]) ; pending.append({user})`
      —— 于是轮到第 21 轮时 pending 是 **41 条（奇数）**，
      而 `keep = max_turns * 2` 永远是**偶数**，`msgs[-keep:]` 的起点
      落在下标 1，**第一条正好是 assistant**。

      也就是说：B.4 那个 while 循环不是「将来谁改了写入顺序才用得上」的兜底，
      它是**每一次真正发生截断的时候都会走到的主路径**（第 21 轮起每轮都走）。
      截断没发生的时候它一次都不执行 —— 所以只看 turns(10) 那种「整齐的偶数」
      是永远碰不到它的，那几条判据会全绿，而绿得毫无意义。
    """
    msgs = turns(rounds)
    if extra:
        msgs = msgs + [{"role": "user", "text": "接着问一句"}]
    return msgs


def t_context():
    print("\n▸ B. 上下文截断")

    msgs = turns(3)
    kept, dropped = dm.build_context(msgs, max_turns=5)
    check("B.1 没到上限：原样返回、一轮都没丢",
          kept == msgs and dropped == 0, f"留下 {len(kept)} 条、丢了 {dropped}")

    kept, dropped = dm.build_context(turns(10), max_turns=4)
    check("B.2 ★ 到了上限：留下的正好是 4 轮（8 条）",
          len(kept) == 8, f"实际留下 {len(kept)} 条")

    # ★★ 这条是本文件里最容易写错的一条。
    #    丢掉的必须是【问题的条数】，不是「总条数」。
    #    总条数的话，界面上那句「最早的 N 轮没发出去」会翻倍 ——
    #    而它看起来只是个数字，用户拿它判断不了任何事，也不会有人报 bug。
    check("B.3 ★★ 丢的是「几轮」不是「几条」（10 轮留 4 轮 → 丢 6 轮）",
          dropped == 6, f"实际报的是 {dropped}")

    # ⚠ 截出来第一条是 assistant 的话，模型看到的是「自己刚说过一段话
    #   却没人问」，很容易接着那句话自己往下编。
    kept, _ = dm.build_context(turns(10), max_turns=4)
    check("B.4 ★ 截断之后，第一条一定是 user",
          kept and kept[0]["role"] == "user",
          f"第一条是 {kept[0]['role'] if kept else '(空)'}")

    # ★★ 上面那条用的是「整齐的偶数条」—— 那个形状【裸切片本来就是对的】，
    #    所以它绿了也证明不了 `while` 那段守卫干了活。
    #    真正会走到守卫的是 at_cap() 那个形状（见它的 docstring）。
    cap = at_cap()
    kept_cap, dropped_cap = dm.build_context(cap)
    check("B.4b ★★ 真到上限那个形状（41 条，起点本来是 assistant）也修回 user",
          kept_cap and kept_cap[0]["role"] == "user",
          f"留下 {len(kept_cap)}/{len(cap)} 条，第一条是 "
          f"{kept_cap[0]['role'] if kept_cap else '(空)'}")
    check("B.4c ★★ 修掉的那一条，要从「丢了几轮」里【扣掉】而不是当没发生",
          dropped_cap == 1,
          f"实际报的是 {dropped_cap}（切掉 a0 之后，计入的只有 u0 这一问；期望 1）")

    # 兜底：0 / 负数 / 乱七八糟的值不能把上下文切成空的 ——
    # 空上下文发出去，模型会答非所问，而界面上什么都看不出来。
    for bad in (0, -3, 0.5):
        kept, _ = dm.build_context(turns(5), max_turns=bad)
        check(f"B.5 max_turns={bad!r} 时兜底成「至少留一轮」",
              len(kept) >= 2, f"实际留下 {len(kept)} 条")

    check("B.6 空 / None → 空列表 + 0，不抛异常",
          dm.build_context(None) == ([], 0) and dm.build_context([]) == ([], 0),
          f"None→{dm.build_context(None)}、[]→{dm.build_context([])}")


# ══════════════════════════════════════════════════════════
# C. 会话表（Chats）
# ══════════════════════════════════════════════════════════

def t_chats():
    print("\n▸ C. 内存会话表")

    c = dm.Chats()
    rec = c.start("rag", "RAG", "我们用 RAG 做检索。")
    check("C.1 start 建会话，term / line 都存下来",
          rec["term"] == "RAG" and rec["line"] == "我们用 RAG 做检索。"
          and rec["msgs"] == [], f"实际是 {rec!r}")

    # ★★ 用户 2026-09-21 拍板：「这次开着就记住，关程序就忘」。
    #    这条就是那句话的落点。写反了（每次 start 都清空）的话，
    #    功能看着是好的，只是【每次点都花钱】—— 而用户完全看不出哪里不对。
    c.say("rag", "user", "第一句")
    c.say("rag", "assistant", "第一答")
    again = c.start("rag", "RAG（换个写法）", "别的上下文")
    check("C.2 ★★ 已存在的 key 再 start 时【原样返回旧的】，不重置",
          len(again["msgs"]) == 2 and again["term"] == "RAG",
          f"再 start 之后 msgs={len(again['msgs'])} 条、term={again['term']!r}")

    c.start("kpi", "KPI")
    c.say("kpi", "user", "问")
    c.say("kpi", "assistant", "答")
    got = c.get("kpi")
    check("C.3 say 按顺序追加 user / assistant 两条",
          [m["role"] for m in got["msgs"]] == ["user", "assistant"],
          f"实际是 {[m['role'] for m in got['msgs']]}")

    # ⚠ system 由调用方在【发请求那一刻】现拼。存进来的话，用户在操作台
    #   改了提示词，这条会话后面几轮还会抱着旧的那份不放（决策 37 要的是
    #   「改了立刻生效」）—— 而界面上看不出任何异常。
    try:
        dm.Chats().start("x", "X")
        cc = dm.Chats()
        cc.start("x", "X")
        cc.say("x", "system", "你是一个助手")
        ok = False
    except ValueError:
        ok = True
    except Exception:
        ok = False
    check("C.4 ★ say 拒绝 system 角色（提示词不跟着会话走，决策 37）", ok,
          "system 被存进会话了 —— 改提示词会不生效，而界面看不出异常")

    c2 = dm.Chats()
    check("C.5 say 到不存在的 key 返回 None（不报错、也不新建）",
          c2.say("nope", "user", "喂") is None and c2.count() == 0,
          f"count()={c2.count()}")

    # ⚠ 用 pop 不用 clear：「删除一个不存在的东西」报错的话，
    #   前端那个「重新问」按钮会因为一次重复点击弹一个没头没脑的错。
    c3 = dm.Chats()
    c3.start("a", "A")
    c3.reset("a")
    c3.reset("a")
    check("C.6 reset 幂等：删一个不存在的 key 也不报错",
          c3.count() == 0, f"count()={c3.count()}")

    c4 = dm.Chats()
    c4.start("a", "A")
    c4.start("b", "B")
    check("C.7 has / count / keys 对得上",
          c4.has("a") and c4.has("b") and not c4.has("c")
          and c4.count() == 2 and sorted(c4.keys()) == ["a", "b"],
          f"count={c4.count()} keys={c4.keys()}")
    check("C.8 has('') 是 False（空 key 不算一条会话）",
          not c4.has("") and not c4.has(None), f"has('')={c4.has('')}")

    # ★★ 这张表【故意不做归一化】。归一化是 `kb.canon()` 的事，而且
    #    只有那一份实现（硬约束 —— 两份实现迟早分叉，分叉了不报错，
    #    只是同一个词变成两条会话，用户会觉得「刚才聊过的它又忘了」）。
    #    这条断言钉的是「别在 Chats 里顺手加一个 .lower()」。
    c5 = dm.Chats()
    c5.start("RAG", "RAG")
    c5.start("rag", "rag")
    check("C.9 ★ Chats 自己不归一小写（归一化只归 kb.canon 管，别加第二份）",
          c5.count() == 2, f"count={c5.count()} —— 两边被并成一条了")


# ══════════════════════════════════════════════════════════
# D. 反向对照 —— 证明上面几条判据【够得着】
# ══════════════════════════════════════════════════════════
#
# ★★★ `CLAUDE.md` 那条老规矩：新加一条断言跑绿不算数 ——
#     给它配的变体不红，它就是装饰品。
#     下面是三个「写错了的实现」，判据必须在它们身上红。
#     ⚠️ 它们不是 mock 掉被测对象：被测的就是这三条判据本身够不够得着。

def _naive_dropped_total(msgs, max_turns):
    """写错的版本：丢掉的报成「总条数」。"""
    keep = max(1, int(max_turns)) * 2
    return len(msgs) - len(msgs[-keep:])


def _naive_slice(msgs, max_turns):
    """写错的版本：裸切片，不对齐 user。"""
    keep = max(1, int(max_turns)) * 2
    return msgs[-keep:]


def _naive_trim_tail(msgs, max_turns):
    """写错的版本：为了凑成「整轮」，从【后面】把不成对的那条剪掉。

    ★ 这是个很像样的错误 —— 「一轮 = 一问一答」听起来就该是整的，
      作者会觉得自己在收拾边界。而这里最后一条恰恰是用户刚打完、
      正要发出去的那句追问（`_detail_turn` 是先 append 再截断的），
      于是它被当成「多余的那半轮」剪掉：气泡还在屏幕上，模型没收到。
    """
    keep = max(1, int(max_turns)) * 2
    kept = list(msgs[-keep:])
    while kept and kept[-1].get("role") != "assistant":
        kept.pop()
    return kept


def _naive_cut_from_keep(msgs, max_turns):
    """写错的版本：丢了几轮，按 `(总条数 - keep) / 2` 算。

    这个算法在「裸切片」那套里是对的，但守卫 pop 掉一条之后 `kept` 短了一条，
    真实的切口就大了 —— 继续照 keep 算会把丢掉的轮数少报，甚至报成 0。
    """
    keep = max(1, int(max_turns)) * 2
    return max(0, (len(msgs) - keep) // 2)


class _NaiveChats:
    """写错的版本：每次 start 都清空。"""

    def __init__(self):
        self._d = {}

    def start(self, key, term, line=""):
        self._d[key] = {"term": term or "", "line": line or "", "msgs": []}
        return self._d[key]


def t_variants():
    print("\n▸ D. 反向对照（判据够不够得着）")

    msgs = turns(10)

    real_dropped = dm.build_context(msgs, max_turns=4)[1]
    naive_dropped = _naive_dropped_total(msgs, max_turns=4)
    check("D.1 ★★ B.3 够得着：把「几轮」写成「几条」，数就对不上（6 vs 12）",
          naive_dropped != real_dropped and naive_dropped == 12,
          f"真实现={real_dropped}、错实现={naive_dropped}")

    # ★★ B.4 的够得着，必须拿【真会发生的那个形状】去试。
    #    turns(10) 那种整齐的偶数条，裸切片本来就是对的 ——
    #    用它的 `_naive_slice` 当反向对照，红的是我的夹具，不是产品。
    #    第一次就是这么红的（`裸切片第一条是 'user'`），见 at_cap() 的 docstring。
    cap = at_cap()
    naive_first = _naive_slice(cap, max_turns=dm.MAX_TURNS)[0]["role"]
    check("D.2 ★★ B.4b 够得着：真到上限那个形状，裸切片第一条真是 assistant",
          naive_first == "assistant",
          f"裸切片第一条是 {naive_first!r}（真实现给的是 user，两者不同才算够得着）")

    # ★ 守卫是【从前面 pop】，绝不能从后面剪 ——
    #   从后面剪的话，用户刚刚打完、正要发出去的那句追问会被静默吞掉：
    #   他看到自己那句话的气泡在屏幕上，而模型压根没收到。
    #   这个形状里最后一条正是那句追问，所以拿它钉住。
    kept_cap = dm.build_context(cap)[0]
    check("D.2b ★★ 对齐是砍【开头】的：刚刚发出去的那句还在末尾",
          bool(kept_cap) and kept_cap[-1]["text"] == "接着问一句",
          f"最后一条是 {kept_cap[-1]['text']!r}" if kept_cap else "(空)")

    # → D.2b 的够得着：从后面剪的那种写法，照样通不过 ——
    #   而且它连「首条是 user」都没修好，说明 D.2b 那第二句不是多余的。
    trim_tail = _naive_trim_tail(cap, dm.MAX_TURNS)
    check("D.2c ★★ D.2b 够得着：改从后面剪，那句刚发出去的追问就没了",
          bool(trim_tail) and trim_tail[-1]["text"] != "接着问一句",
          f"末条是 {trim_tail[-1]['text']!r}" if trim_tail else "(剪成了空的)")

    # → B.4c 的够得着：按 keep 算丢了几轮，在这个形状上会报成 0。
    dropped_cap = dm.build_context(cap)[1]
    naive_cut = _naive_cut_from_keep(cap, dm.MAX_TURNS)
    check("D.2d ★★ B.4c 够得着：按 keep 算丢了几轮，这里会少报成 0",
          naive_cut == 0 and dropped_cap == 1,
          f"真实现={dropped_cap}、错实现={naive_cut}（两者不同才算够得着）")

    nc = _NaiveChats()
    nc.start("rag", "RAG")
    nc.start("rag", "RAG（换个写法）")
    check("D.3 ★★ C.2 够得着：把 start 写成每次都清空，term 会跟着变",
          nc._d["rag"]["term"] == "RAG（换个写法）",
          f"错实现的 term 是 {nc._d['rag']['term']!r} —— 它没变的话 C.2 是装饰品")


# ══════════════════════════════════════════════════════════
# E. 三份提示词
# ══════════════════════════════════════════════════════════

def t_prompt():
    print("\n▸ E. 第三份提示词")

    d = pm.DEFAULT_PROMPT_DETAIL
    check("E.1 第三份默认提示词在，而且是一段完整的说明",
          isinstance(d, str) and len(d) > 300, f"长度 {len(d)}")

    # ★★ 这一份和另外两份是【反着】的：卡片要的是「一句话，别展开」，
    #    详情要的恰恰是展开。合并成一份的话，两个方向必有一个是坏的 ——
    #    而且坏的那边只是「解释写得短/长」，不报错。
    check("E.2 ★★ 它【没有】前两份那句「不要展开成长篇科普」",
          "不要展开成长篇科普" not in d, "那句话还在，说明这一份是照抄的")

    check("E.3 前两份里【有】那句话（正向对照：E.2 不是因为整段被删才过的）",
          "不要展开成长篇科普" in pm.DEFAULT_PROMPT
          and "不要展开成长篇科普" in pm.DEFAULT_PROMPT_ANY,
          "英文那份或中文那份里没有这句话了 —— 有人的卡片解释被改长了")

    # ⚠ 这条硬要求是【格式】不是口味：对话框按纯文本显示，
    #   模型吐 `**加粗**` 的话那些星号会原样露在用户脸上。
    check("E.4 保留了「只输出纯文本、不要 markdown」那条硬要求",
          "不要用任何 markdown 标记" in d and "不要" in d,
          "格式那段没了")

    check("E.5 三份提示词都在后端（前端没各写一份）",
          "DEFAULT_PROMPT_DETAIL" in read_app("prompt.py")
          and "不要用任何 markdown 标记" not in read_app(os.path.join("web", "app.js")),
          "前端 app.js 里也有一份，两处迟早分叉")


# ══════════════════════════════════════════════════════════
# F. 只有一份实现
# ══════════════════════════════════════════════════════════

def t_single_source():
    print("\n▸ F. 只有一份实现（源码层）")

    det_code = code_only(read_app("detail.py"))
    # ⚠ 比的是【代码】不是【文件里出现过的字】—— 这一条的说明文字本身就
    #   写着「这里没有任何 json.dump」，拿字符串判会命中它自己。见 code_only 上面那段。
    check("F.1 ★ 会话不落盘（detail.py 的【代码】里没有 sqlite3 / json.dump / open）",
          "sqlite3" not in det_code and "json.dump" not in det_code
          and "open(" not in det_code,
          "用户 2026-09-21 拍板「关程序就忘」，落盘会变成第二个知识库")

    ai = read_app("ai.py")
    chat_code = func_code(ai, "chat")
    # ⚠ 加了 response_format 的话，模型会返回一个 JSON 字符串，
    #   对话框里会显示 `{"回答": "..."}` 这种东西。
    check("F.2 ★ ai.chat 的【代码】里没有 response_format（那是 explain 要的）",
          bool(chat_code) and "response_format" not in chat_code,
          f"chat 的代码里混进了 json_object（读到 {len(chat_code)} 字符）")

    # 正向对照：explain 那半【必须】还有。没有它的话，F.2 会因为
    # 「整个文件都不写 response_format」而永远绿 —— 那是装饰品。
    check("F.3 ai.explain 的【代码】里【有】 response_format（正向对照）",
          "response_format" in func_code(ai, "explain"),
          "explain 的 json_object 没了 —— F.2 可能是因为整个文件都没写")

    main = read_app("main.py")
    # ⚠ pywebview 每次 js_api 调用都新开一条【非守护】线程。等待锁会把它们
    #   攒起来，正好把 `os._exit(0)` 要绕开的问题搬回来（同 auto_poll 的告诫）。
    check("F.4 ★ 详情那把锁是 blocking=False（等待会把非守护线程攒起来）",
          "_chat_lock.acquire(blocking=False)" in main,
          "锁改成阻塞等待了")

    # ★ 缓存判断必须在【建会话】之前。反过来的话，缓存分支永远命不中 ——
    #   功能看着好好的，只是每次点都花钱（这条正是用户的拍板）。
    i_has = main.find("if self.chats.has(key):")
    i_start = main.find("self.chats.start(")
    check("F.5 ★★ detail_open 的缓存判断排在 start 之前",
          0 <= i_has < i_start, f"has 在 {i_has}、start 在 {i_start}")

    check("F.6 第一句只有 main.py 一个拼装点（别在前端再拼一份）",
          main.count("seed_question(") == 1,
          f"main.py 里出现了 {main.count('seed_question(')} 次")


# ══════════════════════════════════════════════════════════
# G. `_detail_turn` 那一层的【拼装】—— 「真·聊天」的落点
# ══════════════════════════════════════════════════════════
#
# ★★ 这一节 2026-09-21 补的，补的是一个真缺口：
#    A~F 验了第一句怎么拼（A）、历史怎么截（B）、会话表怎么存（C），
#    但**「多轮」这件事本身一处都没验过** —— 也就是
#    「第二轮发出去的东西里，到底有没有带上第一轮」。
#
#    这正是用户拍板「能自由追问（真·聊天）」那句话的落点。掉了的话：
#    界面上气泡一条不少、每条也都能滚出来，**只有模型那边每次都在重新
#    认识这个词** —— 用户只会觉得「它怎么老是忘」，然后怪模型不行。
#    这是一个不报错、只是答得不对的失败，正是本项目最不想留的那种。
#
# ⚠ `ai.chat` 在这一节里是【桩】。真正的 HTTP 那一段在
#   `AI接入联调.py`（假服务器，函数级）和 `AI端到端联调.py`
#   （假服务器 + 真窗口）验。这一节被测的是 `_detail_turn` 的
#   **拼装与记账**，不是它怎么发出去的 —— 不是「mock 掉被测对象」。
#
# ⚠ `Api.__new__` 绕开 `__init__`（同 `AI接入联调.py` 的做法）：
#   `__init__` 会起自动通道和热键线程、还会读写 settings。
#   所以下面必须**手动补上被测方法用到的每一个属性** ——
#   漏一个就是 AttributeError，而不是「静默用默认值」。

class _ChatStub:
    """顶替 `ai.chat`：把每次收到的上下文原样记下来，按剧本回答。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []          # 每次调用收到的 messages（深拷一份）
        self.systems = []       # 每次调用收到的 system prompt

    def __call__(self, api_key, system_prompt, msgs):
        self.sent.append([dict(m) for m in msgs])
        self.systems.append(system_prompt)
        if not self.replies:
            return {"ok": False, "msg": "（剧本用完了，这是测试自己的事）"}
        return dict(self.replies.pop(0))


def _ok(text):
    return {"ok": True, "text": text, "seconds": 1.0,
            "usage": {"in": 10, "out": 20, "total": 30}}


def _mk_api(chats):
    import main                                                    # noqa: E402
    api = main.Api.__new__(main.Api)
    api.settings = {"api_key": "sk-test", "system_prompt_detail": ""}
    api.chats = chats
    return api


def _roles(msgs):
    return "".join("u" if m["role"] == "user" else "a" for m in msgs)


def t_turn():
    print("\n▸ G. _detail_turn 的拼装（多轮到底有没有带上文）")

    import ai as ai_mod                                            # noqa: E402

    real_chat = ai_mod.chat
    try:
        _g_turn_body(ai_mod)
    finally:
        # ⚠ 一定要还回去。这个桩是【全局】的（改的是模块属性），
        #   漏还的话后面谁再调 ai.chat 都会拿到「剧本用完了」——
        #   而那时红的会是一条跟本节毫不相干的断言。
        ai_mod.chat = real_chat


def _g_turn_body(ai_mod):
    chats = dm.Chats()
    chats.start("rag", "RAG", "我们用 RAG 做检索。")
    api = _mk_api(chats)
    rec = _ChatStub([_ok("答一"), _ok("答二"), _ok("答三")])
    ai_mod.chat = rec

    first_q = dm.seed_question("RAG", "我们用 RAG 做检索。")
    r1 = api._detail_turn("rag", first_q, first=True)
    check("G.1 第一轮发出去的只有第一句那一条",
          len(rec.sent[0]) == 1 and rec.sent[0][0]["text"] == first_q,
          f"实际发了 {len(rec.sent[0])} 条：{_roles(rec.sent[0])}")

    ask2 = "它和微调有什么区别？"
    r2 = api._detail_turn("rag", ask2)
    # ★★ 本节的核心一条。
    check("G.2 ★★ 第二轮【带上了第一轮】：发出去的是「第一句+答一+追问」",
          _roles(rec.sent[1]) == "uau" and rec.sent[1][0]["text"] == first_q
          and rec.sent[1][1]["text"] == "答一" and rec.sent[1][2]["text"] == ask2,
          f"实际发了 {len(rec.sent[1])} 条：{_roles(rec.sent[1])}")

    ask3 = "举个例子"
    api._detail_turn("rag", ask3)
    check("G.3 ★ 第三轮带的是【全部】历史（不是只带上一轮）",
          _roles(rec.sent[2]) == "uauau" and len(rec.sent[2]) == 5,
          f"实际发了 {len(rec.sent[2])} 条：{_roles(rec.sent[2])}")

    # ⚠ 会话里每条只能有一份。多存一份的话，同一句话会在下一轮里
    #   出现两次 —— 模型看到自己把同一段话说两遍，token 也是双份。
    msgs = chats.get("rag")["msgs"]
    check("G.4 ★ 会话里每条只存一份（追加不是复制）",
          _roles(msgs) == "uauaua" and len(msgs) == 6,
          f"实际 {len(msgs)} 条：{_roles(msgs)}")

    # 前端画气泡用的就是这份 —— 它得和会话本身对得上。
    # ⚠ 这里比的是**当时**那一刻的会话（第二轮刚回来），不是现在的 ——
    #   写完这条之后又跑了第三轮，`chats` 已经长到 6 条了。
    check("G.5 返回给前端的 messages 就是这一轮的会话（顺序、条数都对）",
          r2.get("ok") and [m["text"] for m in (r2.get("messages") or [])]
          == [first_q, "答一", ask2, "答二"],
          f"前端拿到 {len(r2.get('messages') or [])} 条："
          f"{[m.get('text') for m in (r2.get('messages') or [])]}")

    # ── 失败那一轮：不许写进会话 ────────────────────────────────
    rec.replies.append({"ok": False, "msg": "余额不足"})
    before = list(chats.get("rag")["msgs"])
    r5 = api._detail_turn("rag", "这句会失败")
    # ⚠ 必须 `list(...)` 拷一份。`Chats.get()` 返回的是**那个列表本身**
    #   （它的 docstring 里写着「不是副本」），不拷的话 `after` 会随着
    #   后面那轮重试一起长大 —— 于是「重试发出的条数 == 失败后条数 + 1」
    #   这条判据会拿**长大之后**的数去比，7 != 8，红得莫名其妙。
    #   2026-09-21 就是这么红的，而它红的时候看着像产品多发了一条。
    after = list(chats.get("rag")["msgs"])

    check("G.6 ★ 失败原样返回（ok=False + 那句话带出来）",
          r5.get("ok") is False and "余额不足" in (r5.get("msg") or ""),
          f"实际返回 {r5!r}")
    # ★★ 这是用户看得见的那件事：失败了气泡不出现，**而会话里也没有多出一条**。
    #    两边只要有一边不一致，屏幕和会话就分家了 —— 下次重开这一段
    #    会少一条，看起来像丢消息。
    check("G.7 ★★ 失败的那一句【没有】被写进会话",
          after == before, f"失败前 {len(before)} 条、失败后 {len(after)} 条")

    # 失败之后接着聊：之前那几轮还得在（清会话的话，用户会突然
    # 发现前面聊的全没了，而界面上气泡还在）。
    rec.replies.append(_ok("答五"))
    api._detail_turn("rag", "重试这句")
    check("G.8 ★★ 失败【不清会话】：重试时前面几轮还在",
          len(rec.sent[-1]) == len(after) + 1
          and rec.sent[-1][0]["text"] == first_q,
          f"重试发出去 {len(rec.sent[-1])} 条：{_roles(rec.sent[-1])}")

    # ★★ 关键的一半：**那句失败的话，后面任何一轮都不该再出现在模型眼前**。
    #    只看「会话条数没变」是不够的 —— 把失败那句写进会话、再把条数
    #    硬掰回去的写法也能过 G.7，而这一条是从【模型看得见什么】验的。
    check("G.9 ★★ 那句失败的话没有混进后面的上下文",
          "这句会失败" not in [m["text"] for m in rec.sent[-1]],
          f"重试那轮发出去的：{[m['text'] for m in rec.sent[-1]]}")

    check("G.10 ★ 每一轮都用「详情」那份提示词（没串成框选那份）",
          len(set(rec.systems)) == 1 and rec.systems[0] == pm.DEFAULT_PROMPT_DETAIL,
          f"实际用了 {len(set(rec.systems))} 种，开头是 {rec.systems[0][:40]!r}")

    # ── 超上限：丢掉的轮数要一路透到返回载荷里 ──────────────────
    # build_context 的算法在 B 节逐条验过了，这里只验【接线】：
    # 它算出来的数有没有真的走到前端那句话里。
    big = dm.Chats()
    big.start("moe", "MoE")
    api2 = _mk_api(big)
    for i in range(dm.MAX_TURNS + 2):
        big.say("moe", "user", f"问{i}")
        big.say("moe", "assistant", f"答{i}")
    rec2 = _ChatStub([_ok("答新")])
    ai_mod.chat = rec2
    r = api2._detail_turn("moe", "再问一句")
    check("G.11 ★ 超上限时，丢掉的轮数透到了返回载荷里（前端那句话靠它）",
          r.get("ok") and r.get("dropped", 0) >= 1,
          f"实际 dropped={r.get('dropped')!r}")

    t_turn_variants(ai_mod, first_q)


def _naive_turn_no_history(ai_mod, api, key, ask):
    """写错的版本：每次只把【这一句】发出去，不带历史。

    ★ 这是最容易写出来的一种 —— 「用户问了什么就发什么」听上去完全合理，
      而且单轮测试全绿。第二句起才会露馅。
    """
    r = ai_mod.chat(api.settings.get("api_key"),
                    api._system_prompt("detail"),
                    [{"role": "user", "text": ask}])
    if not r.get("ok"):
        return r
    api.chats.say(key, "user", ask)
    api.chats.say(key, "assistant", r["text"])
    return {"ok": True, "messages": api._detail_wire(api.chats.get(key))}


def _naive_turn_say_first(ai_mod, api, key, ask):
    """写错的版本：还没确认成功，就先把这句记进会话。

    ★ 这一种更隐蔽：界面上的钩子（「先把用户那句显示出来」）很自然
      就会让人写在这里。后果是失败一次就多一条**没人回答的孤儿**，
      而下一轮模型会看到自己上一句没回话，很容易顺着往下编。
    """
    rec = api.chats.get(key)
    api.chats.say(key, "user", ask)                  # ← 记早了
    ctx, _ = dm.build_context(rec["msgs"])
    r = ai_mod.chat(api.settings.get("api_key"),
                    api._system_prompt("detail"), ctx)
    if not r.get("ok"):
        return r                                     # 孤儿留在了会话里
    api.chats.say(key, "assistant", r["text"])
    return {"ok": True, "messages": api._detail_wire(rec)}


def t_turn_variants(ai_mod, first_q):
    """G 那几条判据【够不够得着】—— 拿两个「写错了的实现」去撞。"""

    # → G.2 / G.3 够得着：不带上文的那个版本，第二轮只发 1 条。
    c = dm.Chats()
    c.start("rag", "RAG")
    api = _mk_api(c)
    stub = _ChatStub([_ok("答一"), _ok("答二")])
    ai_mod.chat = stub
    _naive_turn_no_history(ai_mod, api, "rag", first_q)
    _naive_turn_no_history(ai_mod, api, "rag", "追问二")
    check("G.12 ★★ G.2 够得着：不带历史的写法，第二轮还是只发 1 条",
          len(stub.sent[1]) == 1 and _roles(stub.sent[1]) == "u",
          f"错实现发了 {len(stub.sent[1])} 条：{_roles(stub.sent[1])}")

    # → G.7 / G.8 够得着：先记后发的那个版本，失败之后会话里多一条孤儿。
    c2 = dm.Chats()
    c2.start("rag", "RAG")
    api2 = _mk_api(c2)
    stub2 = _ChatStub([_ok("答一"), {"ok": False, "msg": "余额不足"}])
    ai_mod.chat = stub2
    _naive_turn_say_first(ai_mod, api2, "rag", first_q)
    n_before = len(c2.get("rag")["msgs"])
    _naive_turn_say_first(ai_mod, api2, "rag", "这句会失败")
    n_after = len(c2.get("rag")["msgs"])
    check("G.13 ★★ G.7 够得着：先记后发的写法，失败后会多出一条孤儿",
          n_after == n_before + 1 and _roles(c2.get("rag")["msgs"]).endswith("u"),
          f"失败前 {n_before} 条、失败后 {n_after} 条"
          f"（{_roles(c2.get('rag')['msgs'])}）")


# ══════════════════════════════════════════════════════════

def main():
    print("═" * 62)
    print("问了吗 · 详情对话联调（v0.30）")
    print("═" * 62)

    # ★ 先验尺子：`check()` 是这份脚本全部断言的唯一出口，它坏成
    #   「无条件记通过」的话整片一起变绿，而项数对账还照样通过。
    # ⚠️ 摆在任何 try 之外（老守门员那条规矩：守门断言不能和被守的代码
    #    套在同一个 try 里 —— 前者抛异常时后者跟着缺席，汇总照样打绿的）。
    if not 尺子.gate(check, *尺子.list_snapshot(PASS, FAIL), label="详情联调"):
        return 2

    t_seed()
    t_context()
    t_chats()
    t_variants()
    t_prompt()
    t_single_source()
    t_turn()

    print("\n" + "═" * 62)
    print(f"通过 {len(PASS)} 项、不通过 {len(FAIL)} 项")
    if FAIL:
        print("\n失败明细：")
        for name, detail in FAIL:
            print(f"  · {name}　→ {detail}")

    print("""
⚠ 本文件【三条验不了的】，别当成验过了：
  · 「对话框盖在面板上、不动窗口几何」—— 这里一个窗口都没有。
  · 「第二次点真的没花钱」—— 缓存决策在 Api.detail_open 里，
    判据是【假服务器收到几次请求】，在 AI端到端联调 那边。
    这里只验到「Chats.start 不重置」这一层机制。
  · 「模型真的照着第三份提示词答」—— 提示词服从度一贯测不了，
    要等用户拿真 Key 跑（会产生真费用）。
""")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
