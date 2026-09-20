# -*- coding: utf-8 -*-
"""② AI 端到端联调 —— 从「点按钮」一路验到「解释印在侧栏卡片上」。

    python "Desktop\\问了吗\\冒烟测试\\AI端到端联调.py"
    跑之前请把别的「问了吗」关掉 —— 脚本会自己起一个干净实例。

────────────────────────────────────────────────────────
和 `AI接入联调.py` 的分工：

    那个测的是 ai.py 这个【函数】对不对（78 项，纯内存，几秒跑完）
    这个测的是【整条链路】通不通：按钮 → js_api → ai.explain → 前端重绘（25 项）

    ⚠ 25 项 = 【两相】，各自起一个干净实例（跑完一个 taskkill 掉再起下一个）：
        ① 框选/自动那条：点「立即识别」→ ①③② → 卡片上屏
        ⑦ 快捷键那条（v0.20）：热键线程拿到选段 → 轮询 → clip 管线 → 中文卡片上屏
      两相必须【先后】跑，不能同时活着 —— 它们往同一份 `_app_ai.log` 里写，
      而这一相的判据全是读日志的。真踩过：第一个版本把 clip 那一相写在
      同一个 try 里，上一个实例没关就开下一个，日志被截断后两个进程插花，
      冒出两条「[热键] 已挂起（录制中）」和一屏空洞 —— 判据全建立在脏数据上。

单测全绿也不代表用户在屏幕上看得到东西 —— v0.6 那个「框选完卡片不更新」
的 bug，单测就是绿的。所以这一步必须真开窗口、真点、真截图看。

对面依然不是真 DeepSeek，而是本地假服务器（从 `AI接入联调.py` 里借）。
通过 `WENLEMA_BASE_URL` 环境变量把 app 指过去 —— 联网和费用都为零。
"""
import ctypes
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

# ⚠ 输出重定向到管道/文件时，Python 默认用本地编码 GBK。
#   下面第 ⓪ 步那个 `⓪`（U+24EA）GBK 里【没有】—— 而 ①~⑩ 有。
#   于是它会以「跑到一半 UnicodeEncodeError」的样子炸掉，看着像功能挂了，
#   其实一个字都还没测。main.py 开头改的是同一句。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 探针必须自己 DPI 感知

import win32gui                                                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
APPLOG = os.path.join(HERE, "_app_ai.log")
# ⚠ settings 的路径【不在这里定义】—— 唯一实现处是 `框选联调.py` 的 SETTINGS，
#   下面 `BOX = load(...)` 之后直接取 `BOX.SETTINGS`。
#   联调一律写那份临时文件，`app\settings.json`（里面是用户的真 Key）全程不打开。
#   为什么不许碰它，见 `框选联调.py` 里 SETTINGS 上面那段长注释。
KB_DB = os.path.join(HERE, "_e2e_ai_kb.db")

sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import shell as shell_mod                                         # noqa: E402


def load(name, filename):
    """按文件路径加载同目录的脚本 —— 文件名是中文，不适合当模块名。"""
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


BOX = load("boxlink", "框选联调.py")      # 借它的窗口/点击/OCR 工具
AIT = load("aitest", "AI接入联调.py")     # 借它的假 DeepSeek

SETTINGS = BOX.SETTINGS                   # ← 联调专用的临时 settings（见文件开头）

# 假服务器要答的话。开头「假服务器」四个字是给 OCR 找的锚点 ——
# 面板窄、字小，整句不一定认得全，但四个字一定认得出。
#   第二条是【整条】：`Prompt Engineering` 是示例笔记里现成的一个词组
#   （`框选联调.py:57-61` 那四行），而 ① 只会把它拆成 `Prompt` / `Engineering` 两条候选，
#   词组本身【不在候选表里】。这正是要验的形状 —— ② 返回一条不在 ① 候选表里的术语时，
#   它还能不能一路走到屏幕上。
FAKE_ANSWER = {"条目": [
    {"术语": "RAG", "解释": "假服务器回答：检索增强生成，先从外部资料里查出相关内容再让模型基于它作答。"},
    {"术语": "Prompt Engineering", "解释": "假服务器回答：整条词组——提示词工程，把给模型的指令当成一门手艺来打磨。"},
]}

