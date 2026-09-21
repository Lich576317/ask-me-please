# -*- coding: utf-8 -*-
"""③ 知识库 端到端联调 —— 真开窗口、真点击，一路验到「省了一次模型调用」。

    python "Desktop\\问了吗\\冒烟测试\\知识库端到端联调.py"
    跑之前请把别的「问了吗」关掉 —— 脚本会自己起一个干净实例。

────────────────────────────────────────────────────────
和 `知识库联调.py` 的分工：

    那个测的是 kb.py 这个【模块】对不对（73 项，纯函数 + 临时库，几秒跑完）
    这个测的是【整条链路】通不通：按钮 → js_api → ③ → ② → 前端重绘 → 落库 → 导出

单测全绿也不代表用户点得到东西 —— v0.6 那个「框选完卡片不更新」的 bug，
单测就是绿的。所以这一步必须真开窗口、真点、真截图看。

**最有力的一条断言**：先往库里塞一个词（RAG），策略设 dedupe，再跑管线 ——
然后把假服务器收到的 user message 拆成两段，断言
【候选词那一段里没有 RAG，而原文那一段里有】。

⚠ 这里有个坑，写清楚免得以后有人把它「修」回错的写法：

  user message 是两段拼的（见 ai.build_messages）——
      【笔记原文】……                 ← 整篇 OCR 原文，照发
      【规则初筛捞出的英文候选词】……   ← 只放要问的那批

  原文里【一定】还有 RAG，因为原文是整篇发过去的，模型要靠它理解上下文。
  省钱省在【候选词那一段】：库里已有的词不进候选表，模型就不会为它再写一遍解释。
  所以断言必须切开来看。笼统地搜全文的话，这条永远红 —— 而它红了并不代表
  知识库没生效，只代表断言写错了。这个脚本第一版就是那么写的。

────────────────────────────────────────────────────────
这个脚本【两样用户的东西都不碰】：

  · 库 —— WENLEMA_KB 指到临时文件，程序从头到尾用的是它自己那个空库
  · settings —— WENLEMA_SETTINGS 指到 `_e2e_settings.json`，**真文件不被打开**

⚠ 这里原来写的是「settings.json 还是会临时改写，跑完还原（用 move 不用 copy，
  强杀的话会留个 .e2ebak 在原地看着）」。那套做法 2026-09-15 真出事了：
  用户的真 Key 被覆盖成了假 Key，而报告还同时打着「✓ 数据安全」。
  查不出是谁写的 —— **查不出就别查了，改成让它没得写**。
  怎么写的坏、换成了什么，见 `框选联调.py` 里 SETTINGS 上面那段长注释，
  以及 `进展文档.md` 附录 J。
"""
import ctypes
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

# ⚠ 输出重定向到管道/文件时，Python 默认用本地编码 GBK。
#   本脚本开头那个 `⓪`（U+24EA）GBK 里【没有】—— 而 ①~⑩ 有。
#   所以只有它会把 print 直接炸掉，症状还是「跑到一半 UnicodeEncodeError」。
#   同一个坑 main.py 开头也踩过，那里改的就是这一句。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 探针必须自己 DPI 感知

import win32api                                                   # noqa: E402
import win32con                                                   # noqa: E402
import win32gui                                                   # noqa: E402
import win32process                                               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
APPLOG = os.path.join(HERE, "_app_kb.log")
# ⚠ settings 的路径【不在这里定义】—— 唯一实现处是 `框选联调.py` 的 SETTINGS，
#   下面 `BOX = load(...)` 之后直接取 `BOX.SETTINGS`。
#   联调一律写那份临时文件，`app\settings.json`（里面是用户的真 Key）全程不打开。
#   为什么不许碰它，见 `框选联调.py` 里 SETTINGS 上面那段长注释。
KB_DB = os.path.join(HERE, "_e2e_kb.db")        # 临时库。用户的 app\kb.db 全程不动
OUT_DIR = os.path.join(HERE, "_e2e_out")        # 导出落到这里，不碰用户的「文档」

sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import capture                                                    # noqa: E402
import kb as kb_mod                                               # noqa: E402


def load(name, filename):
    """按文件路径加载同目录的脚本 —— 文件名是中文，不适合当模块名。"""
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


BOX = load("boxlink", "框选联调.py")      # 借它的窗口/点击/OCR 工具

SETTINGS = BOX.SETTINGS                   # ← 联调专用的临时 settings（见文件开头）
AIT = load("aitest", "AI接入联调.py")     # 借它的假 DeepSeek 外壳

# ── 这一轮要验的三样东西 ──────────────────────────────────
SEED_TERM = "RAG"
SEED_EXPL = ("RAGSEED 检索增强生成：先从外部资料里查出相关内容，"
             "再让模型基于这些内容回答。")
EXCLUDE_TERM = "Chunking"

# ⑧ 里从【操作台】手打进屏蔽词框的那个词。
# ⚠ 必须是示例笔记里【没有】的词：它会被真的排进排除名单，
#   而 ⑩ 要断言第 2 轮的候选词，混进一个笔记里的词就把那几条搅了。
#   本节跑完会把它恢复掉，不留痕迹。
NEW_BLK_TERM = "OKR"

# 所有针都对得上的独特字符串。挑选原则：
#   · RAGSEED  全大写 ASCII，OCR 认得稳，且只可能来自库里的那条解释
#   · 来自知识库 只出现在来源标记那个 pill 上（种子解释里【故意】不含这四个字，
#     含了的话这条断言就变成自己找自己，永远绿）
MARK_KB = "来自知识库"
MARK_SEED = "RAGSEED"

AI_DELAY = 1.0        # 假服务器的思考时间。不用像 ② 那个脚本一样留 4 秒 ——
                      # 这里不需要截「正在问模型」，断言全在日志和数据库上

ROUND = [1]           # 第几轮提问。假服务器靠它把前后两次的回答区分开

# ── 跑到哪了 ──────────────────────────────────────────────
# ⚠ `run()` 里有二十几个提前 `return`（几乎每一步都有一句
#   `if not BOX.check(...): return`）。它们【一个字都不打】就交卷，
#   而 main() 照样打「通过 N 项，不通过 M 项」—— 读起来就是一份跑完的报告。
#
#   ★ v0.20 的回归里正是这个形状：⑧ 里一句 `if NEW_BLK_TERM not in after: return`
#     把 ⑨⑩ 共 45 项【整段跳过】，而脚本自己报的是「通过 24 项，不通过 4 项」。
#     抓出它的不是脚本，是 `全部回归.py` 那句「⚠ 期望 69 项，实到 24 项」——
#     单独跑这个脚本的人，看到的是一份完整的绿报告。
#
#   ★ 本文件 main() 里已经写过这句话：「截断的一份报成『通过 N 项』就是在撒谎」。
#     当时只防住了【锁屏】那一种截断 —— 而「没跑完」跟它是因为什么停的无关。
#
#   所以：每节开头走 step() 记下跑到哪，run() 最后一行置 FINISHED。
#   main() 一看 FINISHED 没置上，就知道是中途停的，并且说得出停在哪儿 ——
#   「前 24/69 项跑过，停在 ⑧」比「整段没跑」有用得多，也比「通过 24 项」诚实。
WHERE = ["（还没开始）"]
FINISHED = [False]


def step(title):
    """打一节的小标题，并记下跑到哪了（给 main() 那句「截断了」用）。"""
    WHERE[0] = title.strip()
    print("\n" + title)


# ══════════════════════════════════════════════════════════
# 假 DeepSeek：按收到的候选词现编解释
# ══════════════════════════════════════════════════════════

CAND_MARK = "【规则初筛捞出的英文候选词】"


def cands_of(user_msg):
    """从 user message 里把【候选词那一段】抠出来。

    ⚠ 这正是本脚本的核心工具，不是顺手写的辅助函数。
      原文段和候选段必须分开看，理由见文件头。

    ⚠ 每一项都要 strip()。标记后面的第一个词前面顶着一个换行 ——
      不 strip 的话拿到的是 '\\nRAG'，而断言写的是 `c == "RAG"`，
      于是「repeat 模式绕过了库」这一条会红，但库里代码一点问题都没有。
      第一轮的 `"RAG" not in cands` 靠运气蒙对（'\\nChunking' 本来也不等于 'RAG'），
      第二轮用 == 一比就露馅了 —— 一个只在一半情况下成立的比较，
      比一个明摆着错的比较更费时间。
    """
    if CAND_MARK not in (user_msg or ""):
        return []
    return [t.strip() for t in user_msg.split(CAND_MARK, 1)[1].split("、") if t.strip()]


