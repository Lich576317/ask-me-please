# -*- coding: utf-8 -*-
"""自动通道 端到端联调 —— 真开窗口，真按开关，真等着它自己跑。

    python "Desktop\\问了吗\\冒烟测试\\自动通道端到端联调.py"
    跑之前请把别的「问了吗」关掉 —— 脚本会自己起干净实例。

────────────────────────────────────────────────────────
和 `自动通道联调.py` 的分工：

    那个测判决【逻辑】（49 项，纯内存，注入假时钟，一秒跑完）
    这个测【接线】通不通：开关 → settings 落盘 → 监视线程 → 前端轮询
                        → runPipeline → 假 DeepSeek → 卡片

逻辑全绿也说明不了接线是通的 —— 「后端说该跑了」和「前端真的跑了」
中间隔着三个 js_api 方法，任何一处名字写错都是静默失效：
开关照样变色，什么都不会发生。所以这里必须真等。

对面是本地假 DeepSeek，不联网、不花钱。等待阈值用 WENLEMA_AUTO_SETTLE
压到 3 秒 —— 那是联调后门，不是用户开关。
"""
import ctypes
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

# ⚠ stdout 被重定向时 Python 默认用本地编码 GBK，而这个脚本输出全是中文，
#   还会打印 ★ ⓪ 这类 GBK 里没有的符号 —— 不统一成 UTF-8 会以
#   「跑到一半 UnicodeEncodeError」的样子炸掉，看着像功能挂了。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ctypes.windll.shcore.SetProcessDpiAwareness(2)

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
WEB = os.path.join(APP, "web")
# ⚠ settings 的路径【不在这里定义】—— 唯一实现处是 `框选联调.py` 的 SETTINGS，
#   下面 `BOX = load(...)` 之后直接取 `BOX.SETTINGS`。
#   联调一律写那份临时文件，`app\settings.json`（里面是用户的真 Key）全程不打开。
#   为什么不许碰它，见 `框选联调.py` 里 SETTINGS 上面那段长注释。
KB_DB = os.path.join(HERE, "_e2e_auto_kb.db")

sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import win32gui                                                   # noqa: E402

import auto as auto_mod                                           # noqa: E402
import shell as shell_mod                                         # noqa: E402