AI_DELAY = 4.0        # 让「正在问模型」这段停留得够久，能被稳定截到


def seed_settings(auto_on=False):
    """种一份「装好就能跑」的联调 settings：已有区域、已有（假）Key。

    ⚠ 种的是 `_e2e_settings.json`，不是 `app\\settings.json` —— 整份重写，
      不需要备份，也就没有「还原」这一步可失败（2026-09-15 的教训）。
      区域直接塞进去，本脚本能专注验 ②，不必再走一遍拖框
      （那条链路由 `框选联调.py` 单独负责，两边不重叠）。

    `auto_on` 只有快捷键那一相（`run_clip()`）会传 True：那里要靠
      「自动通道还发不发车」来验「clip 没有把这一屏钉成【跑过了】」。
    """
    note = BOX.NOTE_RECT
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump({
            "api_key": "sk-test-fake",        # 假的。真 Key 由用户自己填
            "kb_policy": "dedupe",
            "kb_dir": "",
            "system_prompt": "",              # 空 = 用后端默认提示词
            "seen_notice": True,              # 别让首用告知盖住界面
            "region": [note[0], note[1], note[2], note[3]],
            "auto_on": auto_on,
        }, f, ensure_ascii=False, indent=2)


def read_log(needle, since=0):
    out = []
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if needle in ln:
                    out.append(ln.strip())
    except FileNotFoundError:
        pass
    return out[since:]


def main():
    print("=" * 62)
    print("问了吗 · ② AI 端到端联调（假 DeepSeek，不联网）")
    print("=" * 62)
    if not BOX.require_clean_screen():
        return 1

    # ── 假 DeepSeek ──────────────────────────────────────
    AIT.SCEN.update(status=200,
                    content=json.dumps(FAKE_ANSWER, ensure_ascii=False),
                    delay=AI_DELAY)
    AIT.GOT.update(hits=0, last=None, auth=None)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), AIT.Fake)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"假 DeepSeek 起在 127.0.0.1:{port}")

    seed_settings()
    open(APPLOG, "wb").close()

    note = BOX.start_note()
    time.sleep(2.0)

    # ⚠ WENLEMA_KB：v0.8 起 Api.explain() 拿到模型回答后会【写知识库】（③ 的入口）。
    #   不指的话，这个脚本每跑一次，就往用户辛辛苦苦攒出来的 app\kb.db 里
    #   塞一批假术语和假解释 —— 而且是悄无声息的。
    #   和 WENLEMA_BASE_URL 一个性质：留给联调脚本的接口，不是给用户的开关，
    #   正常启动时它不存在，行为与写死路径完全一致。
    #
    # ⚠ 每次开跑前【删掉】这个测试库，不能让它跨轮活着。
    #   它现在会真的生效（这正是 ③ 通了的意思）：上一轮把 RAG 写了进去，
    #   这一轮 dedupe 就直接拿库里的解释、根本不问模型 ——
    #   于是「① 捞到的候选词真的进了 user message」那条断言，
    #   是靠 RAG 恰好也在【笔记原文】里才继续变绿的，属于为错误的理由通过。
    #   本脚本的主题是 ②，库里该是空的：让 ③ 的命中与否由
    #   `知识库端到端联调.py` 专门去验，别在这里串味。
    for _p in (KB_DB, KB_DB + "-wal", KB_DB + "-shm"):
        if os.path.exists(_p):
            os.remove(_p)

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
    print(f"app pid = {app.pid}")

    interrupted = False
    try:
        try:
            run(app)
        except BOX.ScreenLocked:
            # 开局 `require_clean_screen()` 已经查过一次没锁 —— 所以走到这里，
            # 一定是【跑到一半才锁的】。措辞在 `BOX.lock_bail()` 里，四个脚本共用。
            interrupted = True
            BOX.lock_bail()
        finally:
            for p in (note, app):
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)

        # ⚠ ⑦ 这一相【另起一个干净实例】，**而且必须在上面那个被 taskkill
        #   之后**才开始。两个理由：
        #
        #   ① ★ 上一轮真栽在这儿：`run_clip()` 开头会 `open(APPLOG, "wb")`
        #      把日志截断，而上一个实例还活着、它的文件句柄偏移还在老远处 ——
        #      于是两个进程往同一个文件里插花，中间留一大段空洞。
        #      症状是日志里冒出**两条**「[热键] 已挂起（录制中）」和一屏空格，
        #      而这一相【所有】判据都是读日志的，等于全部建立在脏数据上。
        #      「日志里有这行」这种判据，前提是**这份日志只有一个作者**。
        #   ② 上一相跑完，面板上已经贴满了 RAG / Prompt Engineering 的卡，
        #      再断言「抓手上了屏」时，一张旧卡就足以让 OCR 找错东西。
        #
        #   代价是多开一次窗口（约 10 秒），换来的是这条断言真能分辨真假。
        if not interrupted:
            try:
                run_clip(port)
            except BOX.ScreenLocked:
                interrupted = True
                BOX.lock_bail()
    finally:
        srv.shutdown()

    if interrupted:
        # ⚠ 不能照常打汇总行、更不能返回 0 —— 整份是【截断的】。
        #   截断的一份报成「通过 N 项」就是在撒谎，而且是把
        #   「没验过」和「验过了没问题」说成同一个样子。
        print("-" * 62)
        print(f"截断了：通过 {BOX._ok} 项，不通过 {BOX._fail} 项，后面的没跑")
        return 1

    print("-" * 62)
    print(f"通过 {BOX._ok} 项，不通过 {BOX._fail} 项")
    return 1 if BOX._fail else 0