class EchoFake(AIT.Fake):
    """假 DeepSeek：收到的候选词有哪些，就一条不少地解释哪些。

    ⚠ 为什么不学 `AI端到端联调.py` 那样写死一条回答：
      那个脚本只验「② 通了没有」，答什么都行。
      这个脚本要拿模型的【输入】和【输出】两个方向对账 ——
      写死一条的话，条目数和候选数对不上，入库 / 收纳历史 / 导出
      三处的断言就全部失去意义了。按候选现编，假服务器的行为才和真模型一致。
    """

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            AIT.GOT["last"] = json.loads(raw)
        except Exception:
            AIT.GOT["last"] = {"__unparsed__": raw}
        AIT.GOT["auth"] = self.headers.get("Authorization")
        AIT.GOT["hits"] += 1

        msgs = (AIT.GOT["last"] or {}).get("messages") or []
        user = msgs[1].get("content", "") if len(msgs) > 1 else ""
        items = [{"术语": t,
                  "解释": f"第 {ROUND[0]} 轮解释：{t} 是这段笔记里的一个英文术语。"}
                 for t in cands_of(user)]

        time.sleep(AI_DELAY)
        content = json.dumps({"条目": items}, ensure_ascii=False)
        self._send(200, json.dumps(AIT._body(content), ensure_ascii=False))


# ══════════════════════════════════════════════════════════
# 屏幕工具：截图 + OCR 找字
# ══════════════════════════════════════════════════════════

def _engine():
    """和 `框选联调.py` 共用同一个 OCR 引擎实例。

    ⚠ 建引擎要 0.7 秒，项目里已经立了「引擎只建一次」的规矩。
      另起一个的话，这个脚本一跑就要多花近一秒，还占一份内存。
    """
    if BOX._engine is None:
        from rapidocr import RapidOCR
        BOX._engine = RapidOCR()
    return BOX._engine


def _pairs(res):
    """OCR 结果 → [(文本, 框)]。

    ⚠ 别写 `res.boxes or []`。boxes 是 numpy 数组，
      对它做真值判断会抛「truth value of an array is ambiguous」——
      而且只在某些版本上抛，看起来像随机失败。
    """
    txts = getattr(res, "txts", None)
    boxes = getattr(res, "boxes", None)
    if txts is None or boxes is None:
        return []
    return list(zip(list(txts), list(boxes)))


# ⚠ 这个异常【不在这里定义】—— 唯一实现处是 `框选联调.py`（四个开窗脚本
#   共用它），同 `screen_locked()` 的道理：各写一份的话，哪天判据改了，
#   这边会【静静地】按老规矩判，不报错。
ScreenLocked = BOX.ScreenLocked


def grab_scaled(rect, path, scale=1):
    """截一块屏幕存成 PNG，可以先放大。

    ⚠ 为什么要放大：面板只有 520px 宽（v0.11 起；再往前是 380），
      来源标记 pill 和分段按钮的字是
      11px CSS —— 本机 150% 缩放下才 16px 物理。RapidOCR 在这个尺寸上会
      【漏字】：不是没渲染，是没认出来。
      放大两倍再喂给它，同一张图立刻就认得出。
      用 np.repeat（最近邻）而不是插值 —— 字是黑白的，插值只会把它糊掉。
    """
    import mss
    from mss import tools as mss_tools
    from mss.exception import ScreenShotError

    # ★ 截之前先问一句「现在是不是锁屏界面」。
    #
    #   锁屏有【两种】翻车方式，2026-09-15 晚在真机上把两种都验了：
    #
    #     ① 锁屏【转换中】→ BitBlt 直接失败，抛 ScreenShotError: 拒绝访问
    #        就是 18:16 崩掉那一下
    #     ② 锁屏【稳住之后】→ BitBlt 照样成功，截到的是锁屏壁纸
    #        实测三个矩形（含 2400,100 那块副屏）全部截到真实内容，
    #        std 30~58、上万种颜色，不是黑屏 —— 这一种更阴：
    #        【它不报错】，只是后面每一条「面板上没有这几个字」都红给你看，
    #        而它们报的原因全是错的。
    #
    #   这一句挡的是 ② 和「① 的检查间隙」，下面那个 except 兜 ① 本身。
    #   两种都必须收口成 ScreenLocked，不然回归读到的还是些指错方向的失败。
    #
    #   ⚠ 这里【不能】只靠下面那个 except：屏幕锁稳之后根本不抛异常。
    #     `require_unlocked()` 挡的正是 ② 这一类，但它只在【开局】查一次 ——
    #     跑到一半才锁的，从前没有任何人管。
    if BOX.screen_locked():
        raise ScreenLocked()

    # ⚠ 是 mss.MSS()，不是已废弃的 mss.mss()（CLAUDE.md 里点过名的那条）
    try:
        with mss.MSS() as sct:
            img = sct.grab({"left": rect[0], "top": rect[1],
                            "width": rect[2], "height": rect[3]})
    except ScreenShotError as e:
        # ★ 就在上面那句话之后、真正截屏之前，屏幕被锁掉了（或者正在锁）。
        #   不接这一下的话，整个脚本会以一个 mss 的 traceback 崩掉 ——
        #   于是 `全部回归.py` 读不到汇总行，把这一份报成「❌ 通过 0」，
        #   看着像产品炸了，其实只是屏幕锁了、什么都没验成。
        #   实踩过（2026-09-15 18:16）：人走开锁屏，四个开窗脚本里
        #   三个拒跑（⏸），这一个崩在半路报成 ❌ —— 同一件事两种报法。
        #
        #   判据用 BOX.confirm_lock()，**不是**当场查一次 BOX.screen_locked()：
        #   这两个判据在【锁屏切换那一小段】里不一致 —— 截屏已经拒绝了，
        #   而 screen_locked() 还说着「没锁」，只查一次就漏。
        #   实测 2026-09-16 00:24 正是这么崩的（这一整段注释当时以为已经修好了）。
        #   唯一实现处在 `框选联调.confirm_lock()`，那边连着两次实测的记录。
        #
        #   也不是「抛了这个异常就算锁屏」：没锁屏却截不到，
        #   那是真问题，要原样抛出去让人看见。
        if BOX.confirm_lock():
            raise ScreenLocked() from e
        raise
    if scale == 1:
        mss_tools.to_png(img.rgb, img.size, output=path)
        return path

    import numpy as np
    a = np.frombuffer(img.rgb, dtype="uint8").reshape(img.height, img.width, 3)
    a = np.repeat(np.repeat(a, scale, axis=0), scale, axis=1)
    mss_tools.to_png(a.tobytes(), (a.shape[1], a.shape[0]), output=path)
    return path


BAND = 400          # 分段扫描的段高（物理像素）


def scan(rect, needles, tag="panel"):
    """一次扫描，把这一屏要找的词【一起】找出来。返回 {词: 屏幕物理坐标}。

    ⚠ 分开找的话，每找一个词都要重新截图 + 跑一遍 OCR，放大那轮尤其慢。
      一次扫描把同一屏要找的词全找出来，是这里唯一值得做的优化。

    四趟，一趟比一趟贵，找齐了就不往下走：
      ① 整条 x1　② 整条 x2　③ 横切成 400 高的段 x1　④ 同样分段 x2

    ⚠ ③④ 这两趟不是「再放大一点」的重复劳动，它们解决的是另一件事：
      RapidOCR 的检测头按【长边】缩放。面板是 520×1600 的一条，
      长边 1600 会被压回 960 —— 也就是【整条一起扫的时候，字反而被缩小了】。
      而这恰恰是最小的那批字（11px CSS = 16px 物理）最吃亏的地方。
      横切成 400 高的段之后，同一条长边规则会把它【放大】2.4 倍。
      实测：「导出 md」这个按钮在整条 x1、x2 两趟里都读不出来，
      切段之后立刻就有了 —— 漏的不是某个词，是「又窄又长」这种截法本身。
      所以这两趟只在真的还缺词时才跑。
    """
    found = {}
    seen = []          # 这一屏 OCR 认出来的【全部】文字，只给下面的留证用
    for kind, sc in (("整条", 1), ("整条", 2), ("分段", 1), ("分段", 2)):
        rest = [n for n in needles if n not in found]
        if not rest:
            break
        if kind == "整条":
            pieces = [(rect, f"_e2e_{tag}{sc}.png")]
        else:
            pieces = [((rect[0], rect[1] + t, rect[2], min(BAND, rect[3] - t)),
                       f"_e2e_{tag}b{t // BAND}{sc}.png")
                      for t in range(0, rect[3], BAND)]
        for piece, path in pieces:
            grab_scaled(piece, path, sc)
            res = _engine()(path)
            for t, b in _pairs(res):
                seen.append(f"[{kind}x{sc}] {t}")
                flat = t.replace(" ", "")
                for n in rest:
                    if n in flat and n not in found:
                        xs = [q[0] for q in b]
                        ys = [q[1] for q in b]
                        found[n] = (piece[0] + int(sum(xs) / len(xs) / sc),
                                    piece[1] + int(sum(ys) / len(ys) / sc))

    # ⚠ 没找齐的话，把这一屏 OCR 认出来的全部文字落一份【纯文本】。
    #   为什么非留不可：PNG 会被下一轮同名覆盖，txt 不会 ——
    #   v0.8 第二轮那 3 条不通过就是这么查不回来的（截图被重跑盖掉了）。
    #   留着它，下次红了至少能看出「是面板真的没这句话，还是 OCR 读岔了」，
    #   这两件事的修法完全不同。
    missing = [n for n in needles if n not in found]
    if missing:
        dump = os.path.join(HERE, f"_e2e_{tag}_ocr.txt")
        try:
            with open(dump, "w", encoding="utf-8") as f:
                f.write(f"# scan(tag={tag}) 这一屏没找齐：{missing}\n")
                f.write(f"# 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# rect={rect}\n\n")
                f.write("\n".join(seen))
            print(f"       ⚠ 没找齐 {missing} —— 这屏的 OCR 原文在 {dump}")
        except Exception as e:                       # 留证失败不该拖垮测试
            print(f"       ⚠ 缺词 {missing}，且原文没落下：{e}")
    return found