def load(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


BOX = load("boxlink", "框选联调.py")

SETTINGS = BOX.SETTINGS                   # ← 联调专用的临时 settings（见文件开头）
AIT = load("aitest", "AI接入联调.py")

FAKE_ANSWER = {"条目": [
    {"术语": "RAG", "解释": "假服务器回答：检索增强生成，先从外部资料里查出相关内容再让模型基于它作答。"},
]}

SETTLE = 3.0          # WENLEMA_AUTO_SETTLE。真用户的默认是 15（v0.13 起）
AI_DELAY = 1.0        # 假模型的思考时间，短一点，测试才不用干等
TITLEBAR_H = 44       # = style.css 里 .titlebar 的 42px + 1px 下边框 + 1px 余量。
                      #   开关高 22px、在栏内垂直居中（y 10~32），44 绰绰有余。
                      #   不能再放大到整条面板 ——「解释过的词会自动收进知识库」
                      #   那句里也有「自动」，整块找会点到知识库卡片上去。


# ── 设置文件 ──────────────────────────────────────────────────
# ⚠ 这里原来有一对 backup_settings() / restore_settings()，2026-09-15 删掉了。
#   它们围着 `app\settings.json`（用户的真 Key）转，而「备份 → 跑 → 还原」
#   是个【承诺】不是机制：有一轮 287 项全绿，用户的真 Key 却没了。
#   现在整份写在 `_e2e_settings.json` 上（见 SETTINGS），没有原件要保，
#   也就没有「还原」这一步可失败。两个阶段轮流改的还是同一个临时文件，
#   所以连着写是对的，不必再备份。
def write_settings(auto_on, notice):
    note = BOX.NOTE_RECT
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump({
            "api_key": "sk-test-fake",
            "kb_policy": "dedupe",
            "kb_dir": "",
            "system_prompt": "",
            "seen_notice": notice,
            "region": [note[0], note[1], note[2], note[3]],
            "auto_on": auto_on,
        }, f, ensure_ascii=False, indent=2)


def read_settings():
    with open(SETTINGS, encoding="utf-8") as f:
        return json.load(f)


def dump_why_not_fired(panel_rect):
    """没触发的时候，把「为什么」当场记下来。

    ⚠ 这一段存在的理由：`自动通道端到端联调` 的失败信息曾经只有
      「等了 40.0s 还是 0 次」，而下面这些原因在外面长得完全一样：

        1. 接线断了（前端没轮询 / 后端没起线程）—— 真 bug
        2. 画面一直在变，stable 攒不满 3 秒          —— 环境

      只有第 1 种该去改代码。分不开的话，一次环境问题会让人
      去翻一遍根本没坏的代码（真发生过）。

    ⚠ v0.13 之前这里还有【第三种】原因：「有人一直在动鼠标键盘，idle 攒不满」。
      触发条件去掉 idle 之后这个原因【不存在了】—— 而当时那段代码还在
      照着旧语义打「这台机器上一直有鼠标输入」。那是一句【假诊断】：
      它会把查的人推去查一个已经不存在的原因，而真原因被它盖过去。
      这个函数当初存在的唯一理由就是消灭这类误导，它自己差点成了那个错误。
      ★ 判据被删掉时，诊断它的代码必须跟着一起删。

      现在只读【一个】量：标题栏开关上那行字 —— 后端把 phase 直接写在上面了：
         「自动 · 待框选」= no_region（没框选）
         「自动 · 待确认」= notice（告知没点掉）
         「自动 · 截屏失败」= error
         「自动 · Ns」    = waiting，N 就是还差几秒
      有这行字就说明【后端线程在跑、前端也在轮询】，「接线断了」当场排除；
      N 一直贴在阈值上不下来，就说明画面一直在变。

      （这段以前还采样过一个「最大系统空闲」，用来区分上面第 2/3 种原因。
        为什么现在不采画面稳定时长：`Watcher.stable_now` 是【app 子进程里】
        那个实例的属性，而这里是【测试进程】，跨进程读不到。
        标题栏那行字本来就是后端把这个数导出来的，读它就行 ——
        顺带省掉 40 秒里约一百次多余的采样。）

      原来的写法是 grab 一张 _auto_titlebar_fail.png 就完了 ——
      图存下来了，一句都没读，日志里只剩一个空标题。图没人看等于没存。
    """
    print("       --- 为什么没触发 ---")

    png = os.path.join(HERE, "_auto_titlebar_fail.png")
    try:
        BOX.grab((panel_rect[0], panel_rect[1], panel_rect[2], TITLEBAR_H), png)
        lines = BOX.ocr_text(png)
    except Exception as e:                       # 诊断自己不许把测试带走
        print(f"       （标题栏读不出来：{e!r}）")
        return
    print(f"       面板标题栏读出：{lines if lines else '（一片空白）'}")
    joined = "".join(lines)
    if not joined:
        print("       ⚠ 标题栏一个字都没读出来 —— 先看窗口是不是被锁屏盖住了，")
        print("         再看上面 app 日志里有没有 [前端] JS报错。")
    elif "待框选" in joined:
        print("       → 后端在跑，是【没框选区域】（phase=no_region）")
    elif "待确认" in joined:
        print("       → 后端在跑，是【告知提示没点掉】（phase=notice）")
    elif "截屏失败" in joined:
        print("       → 后端在跑，是【截屏失败】（phase=error，多半是窗口被最小化）")
    else:
        print("       → 后端线程在跑、前端也在轮询（接线没断）。")
        # 标题栏那行是「自动 · Ns」，N = 还差几秒。它一直贴在阈值上不下来，
        # 就是 stable 攒不满的直接证据 —— 这是现在【唯一】能区分
        # 「画面一直在变」和「别的地方不对」的量。
        m = re.search(r"(\d+)\s*s", joined)
        if m and int(m.group(1)) >= SETTLE - 1:
            print(f"         标题栏说还差 {m.group(1)} 秒、一直贴在这 ——"
                  " 画面一直在变，stable 攒不满。")
            print("         检查框选区域里有没有东西在动：视频、自动滚动、")
            print("         光标停在区域里闪、或别的窗口盖在区域上。")
        else:
            print("         那就是画面一直在变 —— 检查框选区域里有没有东西在动")


# ── 抓屏 / 找控件 ─────────────────────────────────────────────
def panel_pngs():
    """app 和 app/web 下所有 png。用来验「决策 33：截图不留痕」。

    ⚠ 只比【文件名集合】，不比内容 —— 内容每次都不一样，
      但「有没有多出文件」才是决策 33 关心的那件事。
    """
    out = set()
    for d in (APP, WEB):
        for n in os.listdir(d):
            if n.lower().endswith(".png"):
                out.add(os.path.join(d, n))
    return out


def find_switch(panel_rect):
    """在【标题栏那一条】里找「自动」开关，返回它的屏幕坐标。

    ⚠ 必须限制在标题栏，不能整块面板找 ——
      「解释过的词会自动收进知识库」那句里也有「自动」两个字，
      整块找会点到知识库卡片上去，而那张卡片上并没有开关。
      开关上的字还会变长变短（自动 → 自动 · 3s），所以每次点之前都要重找。
    """
    strip = (panel_rect[0], panel_rect[1], panel_rect[2], TITLEBAR_H)
    shot = os.path.join(HERE, "_auto_titlebar.png")
    BOX.grab(strip, shot)
    return BOX.ocr_find(shot, "自动", (strip[0], strip[1]))


def toggle(panel, panel_rect, want):
    """按「自动」开关，直到 settings.json 里真的变成 want。返回成没成。

    ⚠ 判据是【可验证的副作用】（设置落了盘没有），不是「我点过了」。
      也**必须**走 click_until 而不是点一下 —— 窗口不在前台时，
      第一次点击会被 Windows 吃掉（只用来激活窗口），
      上一版这里直接调的 BOX.click，于是第 ③ 步偶发地红：
      日志里连一行 `[自动] 开` 都没有，但界面上它「看着点了」。
    """
    pos = find_switch(panel_rect)
    if not pos:
        return False
    return BOX.click_until(
        panel, pos,
        lambda: read_settings().get("auto_on") is want,
        "自动开关") > 0


def launch(tag):
    """起一个 app 实例。返回 (进程, 日志路径)。"""
    logpath = os.path.join(HERE, f"_app_auto_{tag}.log")
    env = dict(os.environ,
               WENLEMA_BASE_URL=f"http://127.0.0.1:{PORT}",
               WENLEMA_KB=KB_DB,
               WENLEMA_SETTINGS=SETTINGS,
               WENLEMA_AUTO_SETTLE=str(SETTLE))
    log = open(logpath, "wb")
    app = subprocess.Popen([sys.executable, "main.py"], cwd=APP, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    return app, logpath


def kill(app):
    subprocess.run(["taskkill", "/PID", str(app.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def saw_line(logpath, needle):
    try:
        with open(logpath, encoding="utf-8", errors="replace") as f:
            return any(needle in ln for ln in f)
    except FileNotFoundError:
        return False


def dump_tail(logpath, n=30):
    print("       --- app 日志尾部 ---")
    try:
        with open(logpath, encoding="utf-8", errors="replace") as f:
            for ln in f.read().splitlines()[-n:]:
                if ln.strip() and "rapidocr" not in ln.lower():
                    print("       | " + ln)
    except FileNotFoundError:
        print("       | （日志文件不存在）")


# ══════════════════════════════════════════════════════════════
def phase_one():
    """主阶段：开关能开、到点会跑、同一屏不重复跑。"""
    print("\n" + "═" * 62)
    print("阶段一：打开开关 → 画面停住 → 它自己跑")
    print("═" * 62)

    write_settings(auto_on=False, notice=True)     # 开局是【关】的
    png_before = panel_pngs()
    app, logpath = launch("1")
    print(f"app pid = {app.pid}　（WENLEMA_AUTO_SETTLE={SETTLE}）")

    try:
        return run_phase_one(app, logpath, png_before)
    finally:
        kill(app)
        time.sleep(1.5)


def run_phase_one(app, logpath, png_before):
    pw = shell_mod.PANEL_W

    print("\n⓪ 环境前置：示例笔记真的在框选区域里")
    note_shot = os.path.join(HERE, "_auto_note.png")
    seen = False
    for _ in range(8):
        BOX.grab(BOX.NOTE_RECT, note_shot)
        if BOX.ocr_find(note_shot, "Chunking", (0, 0)):
            seen = True
            break
        time.sleep(0.6)
    if not BOX.check("示例笔记窗口在框选区域里（没被盖住）", seen,
                     "把挡在 (180,180)-(1220,720) 上的窗口挪开再跑"):
        return 1

    print("\n① 主窗口就位，找得到「自动」开关")
    panel = BOX.find_window(BOX.PANEL_TITLE, app.pid)
    if not BOX.check("拿到主窗口句柄", bool(panel)):
        dump_tail(logpath)
        return 1

    # ★ 面板的截图区域从【真实窗口】推，不从 capture.primary_rect() 推。
    #   多屏下「主显示器是哪块」和「窗口在哪块」是两件事：本机 2026-09-15
    #   接了第二块屏，primary_rect() 当时给的是副屏，于是这个脚本按副屏坐标
    #   截图，截回来一片空白，后面每条 OCR 断言都「找不到字」——
    #   看着像自动通道挂了，其实只是截图基准错了。
    #   `知识库端到端联调.py` 一直是读 GetWindowRect 的，所以它当时全绿。
    # ★ 再补一道（L.11 ③.2）：先等 app 自己说「窗口已就位」再量。
    #   上面那段讲的「基准错了会静默全红」和这里是同一类事 —— 都是
    #   拿一个【还没成立】的坐标当真。日志用本相自己的 `logpath`
    #   （`_app_auto_{tag}.log`），别去读别人的。
    BOX.wait_laid_out_or_warn(app.pid, logpath)
    wr = win32gui.GetWindowRect(panel)
    origin = (wr[0], wr[1])
    panel_rect = (wr[0], wr[1], wr[2] - wr[0], wr[3] - wr[1])
    mon = BOX.screen_box(panel)
    BOX.check("主窗口贴【所在屏】的右边缘",
              wr[2] == mon[2] and wr[0] == mon[2] - pw, f"rect={wr}　屏={mon}")

    sw = None
    for _ in range(15):
        time.sleep(0.6)
        sw = find_switch(panel_rect)
        if sw:
            break
    if not BOX.check("标题栏上找到「自动」开关", bool(sw),
                     "截图：冒烟测试\\_auto_titlebar.png"):
        dump_tail(logpath)
        return 1

    print("\n② 对照组：开关是关的，画面停住再久也不该自己跑")
    print("     没有这一条，第 ④ 步「它自己跑了」可能只是碰巧 —— 比如")
    print("     某个别的路径也在调模型，而开关根本没起作用。")
    time.sleep(SETTLE + 5)
    BOX.check("关着的时候，假 DeepSeek 一次都没被调",
              AIT.GOT["hits"] == 0, f"实际被调了 {AIT.GOT['hits']} 次")

    print("\n③ 按开关 → 落盘")
    ok3 = toggle(panel, panel_rect, True)
    if not BOX.check("按下去之后 settings.json 里 auto_on=true（可验证的副作用）",
                     ok3, f"settings={read_settings().get('auto_on')!r}"
                          + ("" if ok3 else f"　{BOX.fg_info()}")):
        dump_tail(logpath)
        return 1

    print("\n④ ★ 别动这一屏，等它自己跑（本脚本的核心）")
    print(f"     阈值 {SETTLE}s + 2s 的 tick 间隔，大约 4~6 秒后应该自己跑一整条管线。")
    print("     ⚠ v0.13 起触发【只看画面】—— 用鼠标键盘不再阻止它触发，")
    print("       但别去动【框选区域里】的东西，那会一直重置稳定计时。")
    t0 = time.time()
    while time.time() - t0 < 40:
        if AIT.GOT["hits"] > 0:
            break
        # ⚠ 这里【不采样任何量】。以前采过一个「最大系统空闲」，
        #   用来区分「功能坏了」和「人还在动鼠标」—— 触发条件去掉 idle 之后
        #   那个量不再解释任何事（详见 dump_why_not_fired 的说明）。
        #   现在的判据全在标题栏那行字里，由 dump_why_not_fired 现读。
        time.sleep(0.4)
    took = time.time() - t0

    # ⚠ 明细只在【不通过】时才有意义。通过了还打一句失败时的解释
    #   （「等了 40s 还是 0 次」），全量日志里读起来就是自相矛盾 ——
    #   上一版这里就是这么写的，而同一个毛病已经在单元测试那边让判读出过一次错。
    #   所以每条的 detail 都按 cond 分开写。
    fired = AIT.GOT["hits"] >= 1
    ok = BOX.check("★ 没动这一屏，假 DeepSeek 自己收到了调用（自动通道真的通了）",
                   fired,
                   f"第一调用出现在 {took:.1f}s" if fired
                   else f"等了 {took:.1f}s 还是 0 次")
    if not ok:
        dump_tail(logpath)
        dump_why_not_fired(panel_rect)
        return 1
    print(f"       （阈值 {SETTLE}s。实测常比它多几秒 —— 现在这一屏里还有")
    print("         「点开关把焦点从笔记窗口抢走」造成的重绘，真用户那边没有。）")

    has_pipe = BOX.wait_for(lambda: saw_line(logpath, "[管线]"), 10.0)
    BOX.check("管线是【跑完整条】的（日志里有 [管线]）", has_pipe,
              "" if has_pipe else "只有 [AI] 没有 [管线]，走的不是共用管线（硬约束 4）")
    has_ai = BOX.wait_for(lambda: saw_line(logpath, "[AI]"), 10.0)
    BOX.check("② 结果入库了（日志里有 [AI]）", has_ai,
              "" if has_ai else "管线跑了但 ② 没落库")

    print("\n⑤ 结果贴到了侧栏卡片上")
    time.sleep(2.0)
    shot = os.path.join(HERE, "_auto_panel_result.png")
    got_card = False
    for _ in range(10):
        BOX.grab(panel_rect, shot)
        if BOX.ocr_find(shot, "假服务器", origin):
            got_card = True
            break
        time.sleep(0.5)
    BOX.check("卡片上出现了解释正文（自动跑的结果真的渲染了）", got_card,
              "" if got_card else "截图：冒烟测试\\_auto_panel_result.png")

    print("\n⑥ ★ 同一屏不重复跑（省钱那条）")
    hits = AIT.GOT["hits"]
    time.sleep(SETTLE * 3 + 4)                 # 跨过三个阈值周期
    quiet = AIT.GOT["hits"] == hits
    BOX.check("画面没变，就【不会】再跑一遍", quiet,
              f"停在 {hits} 次" if quiet
              else f"从 {hits} 涨到了 {AIT.GOT['hits']} —— 同一屏被反复问模型")

    print("\n⑦ 决策 33：截图不留痕")
    reg = os.path.join(APP, "_region.png")
    gone = not os.path.exists(reg)
    BOX.check("跑完之后 app\\_region.png 不存在", gone,
              "" if gone else "区域图没删掉 —— 那是用户的笔记原文")
    now_pngs = panel_pngs()
    new = now_pngs - png_before
    BOX.check("整个过程没多出任何截图文件（监视器全程走内存）",
              not new,
              "" if not new else f"多出了 {sorted(new)}")

    print("\n⑧ 再按一次 → 关掉并落盘")
    BOX.check("按回去之后 auto_on=false",
              toggle(panel, panel_rect, False),
              f"settings={read_settings().get('auto_on')!r}")
    return 0


def phase_two():
    """隐私顺序：首用告知还没关掉，自动通道一次都不许跑。

    ⚠ 这一条必须【重启 app】才能验 —— settings 是启动时读进内存的。
      而且它值得单独占一个阶段：自动通道会把笔记【主动】发去第三方，
      用户还没被告知就先外发一次，性质和「他自己点了按钮」完全不同。
      手动通道有遮罩天然挡着，自动通道没有。
    """
    print("\n" + "═" * 62)
    print("阶段二：首用告知还没关 → 自动通道必须一次都不跑")
    print("═" * 62)

    write_settings(auto_on=True, notice=False)
    app, logpath = launch("2")
    print(f"app pid = {app.pid}")

    try:
        up = BOX.wait_for(lambda: saw_line(logpath, "窗口已就位"), 25.0)
        BOX.check("app 起来了（日志里有「窗口已就位」）", up,
                  "" if up else "进程没起来？下面有日志")
        hits_before = AIT.GOT["hits"]

        # 等够「稳定计时攒满 + 一个 tick」。
        # ⚠ v0.13 之前这里要等 SETTLE + 12，那多出来的 12 秒是留给
        #   「空闲自然攒满」的 —— 触发条件去掉 idle 之后就不用了。
        time.sleep(SETTLE + 4)

        quiet = AIT.GOT["hits"] == hits_before
        BOX.check("★ 首用告知没关，画面停住再久也不触发（隐私顺序）", quiet,
                  f"画面停住 {SETTLE + 4:.0f}s，一个请求都没有" if quiet
                  else f"没告知就外发了 {AIT.GOT['hits'] - hits_before} 次")
        if AIT.GOT["hits"] != hits_before:
            dump_tail(logpath)
            return 1

        # 反过来：把告知关掉之后，同一份设置应该就该跑了 ——
        # 否则上面那条可能只是因为「自动通道根本没接上」而变绿。
        print("\n     反向对照：把 seen_notice 改成 true 重启，应该就该跑了")
        kill(app)
        time.sleep(1.5)
        write_settings(auto_on=True, notice=True)
        app2, logpath2 = launch("2b")
        try:
            up2 = BOX.wait_for(lambda: saw_line(logpath2, "窗口已就位"), 25.0)
            BOX.check("app 起来了", up2, "" if up2 else "进程没起来？下面有日志")
            t0 = time.time()
            while time.time() - t0 < 40:
                if AIT.GOT["hits"] > hits_before:
                    break
                time.sleep(0.4)
            again = AIT.GOT["hits"] > hits_before
            BOX.check("告知关掉之后就能触发了（反证上一条不是假绿）", again,
                      f"第 {time.time() - t0:.0f}s 收到调用" if again
                      else "等了 40 秒还是没动静 —— 那阶段一那些『通过』也可疑")
            if not again:
                dump_tail(logpath2)
                return 1
        finally:
            kill(app2)
            time.sleep(1.5)
        return 0
    finally:
        kill(app)


# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 62)
    print("问了吗 · 自动通道 端到端联调（假 DeepSeek，不联网不花钱）")
    print("=" * 62)
    if not BOX.require_clean_screen():
        return 1
    # ★ 先验尺子：`BOX.check` 是全部断言的唯一出口，它坏了几百项一起变绿。
    if not BOX.gate_check_selftest():
        return 2

    global PORT
    AIT.SCEN.update(status=200,
                    content=json.dumps(FAKE_ANSWER, ensure_ascii=False),
                    delay=AI_DELAY)
    AIT.GOT.update(hits=0, last=None, auth=None)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), AIT.Fake)
    srv.daemon_threads = True
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"假 DeepSeek 起在 127.0.0.1:{PORT}")

    for _p in (KB_DB, KB_DB + "-wal", KB_DB + "-shm"):
        if os.path.exists(_p):
            os.remove(_p)

    # ⚠ 陪练笔记的正文要自己写。`框选联调.py` 的 main() 里会写，
    #   但那是【它跑过之后】才有的副作用 —— 单独跑本脚本时文件可能不在，
    #   于是记事本进程起来就崩，界面表现是「示例笔记窗口不在框选区域里」，
    #   看着像环境被挡住了，其实是少了一个文件。
    open(BOX.SAMPLE, "w", encoding="utf-8").write(BOX.NOTE_TEXT)
    note = BOX.start_note()
    time.sleep(2.0)

    # ⚠ `rc` 必须先给个值：phase_one 里抛 ScreenLocked 的话，下面那两行
    #   一行都没执行，finally 之后 `rc` 是【未绑定】—— 直接读它会 NameError。
    rc = 1
    interrupted = False
    try:
        rc = phase_one()
        if rc == 0:
            rc = phase_two()
            # ⚠ phase_two 的返回值从前【没人看】：`rc = phase_two()` 之后再没出现过，
            #   于是它半路 return 1 也照样打一份完整的汇总行。
            #   ★ 这和 `知识库端到端联调.py` ⑧ 那句 return 是同一个形状 ——
            #     v0.20 的回归里它吞掉了 45 项。这里一并收口。
    except BOX.ScreenLocked:
        # 开局 `require_clean_screen()` 已经查过一次没锁 —— 所以走到这里，
        # 一定是【跑到一半才锁的】。措辞在 `BOX.lock_bail()` 里，四个脚本共用。
        interrupted = True
        BOX.lock_bail()
    finally:
        kill(note)
        srv.shutdown()

    if interrupted or rc:
        # ⚠ 不能照常打汇总行、更不能返回 0 —— 整份是【截断的】。
        #   截断的一份报成「通过 N 项」就是在撒谎，而且是把
        #   「没验过」和「验过了没问题」说成同一个样子。
        #
        # ⚠ 锁屏只是截断的【一种】。两个 phase 里那些
        #   `if not BOX.check(...): return 1` 是另一种，从前没人管。
        print("-" * 62)
        print(f"截断了：通过 {BOX._ok} 项，不通过 {BOX._fail} 项，后面的没跑")
        if not interrupted:
            print("       （是某一相里那句 if 没通过就 return 了，不是跑完了）")
        return 1

    print("-" * 62)
    print(f"通过 {BOX._ok} 项，不通过 {BOX._fail} 项")
    return 1 if BOX._fail else 0


if __name__ == "__main__":
    sys.exit(main())