# ══════════════════════════════════════════════════════════
# v0.20 · 快捷键取词（clip）那一相
# ══════════════════════════════════════════════════════════

# 一段【纯中文】。这正是这条通道存在的意义：
# ① 那条规则初筛只认英文字母，这段一个候选都给不出来 ——
# 术语只能由 ② 从原文里挑。所以它同时钉住两件事：
# 「候选为空不早退」和「中文条目能走到屏幕上」。
CLIP_TEXT = "这个季度的核心抓手是把用户留存的颗粒度对齐到周。"

# ★ 锚点用「假服务器回答」五个字，和 region 那一相同一个约定：
#   面板窄、字小，整句不一定认得全，但这五个字一定认得出。
CLIP_ANSWER = {"条目": [
    {"术语": "抓手", "解释": "假服务器回答：抓手——事情最关键的着力点。"},
]}

# 自动通道等多少秒才发车。★ 调大是【故意的】：要保证 clip 先跑完、
# 自动通道后到，这样「clip 有没有把这一屏钉死」才有判别力。
# 反过来（自动先跑）的话，这条断言无论有没有 bug 都是绿的。
CLIP_SETTLE = 12


def run_clip(port):
    """快捷键那条通道：从「热键线程拿到选段」一路验到「中文卡片上屏」。

    ⚠ **最前面那一跳（按 Ctrl+E → 热键真的响）这里验不了** ——
      pynput 的 `GlobalHotKeys._on_press` 里写着 `if not injected:`，
      它刻意忽略注入的按键，`SendInput` 造出来的 Ctrl+E 它看不见。
      所以走 `WENLEMA_CLIP_TEXT` 后门从 `_worker` 的出口接进去。
      **这一跳只能人工验**，本文件末尾会再写一遍，别当成测过了。

      后门接进去之后，剩下全条都是真的：轮询 → ack → 前端 runPipeline('clip')
      → recognize('clip') → 空候选不早退 → ② → extras 上屏。
    """
    print("\n" + "─" * 62)
    print("⑦ 快捷键取词通道（clip）—— 另起一个干净实例")
    print("─" * 62)
    print(f"    （注入的选段：{CLIP_TEXT}）")
    print(f"    （自动通道开着、等 {CLIP_SETTLE} 秒 —— 用来验 clip 没把这一屏钉死）")

    # ★ 上一相如果没收干净（taskkill 没生效、或者有人在跑这个脚本的同时
    #   自己开着一个「问了吗」），这里再拦一道。不是多余的谨慎：
    #   这一相的判据**全是读日志的**，而两个实例会往同一份日志里插花，
    #   到时候红的是「clip 没把这一屏钉死」这种功能断言的名字。
    if not BOX.require_clean_screen():
        return

    seed_settings(auto_on=True)
    open(APPLOG, "wb").close()

    AIT.SCEN.update(status=200,
                    content=json.dumps(CLIP_ANSWER, ensure_ascii=False),
                    delay=1.0)
    AIT.GOT.update(hits=0, last=None, auth=None)

    env = dict(os.environ,
               WENLEMA_BASE_URL=f"http://127.0.0.1:{port}",
               WENLEMA_KB=KB_DB,
               WENLEMA_SETTINGS=SETTINGS,
               WENLEMA_CLIP_TEXT=CLIP_TEXT,
               WENLEMA_AUTO_SETTLE=str(CLIP_SETTLE))
    log = open(APPLOG, "wb")
    app = subprocess.Popen([sys.executable, "main.py"], cwd=APP, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    try:
        _run_clip_asserts(app)
    finally:
        subprocess.run(["taskkill", "/PID", str(app.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _run_clip_asserts(app):
    # ── 等前端把选段取走并把管线跑完 ────────────────────────────
    got = BOX.wait_for(lambda: bool(read_log("[管线·取词]")), 40)
    if not BOX.check("★ 前端轮询取走了选段并跑完了 clip 管线"
                     "（日志里有 `[管线·取词]`）", got,
                     "没有它 = 后端种下的选段从没被前端取走，"
                     "或者前端那条 clip 分支没跑起来"):
        _dump_clip(app)
        return

    clip_lines = read_log("[管线·取词]")
    ln = clip_lines[0]
    check_txt = ln.split("[管线·取词]", 1)[1].strip()
    BOX.check("clip 那条日志报的是【选中多少字】（不是截屏/OCR 耗时）",
              "选中" in check_txt and "截屏" not in check_txt, repr(ln))

    # ★ 前缀必须是 `[管线·取词]`，不能是 `[管线]`。
    #   `"[管线]" in "[管线·取词] …"` 恰好为假（`线` 后面跟的是 `·`）——
    #   所以两个脚本的解析互不干扰。更要紧的是：回归跑到一半，
    #   手在键盘上按一下 Ctrl+E 就会多打一条，用同一个前缀的话，
    #   红的会是【框选联调】那条「① 跑通」，看着像管线坏了。
    BOX.check("★ clip 的日志前缀和 region 分开（没串成 `[管线]`）",
              "[管线] 截屏" not in "\n".join(read_log("[管线] 截屏")),
              "有 `[管线] 截屏` 就说明 clip 那条也去截屏了 —— 它不该截屏")

    # ── ② 真的被调了，而且用的是中英不限那份提示词 ──────────────
    req = AIT.GOT.get("last") or {}
    msgs = req.get("messages") or []
    sys_prompt = msgs[0].get("content", "") if msgs else ""
    user_msg = msgs[1].get("content", "") if len(msgs) > 1 else ""

    BOX.check("★ 纯中文、① 一个候选都没有，② 还是被调了（没被早退挡住）",
              AIT.GOT.get("hits", 0) >= 1,
              f"假服务器收到 {AIT.GOT.get('hits', 0)} 次请求")
    BOX.check("★ clip 那条日志明说了「无规则候选，交给模型挑」",
              bool(read_log("无规则候选")),
              "这句是 clip 故意绕过空候选闸门的现场记录，"
              "没有它就只能靠猜「到底走没走那条岔路」")

    import prompt as prompt_mod                                   # noqa: E402
    BOX.check("clip 用的是【中英不限】那份提示词",
              sys_prompt == prompt_mod.DEFAULT_PROMPT_ANY,
              "用了英文那份的话，模型会拒答中文")
    BOX.check("★ clip 的 user message 头是「术语候选词」，不是「英文候选词」",
              "【规则初筛捞出的术语候选词】" in user_msg,
              user_msg[:100])

    # ★ 中文术语【不在候选表里】—— 它靠的就是 extras 那条通道上屏
    #   （v0.10 决策 47 留下的、唯一不依赖候选表的卡片来源）。
    panel = BOX.find_window(BOX.PANEL_TITLE, app.pid)
    if not BOX.check("拿到主窗口句柄（clip 那一相）", bool(panel)):
        _dump_clip(app)
        return
    r = win32gui.GetWindowRect(panel)
    origin = (r[0], r[1])
    panel_rect = (r[0], r[1], r[2] - r[0], r[3] - r[1])
    BOX.check("★ 中文术语「抓手」一路走到了屏幕上（走了 extras 那条通道）",
              find_on_panel(panel_rect, origin, "抓手"),
              "上不了屏说明 extras 分支没接住不在候选表里的条目 —— "
              "而 clip 这条通道【所有】中文条目都是那种条目")

    # ── ★ clip 不许上报 done ────────────────────────────────────
    # `auto.report('done')` 有两个副作用：清 busy，**和把「这一屏跑过了」钉上**。
    # 快捷键解释的是【剪贴板里的文字】，压根没读那一屏 —— 钉住它的后果很具体：
    # **压掉一次合法的自动通道运行**，而且是静默的，用户只会觉得
    # 自动通道「有时候不灵」。
    #
    # 判据是【正面】的：自动通道照样发车（日志里出现 `[管线] 截屏`）。
    # 用反面的「一段时间内没有截屏」验不出来 —— 那和「自动通道本来就慢」
    # 长得一模一样。这条等得久是有意的，见 CLIP_SETTLE 上面的说明。
    fired = BOX.wait_for(lambda: bool(read_log("[管线] 截屏")), CLIP_SETTLE * 2 + 30)
    BOX.check("★ clip 没把这一屏钉成「跑过了」—— 自动通道照样发车",
              fired,
              "两种可能，先分清再改代码："
              "① clip 真的上报了 done（本相要防的 bug）；"
              "② 自动通道本来就没触发 —— 先看标题栏那行字是"
              "「自动 · Ns」还是「待框选/待确认/截屏失败」，"
              "接线断没断和画面积不满 N 秒要分开，"
              "`自动通道端到端联调.py` 的 dump_why_not_fired 就是干这个的")
    if not fired:
        _dump_clip(app)


def _dump_clip(app):
    """clip 那一相没跑通时，把 app 日志尾部打出来。

    ⚠ 诊断代码必须比被测代码更不容易出错（auto.py / hotkey.py 同款告诫）：
      这里只读文件、只 print，不碰窗口也不碰剪贴板。
    """
    print("    ── app 日志尾部（诊断用）──")
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            for ln in f.readlines()[-25:]:
                print("      " + ln.rstrip())
    except Exception as e:
        print(f"      （读日志失败：{e!r}）")


def find_on_panel(panel_rect, origin, needle, tries=9, notches=-4):
    """在面板上找 needle —— 一屏找不到就往下滚一点再找。

    ⚠ 为什么要滚：② 新增的整条是【追加在 ① 那批卡片后面】的
      （这样 ① 的卡片位置一个都不动，两段渲染不会整体重排 —— 决策 47）。
      所以它排在卡片流的最下面，面板一屏放不下时就在折线以下 ——
      截图里没有它，但它是好好渲染出来了的。
      不滚的话，这条断言会顶着「功能坏了」的名字红，实际只是它在屏幕外，
      而「功能坏了」和「在屏幕外」修法完全不同。

    ⚠ 只往下滚：调用它的顺序必须和卡片在流里的顺序一致（上面的先找）。
      一次滚 4 格 ≈ 12 行，远小于面板高度，不会一步跨过整张卡。
    """
    shot = os.path.join(HERE, "_ai_panel_result.png")
    cx = panel_rect[0] + panel_rect[2] // 2
    cy = panel_rect[1] + panel_rect[3] // 2
    for i in range(tries):
        BOX.grab(panel_rect, shot)
        if BOX.ocr_find(shot, needle, origin):
            return True
        if i < tries - 1:
            BOX.wheel((cx, cy), notches)
            time.sleep(0.4)
    return False


def run(app):
    pw = shell_mod.PANEL_W

    print("\n⓪ 先确认示例笔记真的在框选区域里")
    # ⚠ 这一步是环境前置检查，不是功能断言。
    #   缺了它，「笔记被别的窗口盖住」会伪装成「候选词没进 prompt」那一项失败 ——
    #   一个环境问题顶着功能问题的名字出现，最费时间。
    note_shot = os.path.join(HERE, "_ai_note.png")
    seen_note = False
    for _ in range(8):
        BOX.grab(BOX.NOTE_RECT, note_shot)
        if BOX.ocr_find(note_shot, "Chunking", (0, 0)):
            seen_note = True
            break
        time.sleep(0.6)
    if not BOX.check("示例笔记窗口在框选区域里（没被盖住）", seen_note,
                     "把挡在 (180,180)-(1220,720) 上的窗口挪开再跑"):
        return

    print("\n① 主窗口就位")
    panel = BOX.find_window(BOX.PANEL_TITLE, app.pid)
    if not BOX.check("拿到主窗口句柄", bool(panel)):
        return
    r = win32gui.GetWindowRect(panel)
    origin = (r[0], r[1])

    # ★ 面板的截图区域从【真实窗口】推，不从 capture.primary_rect() 推。
    #   多屏下「主显示器是哪块」和「窗口在哪块」是两件事：本机 2026-09-15
    #   接了第二块屏，primary_rect() 当时给的是副屏，于是这里按副屏坐标截图，
    #   截回来一片空白，后面每条 OCR 断言都「找不到字」——
    #   看着像功能全坏了，其实一个截图基准错了。
    #   `知识库端到端联调.py` 一直是读 GetWindowRect 的，所以它当时全绿。
    panel_rect = (r[0], r[1], r[2] - r[0], r[3] - r[1])
    mon = BOX.screen_box(panel)
    BOX.check("主窗口贴【所在屏】的右边缘",
              r[2] == mon[2] and r[0] == mon[2] - pw, f"rect={r}　屏={mon}")

    shot = os.path.join(HERE, "_ai_panel0.png")
    btn = None
    for _ in range(20):
        time.sleep(0.6)
        BOX.grab(panel_rect, shot)
        btn = BOX.ocr_find(shot, "立即识别", origin)
        if btn:
            break
    if not BOX.check("界面上找到「立即识别」按钮", bool(btn)):
        return

    print("\n② 点「立即识别」→ ① 跑完 → ② 打到假 DeepSeek")
    # ⚠ 用 `click_until`，不要「activate 一次 + 点一下」。
    #   窗口不在前台时，**第一次点击会被 Windows 吃掉** —— 它只用来激活窗口，
    #   不往下传给 WebView2。这条 `框选联调.click_until` 的 docstring 里写着，
    #   那把工具当初就是为它写出来的。
    #   这个脚本原来正是「单独一下」版，于是「点了没反应」会顶着
    #   **「① 跑通（日志里有 [管线]）」** 这个名字红掉 ——
    #   看着像 ① 坏了，实际是点击根本没进 WebView。
    #   真踩过：2026-09-15 18:54 的回归就红了这一条，
    #   而 17:32 那次**同一份代码是绿的** —— 纯偶发；
    #   偶发的触发条件是「紧跟在另一个开窗脚本后面跑」，正是回归里的顺序。
    #   判据用副作用（日志里真的出现 [管线]），不用「我点过了」。
    n = BOX.click_until(panel, btn,
                        lambda: BOX.wait_for(
                            lambda: bool(read_log("[管线]")), 8),
                        "立即识别")
    t0 = time.time()
    if not n:
        print("       ★ 「立即识别」点了 4 次，日志里始终没有 [管线]。")
        print("         要分清两件事，修法完全不同：")
        print("           点击没送到     —— app 日志里【一行都没有】")
        print("           ① 真的跑不起来 —— 有别的行，只是没有 [管线]")

    # ⚠ 「正在问模型」只在 ② 飞着的这段时间里存在，跑完就被结果顶掉了。
    #   所以要在这个循环里顺手抓，不能等 [AI] 出来之后再回头找 ——
    #   （第一版就是这么写的，那项永远不可能通过。）
    pipe, ai, seen_wait = [], [], False
    shot_wait = os.path.join(HERE, "_ai_panel_wait.png")

    while time.time() - t0 < 45:
        time.sleep(0.4)
        pipe = read_log("[管线]")
        ai = read_log("[AI]")

        # ① 打印了 [管线]、② 还没回 —— 这正是该显示进度的那几秒。
        # 抓图 + OCR 要一秒多，所以只试两次，别把整个等待窗口占满。
        if pipe and not ai and not seen_wait:
            time.sleep(0.8)
            BOX.grab(panel_rect, shot_wait)
            seen_wait = bool(BOX.ocr_find(shot_wait, "正在问模型", origin))

        if ai:
            break

    if not BOX.check("① 跑通（日志里有 [管线]）", bool(pipe), dump_hint()):
        return
    print(f"       {pipe[0]}")
    if not BOX.check("② 打到假服务器并拿到回复", bool(ai), dump_hint()):
        return
    print(f"       {ai[0]}")

    print("\n③ 这次调用长什么样（硬约束都在这里）")
    BOX.check("② 只调了一次模型（硬约束：不许拆成多阶段）",
              AIT.GOT["hits"] == 1, f"实际 {AIT.GOT['hits']} 次")

    req = AIT.GOT["last"] or {}
    msgs = req.get("messages") or []
    check_system = msgs and msgs[0].get("content", "")
    import prompt as prompt_mod
    BOX.check("用的是后端默认提示词（settings 里 system_prompt 为空）",
              check_system == prompt_mod.DEFAULT_PROMPT,
              f"实际开头：{check_system[:40]!r}")
    BOX.check("Authorization 用的是设置里那个 Key",
              AIT.GOT["auth"] == "Bearer sk-test-fake", AIT.GOT["auth"])

    user_msg = msgs[1].get("content", "") if len(msgs) > 1 else ""

    # ⚠ 必须切到【候选段】里去找，不能直接 `"RAG" in user_msg`。
    #   user message 的正文就是整段笔记原文 —— 而笔记里本来就写着 RAG。
    #   于是不管 ① 捞没捞到、③ 挡没挡掉，整句查找都会绿：一条永远为真的断言。
    #   （真踩过：测试库里残留了一个 RAG，这一轮它根本没进候选，
    #     断言照样通过，靠的是原文。）
    #   切段之后它才真的在问「候选有没有被送到」。
    CAND_MARK = "【规则初筛捞出的英文候选词】"
    cand_part = user_msg.split(CAND_MARK, 1)[1] if CAND_MARK in user_msg else ""
    BOX.check("① 捞到的候选词真的进了【候选段】",
              "RAG" in cand_part and "Chunking" in cand_part,
              "" if cand_part else f"user message 里没有「{CAND_MARK}」这一节")

    print("\n④ 等待期间要有进度（② 会阻塞十几秒，不能让人对着空面板等）")
    BOX.check("面板上出现过「正在问模型」", seen_wait,
              "" if seen_wait else "② 全程没给进度提示")

    print("\n⑤ 解释真的渲染到卡片上了吗（这一步单测测不到）")
    time.sleep(2.0)
    shot2 = os.path.join(HERE, "_ai_panel_result.png")
    got_expl = got_head = False
    for _ in range(10):
        BOX.grab(panel_rect, shot2)
        got_expl = got_expl or bool(BOX.ocr_find(shot2, "假服务器", origin))
        got_head = got_head or bool(BOX.ocr_find(shot2, "AI新增", origin))
        if got_expl and got_head:
            break
        time.sleep(0.5)

    BOX.check("卡片上出现了解释正文", got_expl,
              "截图：冒烟测试\\_ai_panel_result.png" if not got_expl else "")
    # ⚠ 这条以前找的是「剔掉」—— v0.8 把「AI 剔掉了 N 个」折叠区并进了
    #   「本次没显示的 N 个词」，那个词在界面上已经不存在了。
    #   现在钉的是状态行那句「知识库 N 条（瞬时）　AI 新增 M 条（Xs）」：
    #   renderStage1 写的是「初筛/问模型/挡掉」，renderStage2 才写「AI 新增」
    #   （app.js:457），所以它出现 == ② 回来并且重绘了，而不是停在 ① 的候选列表。
    #   ⚠ 别改成「未显示 N 个」：那句挂在 `if (notShown)` 下面 ——
    #     模型把候选全采纳时它不出现，会变成一条凭模型心情红的断言。
    BOX.check("状态行被 ② 改写过（说明是 ② 重绘的，不是停在 ①）",
              got_head,
              "还在 ① 的状态行（初筛/问模型）→ ② 的结果没画上去" if not got_head else "")

    print("\n⑥ ② 返回的【整条】也要有卡片（卡片不能只由 ① 的候选表生成）")
    # ⚠ 这一节才是 v0.10 的真正回归测试，单测测不到它。
    #   形状：模型返回的术语【不在 ① 的候选表里】。
    #   `Prompt Engineering` 就是现成的用例 —— ① 只捞得出 `Prompt` / `Engineering` 两条，
    #   词组本身捞不出来（它俩挨着，但 ① 是按词块切的网，判断「合起来是不是一个说法」是 ② 的活）。
    #
    #   改之前这里必红：卡片是 `bucketize(r1)` 拿着 ① 的候选表生成的
    #   （app.js 的 renderStage2），key 不在候选表里的 entry **一张卡都不生成** ——
    #   整条的解释凭空消失，而它组成的那些单词卡片也拿不到解释。
    #   也就是说：只改提示词会让这个功能【比现在更差】。
    BOX.activate(panel)          # 滚轮要窗口在前台，否则滚的是别的窗口
    got_phrase = find_on_panel(panel_rect, origin, "整条词组")
    BOX.check("② 返回的整条生成了卡片（术语不在 ① 候选表里也画得出来）",
              got_phrase,
              "" if got_phrase else
              "整屏滚到底也找不到「整条词组」—— 卡片只由 ① 的候选表生成，"
              "整条的解释根本没画出来")

    # 同一个问题的另一头：组成整条的那两个单词，不该再各占一张卡。
    # 「本次没显示的词」里要出现「已并入整条」这一行 ——
    # 把它们归到「AI 未采纳」下是不实的：模型采纳了，只是作为那一整条的一部分。
    # ⚠ 找「并入整条」而不是「整条」：上面那张整条卡片的解释正文里就有「整条词组」，
    #   只找「整条」会把它自己的卡片认成折叠区。
    got_merged = find_on_panel(panel_rect, origin, "并入整条")
    BOX.check("被整条折进去的单词进了「已并入整条」那一行",
              got_merged,
              "" if got_merged else
              "「本次没显示的词」里没有「已并入整条」—— 折进去的词要么还在单独成卡，"
              "要么被错归到「AI 未采纳」")

    print("\n       结果截图：冒烟测试\\_ai_panel_result.png")


def dump_hint():
    print("       --- app 日志尾部 ---")
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            for ln in f.read().splitlines()[-25:]:
                if ln.strip() and "rapidocr" not in ln.lower():
                    print("       | " + ln)
    except FileNotFoundError:
        print("       | （日志文件不存在）")
    return ""


if __name__ == "__main__":
    sys.exit(main())