def right_click(pos):
    """右键。框选联调.py 里只有左键，这里补一个 —— 不动那个已经全绿的文件。"""
    win32api.SetCursorPos(pos)
    time.sleep(0.2)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
    time.sleep(0.4)


# ══════════════════════════════════════════════════════════
# 等效果、点到生效
# ══════════════════════════════════════════════════════════

def wait_for(ok, timeout, tick=0.2):
    """反复问 ok() 直到它说是，或者超时。返回最后一次的结果。

    ⚠ 判据一律用【可验证的副作用】—— 设置里落了盘没有、日志多了一条没有、
      库里的行变没变，而不是「按钮看着变色了」。OCR 会看走眼，副作用不会。
    """
    deadline = time.time() + timeout
    while True:
        if ok():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(tick)


def read_setting(key):
    """读一眼程序写出来的设置，读不到就当 None（文件可能正被写到一半）。"""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f).get(key)
    except Exception:
        return None


def click_until(panel, pos, ok, what, tries=3):
    """点一个按钮，直到 ok() 说它生效了。返回第几次才成（0 = 一直没成）。

    ⚠ 为什么不能只点一下：窗口不在前台时，**第一次点击会被 Windows 吃掉** ——
      它只用来把窗口激活，不往下传给 WebView2，于是按钮像没反应一样。
      `框选联调.py` 的 activate() 就是为这件事写的，但它只做到
      「点之前先把窗口提到前台」，挡不住「提到了还是被吃掉」。

      实测：v0.8 这一轮里，「可重复解释」和「收纳历史」两个按钮各点了一次、
      各自毫无反应（截图逐像素比对，界面连 hover 态都没变），
      而同一轮的「立即识别」一次就成。所以判据不能是「我点过了」。

    ⚠ ok 自己负责「等多久再下结论」。切策略是本地写个 json，几十毫秒；
      跑一轮管线要三秒多。如果在 click_until 里统一 sleep，快的那类白等，
      慢的那类又会因为等不够被判成「没生效」，于是点第二遍 ——
      第 1 轮点出两条 [管线]，后面 AIT.GOT 的调用次数就全对不上了。
    """
    for i in range(1, tries + 1):
        BOX.activate(panel)
        BOX.click(pos)
        if ok():
            if i > 1:
                print(f"       （「{what}」点到第 {i} 次才生效 ——"
                      f" 前 {i - 1} 次被窗口激活吃掉了）")
            return i
    return 0


def note_visible(tries=8, shot=None):
    """示例笔记此刻还在框选区域里吗。

    ⚠ 这是【环境前置检查】，不是功能断言。
      框选区域是一块屏幕坐标，任何盖在它上面的窗口都会被一起认进去。
      不用这个查出来，「笔记被盖住」就会伪装成
      「★ 候选词段里没有 RAG」那种功能断言失败 —— 环境事故顶着
      代码 bug 的名字出现，是最费时间的一种。
      `框选联调.py` 里记着一次真事故：某一轮识别出的是别人屏幕上
      一篇讲数字人直播的文章，候选词全是 OCR 噪声。

    ⚠ ⓪ 和第 2 轮之前各要一次。第 1 轮到第 2 轮之间隔了右键、弹提示、
      切策略好几次点击，几十秒里那块区域是可能被压住的 —— 实测发生过。
    """
    shot = shot or os.path.join(HERE, "_kb_note.png")
    for _ in range(tries):
        BOX.grab(BOX.NOTE_RECT, shot)
        if BOX.ocr_find(shot, "Chunking", (0, 0)):
            return True
        time.sleep(0.6)
    return False


# ══════════════════════════════════════════════════════════
# 操作台：进去、出来（v0.11 新增）
# ══════════════════════════════════════════════════════════

# ⚠ v0.11 把「可重复解释 / 导出目录 / 收纳历史 / 导出 md」全搬进了操作台，
#   默认是 hidden 的。不先点齿轮进去，scan() 在这一屏上永远找不到它们 ——
#   而失败的样子是「界面上找不到『可重复解释』」，看起来像按钮没了，
#   其实是【这一页不是那一页】。这种红法最难查，所以进出都做成显式的一步。
#
# 齿轮的标签是「设置」两个字，不是只有一个 ⚙ —— 因为 OCR 认不出符号。
# 这不是排版偏好，是「点得到它」这件事必须可验证。
TOP_STRIP = 300         # 标题栏 + 操作台头部都在这条里。
                        # ⚠ 找齿轮【只扫这一条】，不扫整个面板：
                        #   操作台里有一张卡叫「知识库设置」，含「设置」二字 ——
                        #   扫全屏的话，进来之后找「设置」会撞上它。
                        #   只扫 300 高就不会，因为那张卡在下面。


def console_top(panel_rect, notches=25):
    """把操作台滚回【最顶上】。

    ⚠ 为什么非要有这一步：`#view-console` **本身就是那个 .scroll 容器**，
      两个视图是用 `hidden` 切的、不重建 DOM —— 所以**滚动位置会留着**。
      上一节为了够到下面的卡片往下滚了，下一节 `open_console()` 进来时
      它还停在老地方：`返回条目` 被滚出顶栏、上面几张卡也全在屏幕外。
      于是红出来的是「操作台上找得到『返回条目』」/「操作台里找到
      『可重复解释』」—— 每一条的原因都是错的，真因是【视口没归位】。

    ⚠ 往上滚到底【不需要】知道滚过了多少：滚到顶之后再滚，浏览器就不动了。
      所以这里可以放心地多滚（幂等），不用像往下找那样一格一格试。

    ⚠ 光标摆在左边距上，不摆正中间：正中间可能落在 `<textarea>` 上，
      那一下滚轮滚的是那个框、不是整页（System Prompt 展开时就会）。
    """
    BOX.wheel((panel_rect[0] + 24, panel_rect[1] + panel_rect[3] // 2), notches)
    time.sleep(0.35)


def scan_down(panel_rect, needles, tag, tries=8, notches=-4):
    """在操作台里找 needles —— 一屏找不到就往下滚一点再找。

    ⚠ 为什么要滚：`grab()` 只截【当前视口】。v0.20 往操作台加了一张
      「快捷键取词」卡片，正好排在「屏蔽词」上面 —— 屏就那么大，
      下面那张卡就被顶到折线以下了。**截图里没有它，而它其实好好渲染着。**
      不滚的话，这条断言会顶着「功能坏了」的名字红，实际只是它在屏幕外 ——
      而这两件事的修法完全不同（同 `框选联调.wheel` 的说明）。

      ★ 这不是理论：v0.20 的回归里 ⑧ 就是这么红的（4 条），
        当时的诊断结论是「屏蔽词的增删功能坏了」。本项目的原话 ——
        **先分清「功能坏了」还是「它在折线以下屏幕外」，再动手查。**

    ⚠ 只往下滚，所以调用它的顺序必须和卡片在流里的顺序一致。
      一次 4 格 ≈ 12 行，远小于面板高度，不会一步跨过整张卡。

    ⚠ 每次 `scan()` 最多跑四趟（整条 x1/x2 + 分段 x1/x2），找不到就白跑四趟 ——
      所以这里只放【同一张卡上】的针，别拿它当通用扫描器使。
    """
    pos = (panel_rect[0] + 24, panel_rect[1] + panel_rect[3] // 2)
    got = {}
    for i in range(tries):
        got = scan(panel_rect, needles, tag=f"{tag}s{i}" if i else tag)
        if all(n in got for n in needles):
            if i:
                print(f"       （{tag} 往下滚了 {i} 格才进视口 —— 不是坏了）")
            return got
        if i < tries - 1:
            BOX.wheel(pos, notches)
            time.sleep(0.45)
    return got


def open_console(panel, panel_rect, box, tries=3):
    """点标题栏齿轮进操作台。

    ⚠ 进来之后【把滚动位置归零】—— 见 `console_top()`。
      不加这一步，⑧ 为了够到「屏蔽词」往下滚过之后，
      ⑨ 再进来找「可重复解释」就会扑空，而它报的是「按钮没了」。
    """
    strip = (panel_rect[0], panel_rect[1], panel_rect[2], TOP_STRIP)
    hit = scan(strip, ["设置"], tag="gear")
    if not box.check("标题栏上找到齿轮「设置」", "设置" in hit,
                     unless("设置" in hit, "截图：冒烟测试\\_e2e_gear1.png")):
        return False

    ok = False
    for i in range(tries):
        box.activate(panel)
        box.click(hit["设置"])
        time.sleep(0.7)
        if "返回条目" in scan(strip, ["返回条目"], tag="console"):
            ok = True
            if i:
                print(f"       （齿轮点到第 {i + 1} 次才生效 ——"
                      f" 前 {i} 次被窗口激活吃掉了）")
            break
    ok = box.check("点了齿轮，操作台出来了", ok,
                   unless(ok, "点了 3 次都没进去"))
    if ok:
        console_top(panel_rect)
    return ok


def back_to_stream(panel, panel_rect, box, tries=3):
    """点「返回条目」回条目页。

    ⚠ 每次进操作台办完事【都要回来】：条目页上那些是动作（立即识别），
      操作台里那些是配置。切回来还有一层验证价值 ——
      两页是 hidden 互斥的，回得来就说明切换没把哪一页卡死。

    ⚠ 先 `console_top()` 再找按钮：「返回条目」在 `.console-head` 里，
      而它在**滚动容器内部** —— 上一节往下滚过了的话，它已经被滚出顶栏，
      这一条会红成「操作台上找得到『返回条目』」。真因是滚动位置，不是按钮。
    """
    console_top(panel_rect)
    strip = (panel_rect[0], panel_rect[1], panel_rect[2], TOP_STRIP)
    ok = False
    for i in range(tries):
        hit = scan(strip, ["返回条目"], tag="back")
        if "返回条目" not in hit:
            return box.check("操作台上找得到「返回条目」", False,
                             "截图：冒烟测试\\_e2e_back1.png")
        box.activate(panel)
        box.click(hit["返回条目"])
        time.sleep(0.7)
        if "立即识别" in scan(panel_rect, ["立即识别"], tag="stream"):
            ok = True
            break
    return box.check("点「返回条目」回到了条目页", ok,
                     unless(ok, "点了 3 次都没回去"))


# ══════════════════════════════════════════════════════════
# 用户的文件：先挪走，跑完挪回来
# ══════════════════════════════════════════════════════════

# ⚠ 这里原来有一对 stash() / unstash()，2026-09-15 删掉了。
#   它们把用户的 `settings.json` 挪成 `settings.json.e2ebak`、跑完再挪回来 ——
#   而「挪走 → 跑 → 挪回」是个【承诺】不是机制：有一轮 287 项全绿，
#   用户的真 Key 却没了（settings.json 停在脚本播下去的假 Key 上），
#   而且事后单个跑 / 连着跑 / 再跑一遍全部回归，都没能复现。
#   现在整份写在 `_e2e_settings.json` 上（见 SETTINGS），没有原件要保，
#   也就没有「挪回来」这一步可失败。查不出来的竞态，最好的处理方式
#   是不让它有东西可竞。

# ══════════════════════════════════════════════════════════
# 播种：库、设置、示例笔记
# ══════════════════════════════════════════════════════════

def seed_kb():
    """建一个全新的库，里面只有一条 RAG。

    ⚠ 表结构走 kb._migrate()，不是在这里手抄一份 CREATE TABLE。
      抄一份的话，哪天 schema 改了，这个测试会拿一套【过时的表】去喂程序，
      然后以一个极难看懂的方式失败。
    """
    for p in (KB_DB, KB_DB + "-wal", KB_DB + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    now = int(time.time())
    c = sqlite3.connect(KB_DB)
    try:
        kb_mod._migrate(c)
        c.execute("INSERT INTO terms"
                  "(key, term, explanation, source, created_at, updated_at, last_used_at)"
                  " VALUES(?,?,?,?,?,?,?)",
                  (kb_mod.canon(SEED_TERM), SEED_TERM, SEED_EXPL, "ai", now, now, now))
        c.commit()
    finally:
        c.close()


def seed_settings():
    """种一份「装好就能跑」的联调设置：已有区域、已有假 Key、目录指向临时目录。

    ⚠ kb_dir 必须指向临时目录。不指的话，导出会落进
      `~/Documents/问了吗` —— 测试往用户的文档目录里丢文件，越界了。
    ⚠ 种的是 `_e2e_settings.json`，不是 `app\\settings.json`（用户的真 Key 在那份里）。
      整份重写，不需要先把原件挪走。
    """
    note = BOX.NOTE_RECT
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump({
            "api_key": "sk-test-fake",        # 假的。真 Key 由用户自己填
            "kb_policy": "dedupe",
            "kb_dir": OUT_DIR,
            "system_prompt": "",              # 空 = 用后端默认提示词
            "seen_notice": True,              # 别让首用告知盖住界面
            "region": [note[0], note[1], note[2], note[3]],
            "auto_on": False,
        }, f, ensure_ascii=False, indent=2)


def db(sql, args=()):
    """直接读程序那个库来对账。

    ⚠ 这是本脚本第二可靠的证据来源（第一是 user message）。
      界面上「库中 N 条」是给人看的，OCR 读数字会看走眼；
      直接问数据库不会。WAL 模式下另一个进程读写互不阻塞，安全。
    """
    c = sqlite3.connect(KB_DB, timeout=5)
    try:
        c.row_factory = sqlite3.Row
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


# ══════════════════════════════════════════════════════════
# 日志
# ══════════════════════════════════════════════════════════

def log_lines(needle):
    out = []
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if needle in ln:
                    out.append(ln.strip())
    except FileNotFoundError:
        pass
    return out


def wait_log(needle, count, timeout=60):
    """等日志里第 count 条 needle 出现。返回全部匹配行。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = log_lines(needle)
        if len(lines) >= count:
            return lines
        time.sleep(0.4)
    return log_lines(needle)


def unless(cond, text):
    """过了就不打印的说明文字。

    ⚠ `框选联调.py` 的 check() 是共用工具，detail【通过也照打】
      （另外两个脚本还在用它，不去动）。于是像
      「把挡住的窗口挪开再跑」这种话，会在 [通过] 那一行后面跟着出现 ——
      看报告的人得先自己分辨「这句到底算不算数」。
      凡是【只对失败有意义】的说明，都从这里过一道。
    """
    return "" if cond else text


def hint(cond):
    """只在【失败】时把日志尾部打出来，返回空串当 detail。

    ⚠ 别学 `AI端到端联调.py` 里 `check(..., dump_hint())` 那个写法 ——
      Python 的实参是【先求值】的，那一行等于每一项都把日志倒一遍，
      不管过没过。通过的项混在日志堆里，一眼看不出到底红了几条。
    """
    if not cond:
        dump_hint()
    return ""


def dump_hint():
    print("       --- app 日志尾部 ---")
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            for ln in f.read().splitlines()[-25:]:
                if ln.strip() and "rapidocr" not in ln.lower():
                    print("       | " + ln)
    except FileNotFoundError:
        print("       | （日志文件不存在）")


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

def main():
    print("=" * 62)
    print("问了吗 · ③ 知识库 端到端联调（假 DeepSeek，不联网不花钱）")
    print("=" * 62)
    if not BOX.require_clean_screen():
        return 1
    # ★ 先验尺子：`BOX.check` 是全部断言的唯一出口，它坏了几百项一起变绿。
    if not BOX.gate_check_selftest():
        return 2

    srv = ThreadingHTTPServer(("127.0.0.1", 0), EchoFake)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"假 DeepSeek 起在 127.0.0.1:{port}")

    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    seed_settings()
    seed_kb()
    open(APPLOG, "wb").close()

    # 示例笔记。⚠ 自己写一份，不要去指望 `框选联调.py` 上次跑过 ——
    #   那个脚本 main() 里才写这个文件，这里直接借 start_note() 的话，
    #   没跑过框选联调就会以一个「记事本窗口打不开」的方式失败。
    with open(BOX.SAMPLE, "w", encoding="utf-8") as f:
        f.write(BOX.NOTE_TEXT)

    note = BOX.start_note()
    time.sleep(2.0)

    # ⚠ WENLEMA_SETTINGS：和 WENLEMA_KB / WENLEMA_BASE_URL 一个性质 ——
    #   留给联调脚本的接口，不是给用户的开关。有它，app 才不去读
    #   `app\settings.json`（用户的真 Key 就在那份里）。
    env = dict(os.environ,
               WENLEMA_BASE_URL=f"http://127.0.0.1:{port}",
               WENLEMA_KB=KB_DB,
               WENLEMA_SETTINGS=SETTINGS)
    log = open(APPLOG, "wb")
    app = subprocess.Popen([sys.executable, "main.py"], cwd=APP, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    print(f"app pid = {app.pid}　示例笔记 pid = {note.pid}")

    interrupted = False
    try:
        # ⚠ 把 note 一起传进去：第 2 轮之前要把笔记窗口重新顶到最前，
        #   而按标题找窗口必须带上 pid（决策 31），否则会抓到上一轮
        #   没杀干净的旧笔记窗口，顶上去的还是被压住的那个。
        run(app, note)
    except ScreenLocked:
        # 开局 `require_clean_screen()` 已经查过一次没锁，所以走到这里
        # 一定是【跑到一半才锁的】。措辞在 `BOX.lock_bail()` 里，四个脚本共用。
        interrupted = True
        BOX.lock_bail()
    finally:
        for p in (note, app):
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        srv.shutdown()
        # ⚠ 这里原来还有一句「等程序死透了再还原」—— 现在没有要还原的东西了。
        #   那块屏上跑的 app 读写的始终是 `_e2e_settings.json`。
        time.sleep(0.8)

    # ⚠ 不能照常打汇总行、更不能返回 0。
    #   上面已经绿了的那些项是真的，但【整份是截断的】——
    #   截断的一份报成「通过 N 项」就是在撒谎，而且是本项目最忌讳的
    #   那种撒谎：把「没验过」和「验过了没问题」说成同一个样子。
    #
    # ⚠ 说「截断」跟它是【因为什么】停的无关：锁屏是一种（interrupted），
    #   `run()` 里二十几个提前 return 是另一种（没置上 FINISHED）。
    #   从前只防住了锁屏那一种 —— 于是 v0.20 里 ⑧ 那句 `return`
    #   把 ⑨⑩ 共 45 项整段跳过之后，脚本照样打「通过 24 项，不通过 4 项」。
    #   两种截断，一句真话。
    if interrupted or not FINISHED[0]:
        print("-" * 62)
        print(f"截断了：通过 {BOX._ok} 项，不通过 {BOX._fail} 项，后面的没跑")
        print(f"       停在「{WHERE[0]}」"
              f" —— 是那句 if 没通过就 return 了，不是跑完了。")
        return 1

    print("-" * 62)
    print(f"通过 {BOX._ok} 项，不通过 {BOX._fail} 项")
    return 1 if BOX._fail else 0


def run(app, note):
    step("⓪ 先确认示例笔记真的在框选区域里")
    seen = note_visible()
    if not BOX.check("示例笔记窗口在框选区域里（没被盖住）", seen,
                     unless(seen, "把挡在 (180,180)-(1220,720) 上的窗口挪开再跑")):
        return

    step("① 主窗口就位")
    panel = BOX.find_window(BOX.PANEL_TITLE, app.pid)
    if not BOX.check("拿到主窗口句柄", bool(panel)):
        return
    # ★ 先等 app 自己说「窗口已就位」再量（L.11 ③.2）。
    #   pywebview 先建窗口、再回调 on_ready，窗口期里 `EnumWindows` 已经
    #   看得见窗口，但 `layout("right")` 还没跑 —— 这时候量到的 rect 是初始值，
    #   而下面每一段 scan 都拿它当截图基准，会一路「找不到字」。
    #   ⚠ 日志必须是本脚本自己那份 APPLOG(`_app_kb.log`)：这个进程的
    #     「已就位」只有这份日志里才有，读别人的等于拿别人的启动当自己的前置。
    BOX.wait_laid_out_or_warn(app.pid, APPLOG)
    r = win32gui.GetWindowRect(panel)
    panel_rect = (r[0], r[1], r[2] - r[0], r[3] - r[1])    # 屏幕坐标，scan 直接用

    step("② 界面上先要看得见库里有东西")
    # 界面启动时会 loadSettings() → renderKb()，「库中 N 条」应该已经在上面了。
    # 这一条同时证明知识库卡片真的渲染出来了（否则后面点它的按钮都是空谈）。
    hit = {}
    for _ in range(15):
        time.sleep(0.6)
        hit = scan(panel_rect, ["库中", "立即识别"], tag="boot")
        if len(hit) == 2:
            break
    if not BOX.check("界面上找到「库中 N 条」", "库中" in hit,
                     "知识库卡片没渲染出来" if "库中" not in hit else ""):
        return
    BOX.check("界面上找到「立即识别」按钮", "立即识别" in hit)
    print(f"       库中 pill @ {hit.get('库中')}　立即识别 @ {hit.get('立即识别')}")

    # ══════════ 第 1 轮：dedupe，库里已有 RAG ══════════
    step("③ 第 1 轮：策略「不重复解释」，库里已经有一条 RAG")
    ROUND[0] = 1
    clicked = click_until(
        panel, hit["立即识别"],
        lambda: wait_for(lambda: len(log_lines("[管线]")) >= 1, 30),
        "立即识别")
    if not BOX.check("点「立即识别」，第 1 轮真的跑起来了", bool(clicked),
                     unless(clicked, "点了 3 次都没反应")):
        return
    pipe = wait_log("[管线]", 1, timeout=60)
    ai = wait_log("[AI]", 1, timeout=60)
    if not BOX.check("① 跑通（日志里有 [管线]）", bool(pipe), hint(pipe)):
        return
    print(f"       {pipe[0]}")
    if not BOX.check("② 打到假服务器并拿到回复", bool(ai), hint(ai)):
        return
    print(f"       {ai[0]}")

    n1 = AIT.GOT["hits"]
    BOX.check("这一轮只调了一次模型", n1 == 1, f"实际 {n1} 次")
    BOX.check("管线行说「库里已有 1」", "库里已有 1" in pipe[0], pipe[0])

    req = AIT.GOT["last"] or {}
    msgs = req.get("messages") or []
    user = msgs[1].get("content", "") if len(msgs) > 1 else ""
    raw_part = user.split(CAND_MARK, 1)[0] if CAND_MARK in user else user
    cands = cands_of(user)

    step("④ ★ 这一轮的重点：库里已有的词，有没有真的不问模型")
    BOX.check("user message 的【原文段】里仍然有 RAG", "RAG" in raw_part,
              "原文是整篇照发的，模型要靠它理解上下文")
    BOX.check("★ user message 的【候选词段】里【没有】RAG",
              "RAG" not in cands,
              f"候选词 = {cands}")
    BOX.check("候选词段里【有】Chunking（证明不是整批都没发）",
              any("Chunk" in c for c in cands), f"候选词 = {cands}")
    BOX.check("命中的 RAG 没被灌进 history 流水",
              not db("SELECT 1 FROM history WHERE key=?", (kb_mod.canon(SEED_TERM),)),
              "流水只留给「新解释出来的东西」—— 命中的词再灌一遍，只会让它白白翻倍")

    # ══════════ 界面：来源标记 ══════════
    step("⑤ 界面：知识库那一条要贴出来，并且标出来源")
    time.sleep(1.5)
    ui = {}
    for _ in range(8):
        ui = scan(panel_rect, [MARK_KB, MARK_SEED, EXCLUDE_TERM], tag="r1")
        if len(ui) == 3:
            break
        time.sleep(0.5)
    BOX.check(f"卡片上出现「{MARK_KB}」标记", MARK_KB in ui,
              "截图：冒烟测试\\_e2e_r11.png")
    BOX.check("库里那条解释的正文真的印在卡片上（RAGSEED）", MARK_SEED in ui,
              "卡片上没找到库里的解释" if MARK_SEED not in ui else "")
    BOX.check(f"要排除的那个词「{EXCLUDE_TERM}」在界面上（下面要右键它）",
              EXCLUDE_TERM in ui)

    step("⑥ 自动收录：模型答过的词，这一轮就该进库了")
    row = db("SELECT explanation FROM terms WHERE key=?", (kb_mod.canon(EXCLUDE_TERM),))
    # ⚠ `detail` 是【两路都打】的（`框选联调.check`）。这句话只有没进库时才成立，
    #   直接写进 detail 会让【通过】那一行上说「没进库的话…」——
    #   照本文件 :920 已经在用的写法，不进库时才有话说。
    BOX.check(f"「{EXCLUDE_TERM}」已经自动进了库（dedupe 模式）", bool(row),
              "" if row else "没进库的话，「不重复解释」的省钱承诺"
                             "要等用户手动点一次才开始生效")
    if row:
        BOX.check("存的是这一轮模型给的那版",
                  f"第 1 轮解释" in row[0]["explanation"], row[0]["explanation"][:40])

    # ══════════ 右键排除 ══════════
    step("⑦ 右键条目「不再解释该词」")
    if EXCLUDE_TERM not in ui:
        BOX.check("拿到卡片坐标才能右键", False, "上一步没找到这个词")
        return
    BOX.activate(panel)
    right_click(ui[EXCLUDE_TERM])
    menu = scan(panel_rect, ["不再解释该词"], tag="ctx")
    if not BOX.check("右键菜单弹出来了", "不再解释该词" in menu,
                     "截图：冒烟测试\\_e2e_ctx1.png"):
        return
    n = click_until(panel, menu["不再解释该词"],
                    lambda: wait_for(lambda: bool(
                        db("SELECT 1 FROM excluded WHERE key=?",
                           (kb_mod.canon(EXCLUDE_TERM),))), 3),
                    "不再解释该词")
    if not n:
        # ★ click_until 点满 4 次都没生效时【返回 0，而且一个字都不打】。
        #   不把这个 0 收下来，下面几条会以「没弹提示」「名单里没有它」的样子红掉 ——
        #   那些原因【全都指向产品】，而真正的原因（点击根本没进 WebView）
        #   一个字都不说。实踩过：2026-09-15 18:15 那轮就是这么红的，
        #   4 条不通过查了一轮，才发现该查的是点击，不是排除功能。
        #
        #   区分这两种情况的判据只有一个：
        #     app 日志里有没有 `[知识库] 排除：` 那一行
        #     （`main.py` 的 kb_exclude，成功必打，所以「没这一行」= 请求没到后端）
        #       有 → 请求到了后端而产品没反应，那才是真 bug
        #       没有 → 点击没送到。多半是窗口没抢到前台（前台锁）
        print("       ★ 「不再解释该词」点了 4 次，一次都没落进 WebView。")
        print("         这不是「排除功能坏了」—— 这是【点击没送到】。")
        print("         判据：看 app 日志有没有 `[知识库] 排除：` 那一行。")
        print("         有 = 请求到了后端、产品真的没反应（真 bug）；")
        print("         没有 = 点击没送到，先查前台焦点 / 屏幕是不是正锁。")

    # ⚠ 提示条 6 秒后自己消失，所以先扫它，再查数据库。
    #   反过来的话，数据库那几项查完可能已经超时，这一条会以「没弹提示」的样子红掉。
    toast = scan(panel_rect, ["已不再解释"], tag="toast")
    BOX.check("给了「已不再解释」的一次性提示（排除是单向门，必须看得见）",
              "已不再解释" in toast, "截图：冒烟测试\\_e2e_toast1.png")

    ex = db("SELECT key, term FROM excluded")
    BOX.check("排除名单里出现了它", any(r["key"] == kb_mod.canon(EXCLUDE_TERM)
                                   for r in ex), [dict(r) for r in ex])
    BOX.check("它【同时】还在库里 —— 排除必须排在知识库命中之前判",
              bool(db("SELECT 1 FROM terms WHERE key=?", (kb_mod.canon(EXCLUDE_TERM),))),
              "两个条件同时成立，才说明「排除 > 命中」这条顺序是对的")

    # ══════════ 操作台里的屏蔽词（v0.11 补的集中管理页）══════════
    step("⑧ 操作台的屏蔽词：⑦ 右键加的那个词，在这一页看得见、删得掉")

    # ⚠ 这一节是 v0.11 才补的断言。功能本身也是 v0.11 才有的：
    #   在那之前，排除名单是个【单向门】—— 右键加进去的词，界面上
    #   没有任何地方能看见它、更删不掉它。决策 21 早写了要有集中管理界面，
    #   一直没做，这一页就是补它的。
    #
    #   所以这里验的【不是】「能不能屏蔽一个词」——排除的语义、
    #   优先级、对整条的影响，`知识库联调.py` 的 t_excluded / t_phrase_excluded
    #   已经钉了十几条了。这里验的是这一页独有的那件事：
    #   ★ **两扇门（右键 / 操作台）通的是不是同一个房间**。
    #     各管一份名单的话，用户在操作台里删不掉右键加的词，
    #     而界面上看起来一切正常 —— 这正是「静默无效」，本项目最不想留的那种。
    if not open_console(panel, panel_rect, BOX):
        return

    # ⚠ 这里【必须】用 scan_down 而不是 scan：v0.20 起「屏蔽词」排在
    #   「快捷键取词」下面，默认视口里够不着（`grab()` 只截一屏）。
    #   用 scan 的话，下面那 4 条会一起红成「屏蔽词的增删坏了」——
    #   而功能一点没坏，只是那张卡在折线以下。附录 L.9。
    card = scan_down(panel_rect, ["屏蔽词", "添加", EXCLUDE_TERM, "1个"], tag="blk")
    BOX.check("操作台里有「屏蔽词」这一栏", "屏蔽词" in card,
              unless("屏蔽词" in card, "截图：冒烟测试\\_e2e_blk1.png"))
    BOX.check(f"★ ⑦ 用右键加进去的 {EXCLUDE_TERM}，在这一页也列着（同一个名单）",
              EXCLUDE_TERM in card,
              unless(EXCLUDE_TERM in card,
                     "右键和这一页各存一份名单的话，用户在这里删不掉右键加的词"))
    BOX.check("计数跟着名单走（此刻 1 个）", "1个" in card,
              unless("1个" in card, "计数和列表对不上，用户会以为加漏了"))

    # ── 从这一页自己加一个词 ──────────────────────────────
    # ⚠ 上面那次 scan_down 已经把这一卡滚进视口了，这里【不用再滚】——
    #   但也别退回成 scan：`card` 是在滚过之后拿到的，之后不改滚动位置，
    #   这几针才在同一屏上。要改就得一起改。
    inp = scan(panel_rect, ["输入要屏蔽的词"], tag="blk-in")
    add = scan(panel_rect, ["添加"], tag="blk-add")
    if not BOX.check("找得到屏蔽词输入框和「添加」按钮",
                     "输入要屏蔽的词" in inp and "添加" in add,
                     unless("输入要屏蔽的词" in inp and "添加" in add,
                            "截图：冒烟测试\\_e2e_blk_in.png")):
        return

    # ── ★「点输入框 → 打字 → 点添加」不能是三个裸动作，要整段重试 ──────
    #   原来这里是：activate 一次 → 点输入框 → type → 点「添加」。
    #   窗口没抢到前台时【第一下点击会被 Windows 吃掉】（见 click_until
    #   的说明），后果是输入框根本没拿到焦点，`type_text` 把词打进了空气，
    #   于是「添加」点几下都不会有任何反应 —— 而报出来是「屏蔽词加不上」。
    #   一条功能断言，把「点击没送到」说成了「产品坏了」，方向整个反了。
    #
    #   ⚠ 但【不能】把「打字」也塞进重试里。打第二次是【追加】不是覆盖
    #     （框里已经有 HTTPX，再打一遍成了 HTTPXHTTPX），落库的 key 就对不上，
    #     报出来还是「加不上」—— 而且真往用户名单里塞了个谁也没要的怪词。
    #     所以分两段：先把焦点点稳，再打一次字，最后重试点「添加」。
    #
    #   ⚠ 输入框的焦点【没有可验证的副作用】能当判据（点了就是点了），
    #     所以这里只能「每下都重新 activate、连点几下」地兜。
    #     好在输入框是【幂等】的 —— 点几下都只是重新拿一次焦点，
    #     不像「添加」点重了会重复加词。有副作用的那个才必须走 click_until。
    for _ in range(3):
        BOX.activate(panel)
        BOX.click(inp["输入要屏蔽的词"])

    BOX.type_text(NEW_BLK_TERM)

    def _added():
        return bool(db("SELECT 1 FROM excluded WHERE key=?",
                       (kb_mod.canon(NEW_BLK_TERM),)))

    # ⚠ 判据用【数据库】不是「我点过了」：OCR 会看走眼，落库不会。
    n = click_until(panel, add["添加"], lambda: wait_for(_added, 3), "添加")

    added = _added()
    if not n:
        # ★ click_until 点满 4 次不生效时【返回 0，而且一个字都不打】。
        #   不收下这个 0，下面三条会以「没回已屏蔽」「列表里没有它」
        #   「计数没变成 2 个」的样子红掉 —— 那些原因【全指向产品】，
        #   而真因（请求压根没发出去）一个字都不说。
        #
        #   判据还是那一个：app 日志有没有 `[知识库] 排除：` 那一行。
        #   ⚠ 前端在框为空时【直接 return，不发请求】（app.js 的 add()），
        #     所以「没有这一行」同时盖住两件事：点击没送到 / 字没打进框。
        #     这两种都【不是】屏蔽功能的问题。
        print(f"       ★ 「添加」点了 4 次，排除名单里始终没有 {NEW_BLK_TERM}。")
        print(f"         先看 app 日志有没有 `[知识库] 排除：{NEW_BLK_TERM}` 那一行：")
        print("           有   → 请求到了后端却没落库，那是产品真的坏了。")
        print("           没有 → 请求压根没发出去。多半是【打进去的字没进输入框】")
        print("                  （点那几下没抢到前台），不是屏蔽功能的问题。")

    BOX.check(f"手打「{NEW_BLK_TERM}」点「添加」，真的落进了排除名单",
              added,
              unless(added, f"点完 4 秒，excluded 表里还是没有 "
                            f"{kb_mod.canon(NEW_BLK_TERM)}　{BOX.fg_info()}"))

    # ⚠ 也用 scan_down：名单刚长了一行，下面那些内容整体下移了 ~30px，
    #   原来贴着底边的那一屏可能正好把新行漏在折线以下。
    #   不滚的话，「新加的词出现在列表里」会红 —— 而它其实加上了、也画出来了。
    after = scan_down(panel_rect, ["已屏蔽", NEW_BLK_TERM, "2个"], tag="blk2")
    BOX.check("界面回了「已屏蔽…」（看得见的反馈，不是默默加上了）",
              "已屏蔽" in after, unless("已屏蔽" in after, "截图：冒烟测试\\_e2e_blk2.png"))
    BOX.check("新加的词出现在列表里", NEW_BLK_TERM in after,
              unless(NEW_BLK_TERM in after, "落库了但列表没重绘 —— 用户会以为没加上"))
    BOX.check("计数变成 2 个", "2个" in after,
              unless("2个" in after, "列表和计数不同步"))

    # ── 再删掉它（单向门必须有回来的路）────────────────────
    # ⚠ 「恢复」按钮和词在同一行，所以【只扫这一行那条带】。
    #   整屏扫"恢复"的话，Chunking 那行也有一个，拿到哪个全看 OCR 的遍历顺序 ——
    #   拿到 Chunking 的那条，会以「恢复没生效」的样子红，而其实点错了行。
    if NEW_BLK_TERM not in after:
        # ⚠ 这句话从前是光秃秃一个 return —— 它一执行，⑨⑩ 共 45 项【整段没跑】，
        #   而脚本照样打「通过 N 项，不通过 M 项」。v0.20 的回归就是这么被骗过去的。
        #   现在 main() 靠 FINISHED 认得出这种截断（见那边），这里再点一句，
        #   是为了让人一眼看出【是从这一条断的】。
        print(f"       ★ 这一条没通过 —— ⑨⑩ 和导出那几节【整段没跑】，不是跑完了。")
        return
    band = (panel_rect[0], after[NEW_BLK_TERM][1] - 24, panel_rect[2], 48)
    un = scan(band, ["恢复"], tag="blk-un")
    if not BOX.check(f"「{NEW_BLK_TERM}」那一行找得到「恢复」按钮", "恢复" in un,
                     unless("恢复" in un, "截图：冒烟测试\\_e2e_blk_un.png")):
        return
    # ★ 同样不能是裸点击 —— 理由见上面「添加」那一段（第一下点击会被
    #   窗口激活吃掉），而这里的代价更大：报出来是「恢复删了个假动作」，
    #   听起来像【数据删不掉】，实际上是点击没送到。
    n = click_until(panel, un["恢复"],
                    lambda: wait_for(lambda: not db(
                        "SELECT 1 FROM excluded WHERE key=?",
                        (kb_mod.canon(NEW_BLK_TERM),)), 3),
                    "恢复")

    gone = not db("SELECT 1 FROM excluded WHERE key=?",
                  (kb_mod.canon(NEW_BLK_TERM),))
    if not n:
        # ⚠ 这一条【没有】「请求到没到后端」的判据可用 ——
        #   `main.py` 的 kb_unexclude 不像 kb_exclude 那样往日志里打一行。
        #   所以别在这儿编一个日志判据出来，只能给能给的：
        #   看下面那条断言里的 fg_info()，前台不是面板 = 点击没送到。
        print("       ★ 「恢复」点了 4 次，名单里它还在。")
        print("         这一条没有「请求到没到后端」的日志判据可用")
        print("         （kb_unexclude 不打日志行），所以先看下面那句 fg_info()：")
        print("           前台不是面板 → 点击没送到，不是「恢复」坏了。")

    BOX.check("★ 点「恢复」把它从名单里拿掉了（这个门是双向的）", gone,
              unless(gone, "点了「恢复」但数据库里还在 —— 那是删了个假动作"
                           f"　{BOX.fg_info()}"))
    # ⚠ 同时确认【没有误伤】：恢复一个词不该把别的词一起带走。
    #   列表重绘是按 key 删的，写错 key 的话会静默删掉别人，
    #   而界面上看起来只是「少了一行」。
    BOX.check(f"恢复它没有误伤 {EXCLUDE_TERM}",
              bool(db("SELECT 1 FROM excluded WHERE key=?",
                      (kb_mod.canon(EXCLUDE_TERM),))),
              f"名单现在 = {[dict(r) for r in db('SELECT key, term FROM excluded')]}")

    # ⚠ 同 `after`：删掉一行会让下面的内容上移，那一屏也可能不再对得上。
    back = scan_down(panel_rect, ["已恢复", "1个"], tag="blk3")
    BOX.check("界面上它也没了，计数回到 1 个",
              "已恢复" in back and "1个" in back,
              unless("已恢复" in back and "1个" in back,
                     f"截图：冒烟测试\\_e2e_blk3.png　找到的={sorted(back)}"))

    if not back_to_stream(panel, panel_rect, BOX):
        return

    # ══════════ 第 2 轮：repeat，验证策略真的生效 ══════════
    step("⑨ 切到「可重复解释」再跑一轮 —— 策略必须真的管用")

    # ⚠ 跑第 2 轮之前【必须再验一次】笔记还在不在。
    #   第 1 轮到现在隔了右键、弹提示、切策略好几次点击，几十秒里那块
    #   屏幕区域是可能被压住的 —— 实测发生过一次：第 2 轮识别到的是
    #   浏览器里一篇飞书文档，候选词成了 Board / Share / Editing。
    #   不查的话，报出来的是「候选词段里没有 RAG」—— 一条功能断言，
    #   把一件环境事故说成了代码 bug，方向整个反了。
    if not note_visible(tries=5):
        # 先试着把笔记窗口重新顶到最前。它是 -topmost 的，正常情况下
        # 本来就该在最上面；这一步只为把「被别的窗口抢走了置顶」捞回来。
        nh = BOX.find_window("示例笔记", note.pid, timeout=2)
        if nh:
            win32gui.SetWindowPos(
                nh, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
            time.sleep(0.6)
    if not note_visible(tries=3):
        # 顶不起来就是真被压住了 —— 截一张全屏，让人一眼看见是谁。
        BOX.grab(capture.primary_rect(), os.path.join(HERE, "_kb_r2cover.png"))
        BOX.check("第 2 轮开跑前，示例笔记还在框选区域里", False,
                  "全屏截图：冒烟测试\\_kb_r2cover.png")
        return

    # 三档策略在操作台里，先点齿轮进去
    if not open_console(panel, panel_rect, BOX):
        return

    hit2 = scan(panel_rect, ["可重复解释"], tag="seg")
    if not BOX.check("操作台里找到「可重复解释」", "可重复解释" in hit2):
        return
    switched = click_until(panel, hit2["可重复解释"],
                           lambda: wait_for(
                               lambda: read_setting("kb_policy") == "repeat", 4),
                           "可重复解释")
    BOX.check("策略落盘了", bool(switched),
              unless(switched, "点了按钮但设置没变 —— 那就是「切了没反应」的老毛病"))

    # 切完回条目页 —— 「立即识别」在那一页，不在操作台里
    if not back_to_stream(panel, panel_rect, BOX):
        return

    # ★ 条目页那句策略说明要跟着变。它在条目页是【只读的一行字】，
    #   真正的按钮在操作台 —— 两处各写一遍的话，切完策略回到这一页，
    #   这行字还是旧的，用户会以为没切成功。这正是「一个开关摆两处」的典型症状。
    BOX.check("★ 回到条目页，那句策略说明跟着变了（「忽略库」）",
              "忽略库" in scan(panel_rect, ["忽略库"], tag="policy"),
              "截图：冒烟测试\\_e2e_policy1.png")

    hit3 = scan(panel_rect, ["立即识别"], tag="r2btn")
    if not BOX.check("再次找到「立即识别」", "立即识别" in hit3):
        return
    ROUND[0] = 2
    clicked2 = click_until(
        panel, hit3["立即识别"],
        lambda: wait_for(lambda: len(log_lines("[管线]")) >= 2, 30),
        "立即识别")
    if not BOX.check("点「立即识别」，第 2 轮真的跑起来了", bool(clicked2),
                     unless(clicked2, "点了 3 次都没反应")):
        return
    pipe2 = wait_log("[管线]", 2, timeout=60)
    ai2 = wait_log("[AI]", 2, timeout=60)
    if not BOX.check("第 2 轮跑完（[管线] 有第 2 条）", len(pipe2) >= 2, hint(len(pipe2) >= 2)):
        return
    print(f"       {pipe2[1]}")
    if not BOX.check("第 2 轮打到了假服务器", len(ai2) >= 2, hint(len(ai2) >= 2)):
        return

    BOX.check("第 2 轮的总调用次数 = 2", AIT.GOT["hits"] == 2,
              f"实际 {AIT.GOT['hits']} 次")
    BOX.check("管线行说「库里已有 0」—— repeat 就是绕过库",
              "库里已有 0" in pipe2[1], pipe2[1])

    msgs2 = (AIT.GOT["last"] or {}).get("messages") or []
    user2 = msgs2[1].get("content", "") if len(msgs2) > 1 else ""
    cands2 = cands_of(user2)

    step("⑩ ★ 第 2 轮的重点：库被绕过了，但排除名单还拦着")
    BOX.check("★ 候选词段里【重新出现了】RAG（repeat 绕过了知识库）",
              any("RAG" == c for c in cands2), f"候选词 = {cands2}")
    BOX.check(f"★ 候选词段里【没有】{EXCLUDE_TERM}（排除名单拦住了）",
              not any(EXCLUDE_TERM in c for c in cands2), f"候选词 = {cands2}")
    BOX.check("候选词段里还有别的词（证明不是整批都没发）",
              len(cands2) >= 3, f"候选词 = {cands2}")

    # ══════════ 收纳历史 ══════════
    step("⑩b 收纳历史：把 repeat 这轮重新问出来的新版解释收编进库")
    before = db("SELECT explanation FROM terms WHERE key=?",
                (kb_mod.canon(SEED_TERM),))[0]["explanation"]
    BOX.check("收编前，库里还是那条老的", SEED_EXPL == before, before[:40])

    # 收纳历史 / 导出 md 也在操作台里。
    # ⚠ 这里【进去就不出来了】：⑪ 紧接着还要用同一个页面上的「导出 md」。
    if not open_console(panel, panel_rect, BOX):
        return

    hv = scan(panel_rect, ["收纳历史"], tag="harvest")
    if not BOX.check("操作台里找到「收纳历史」", "收纳历史" in hv):
        return
    click_until(panel, hv["收纳历史"],
                lambda: wait_for(lambda: bool(log_lines("[知识库] 收纳历史")), 8),
                "收纳历史")
    hl = log_lines("[知识库] 收纳历史")
    if not BOX.check("后端打印了收纳结果", bool(hl), hint(hl)):
        return
    print(f"       {hl[-1]}")

    after = db("SELECT explanation FROM terms WHERE key=?",
               (kb_mod.canon(SEED_TERM),))[0]["explanation"]
    BOX.check("★ RAG 那条被【新版】覆盖了（收纳的是最新一轮）",
              "第 2 轮解释" in after and after != before, after[:50])
    src = db("SELECT source FROM terms WHERE key=?",
             (kb_mod.canon(SEED_TERM),))[0]["source"]
    BOX.check("来源标成 harvest（下一版导入的条目才不会被它冲掉）",
              src == "harvest", src)

    # ══════════ 导出 md ══════════
    step("⑪ 导出 md（还在操作台里，不用再点一次齿轮）")
    # ⚠ 上面每一节的 `if not BOX.check(...): return` 都会【静默交卷】。
    #   `main()` 靠这一面旗子分辨「跑完了」和「半路停了」——
    #   **只有跑到最后一行才算跑完**。加新节的时候，把新代码写在
    #   最后那句 `FINISHED[0] = True` 前面，别写在它后面（写在后面 = 白写）。
    # ⚠ 找的是「导出md」不是「导出」。这一页上带「导出」二字的地方不止按钮：
    #   上面那个输入框的 placeholder 就是「导出目录」。搜「导出」的话，
    #   scan 取的是【第一条命中的行】，很可能取到输入框 —— 点上去毫无反应，
    #   而失败的样子是「后端没打印导出结果」，看起来像导出功能坏了。
    #   scan 比的是【去掉空格之后】的整行，所以按钮上的「导出 md」写成「导出md」能中，
    #   而「导出目录」中不了。
    ex_btn = scan(panel_rect, ["导出md"], tag="export")
    if not BOX.check("操作台里找到「导出 md」按钮", "导出md" in ex_btn):
        return
    click_until(panel, ex_btn["导出md"],
                lambda: wait_for(lambda: bool(log_lines("[知识库] 导出")), 8),
                "导出 md")
    el = log_lines("[知识库] 导出")
    if not BOX.check("后端打印了导出结果", bool(el), hint(el)):
        return
    print(f"       {el[-1]}")
    BOX.check("导出成功了（不是「导出失败」）", "失败" not in el[-1], el[-1])

    files = [f for f in os.listdir(OUT_DIR) if f.endswith(".md")]
    if not BOX.check("导出目录里真的生成了 md 文件", bool(files),
                     unless(files, f"{OUT_DIR} 里没有 .md")):
        return
    path = os.path.join(OUT_DIR, sorted(files)[-1])
    BOX.check("导到了 kb_dir 指定的目录里（不是用户的「文档」）",
              os.path.dirname(path) == OUT_DIR, path)
    print(f"       {path}")

    with open(path, encoding="utf-8") as f:
        text = f.read()

    def first_cell(ln):
        c = cols_of(ln)
        return c[0].strip() if c else ""

    # ⚠ 表头要按【第一栏是不是「术语」】来排，不能写成 `"术语" not in ln`。
    #   后者会把【解释正文里恰好带「术语」二字的数据行】一起排掉 ——
    #   假服务器的每一条解释都是「X 是这段笔记里的一个英文术语。」，
    #   于是 rows 被清空、下面三条断言全红，而导出的文件其实一个字都没错。
    #   真实模型的解释里「术语」两个字也常见，这个坑不是假服务器特有的。
    rows = [ln for ln in text.splitlines()
            if ln.startswith("|") and "---" not in ln
            and first_cell(ln) != "术语"]
    BOX.check("表格里有两栏表头", "| 术语 | 解释 |" in text)
    BOX.check("每条都是严格两栏（下一版的导入要靠它对齐）",
              rows and all(len(cols_of(ln)) == 2 for ln in rows),
              unless(rows, "一条数据行都没解析出来") or
              f"异常行：{[ln for ln in rows if len(cols_of(ln)) != 2][:2]}")

    rag_row = [ln for ln in rows if first_cell(ln) == SEED_TERM]
    BOX.check("导出的 RAG 那条是收编后的新版",
              rag_row and "第 2 轮解释" in rag_row[0], rag_row[:1])
    BOX.check("排除掉的词【没有】被导出时漏掉（排除只影响显示，不删库）",
              any(first_cell(ln) == EXCLUDE_TERM for ln in rows),
              f"{EXCLUDE_TERM} 不在导出里")
    BOX.check("文件写了「这是快照」的说明（下一版别拿改过的文件来问为什么没生效）",
              "不会回到" in text)

    print("\n       结果截图：冒烟测试\\_e2e_r11.png（第 1 轮卡片）"
          " / _e2e_export1.png（收尾那一屏）")

    FINISHED[0] = True      # ← 跑到这里才算【整份跑完】。见 main() 里那段说明。


def cols_of(line):
    """按【未转义的】竖线切一行表格。`\\|` 是内容里的竖线，不算分隔。

    ⚠ 直接用 str.split("|") 会连转义过的那些一起切开，
      于是「含竖线的解释」那一行会莫名其妙多出一栏 —— 而它其实是对的。
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    out, cur, i = [], "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            cur += s[i:i + 2]
            i += 2
            continue
        if s[i] == "|":
            out.append(cur)
            cur = ""
            i += 1
            continue
        cur += s[i]
        i += 1
    out.append(cur)
    return out


if __name__ == "__main__":
    sys.exit(main())
