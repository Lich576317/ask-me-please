# -*- coding: utf-8 -*-
"""框选 + 识别 联调脚本（可重跑）。

验的是这条链路：

    点「框选区域」→ 全屏覆盖层铺开 → 拖框
      → 归一化坐标回传 → 换算成屏幕物理 rect 存进 settings.json
      → 点「立即识别」→ 截屏 → OCR → 规则初筛

**为什么值得写脚本**：这条链路上每一环都踩过坑 ——
高 DPI 三套坐标系（附录 B）、覆盖层窗口摆位、pywebview 抢窗口。
手工点一次只能知道「成没成」，脚本能知道**哪一环断的**、断的时候
屏幕坐标是多少。

    python "Desktop\\问了吗\\冒烟测试\\框选联调.py"

跑之前请把别的「问了吗」关掉 —— 脚本会自己起一个干净实例。
"""
import contextlib
import ctypes
import io
import json
import os
import subprocess
import sys
import time

ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 探针必须自己 DPI 感知

import mss                                                        # noqa: E402

# ⚠ 输出重定向到管道/文件时，Python 默认用本地编码 GBK。
#   本脚本的输出【全是中文】（[通过] / [不通过] / 各种断言名），
#   一旦被 `| grep` 或 `> 日志.txt` 接管就是一片乱码 ——
#   而跑回归时正好都是这么跑的。main.py 开头改的是同一句。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import win32api                                                   # noqa: E402
import win32con                                                   # noqa: E402
import win32gui                                                   # noqa: E402
import win32process                                               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)

# ⚠ 面板宽度【必须】从 shell.py 读，不能写死。
#   v0.11 把 PANEL_W 从 380 提到 520，这个脚本里有 6 处写死的 380 ——
#   于是四条断言一起红：窗口明明摆得好好的，报出来的却是「主窗口没贴右边缘」
#   「没铺满屏高」，而且截图只截到面板左边 380px，
#   靠右的那些字（比如知识库卡片的「已设置」pill）压根没进画面，
#   于是「框完还是没更新」也红了 —— 四条里没有一条是真的坏了。
#   另外两个开窗脚本（AI端到端 / 自动通道端到端）本来就是读 shell_mod.PANEL_W 的，
#   所以它们没跟着红。这个脚本是最早写的，那时候还没有 shell.py。
import capture                                                    # noqa: E402
import shell as shell_mod                                         # noqa: E402

PANEL_W = shell_mod.PANEL_W
# 等「窗口已就位」的上限。单独拎出来是因为那句 ⚠ 文案要把秒数说出来 ——
# 写死在文案里的话，改了这个数、文案就开始撒谎（2026-09-21 变体实测过）。
LAYOUT_WAIT = 15.0
SAMPLE = os.path.join(HERE, "_sample_note.txt")
NOTE_SCRIPT = os.path.join(HERE, "_note_window.py")
APPLOG = os.path.join(HERE, "_app.log")

# ⚠⚠ 联调【永远不许】碰 `app\settings.json`（2026-09-15 真出过事）。
#
#   四个开窗脚本原来走的是「备份 → 跑 → 还原」。那是个【承诺】，不是机制：
#   有一轮 287 项跑了个全绿，用户的真 Key 却没了 —— settings.json
#   停在脚本播下去的假 Key（`sk-test-fake`）上。单个跑、按顺序连着跑、
#   再跑一遍 `全部回归.py` 用哨兵复现，哨兵每次都逐字节活下来，
#   所以到底是哪个进程在「还原」之后又把内存里那份写了回去，至今没查出来。
#
#   于是改成【从根上不碰】：联调一律用这个临时文件，
#   通过 `WENLEMA_SETTINGS` 告诉 app 去哪儿读（见 main.settings_path()）。
#   真的 settings.json 从头到尾没被打开过，就不存在「还原失败」这回事 ——
#   查不出来的竞态，最好的处理方式是不让它有东西可竞。
SETTINGS = os.path.join(HERE, "_e2e_settings.json")

PANEL_TITLE = "问了吗"
OVERLAY_TITLE = "问了吗 · 框选"

# 给记事本窗口留的位置（物理坐标）—— 框选框的就是它
NOTE_RECT = (180, 180, 1040, 540)

NOTE_TEXT = """RAG 的核心是把外部知识接进模型
Chunking 决定检索粒度，Embedding 决定语义相似度
KPI 和 GMV 是业务指标，MVP 是产品阶段
Prompt Engineering 需要反复迭代
"""

_ok = 0
_fail = 0

# run() 整段跑完了没有。
# ⚠ `run()` 里每个 `if not check(...): return` 都是【静默交卷】——
#   一个字不打就还回去，而 main() 照样打「通过 N 项，不通过 M 项」。
#   ★ `知识库端到端联调.py` 的 ⑧ 里就有一句这种 return，v0.20 的回归里
#     它把后面 45 项整段跳过，而脚本报的是「通过 24 项，不通过 4 项」。
#   main() 里那句「截断的一份报成『通过 N 项』就是在撒谎」只防住了锁屏 ——
#   而「没跑完」跟它是因为什么停的无关。旗子补上这一种。
DONE = [False]


def check(name, cond, detail="", hint=""):
    """`detail` 是【中性观察】，通过和不通过都会打出来。
    `hint` 是【只有不通过时才说的话】——「该往哪看」。

    ⚠ 两者混用会让脚本在【通过】的那一行上说反话。真发生过两次：
      · 「启动时删掉了遗留底图」通过时打的是「还在：app\\web\\_shot.png」
      · 「拖框守卫会吭声」通过时打的是「守卫那段一个字都没打」
      读者看到的是「[通过] + 一句说它没做到的话」。这类话最容易被当成
      噪音略过，于是**测试自己把自己的可信度磨掉了**。
      ★ 一句只在错误的那一边才成立的文案，等于没有文案。

    ⚠ 判据：`detail` 里不该出现「没」「还在」「一个字都」这类
      只有在失败时才为真的词 —— 有的话它就该是 `hint`。
    """
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [通过] {name}" + (f"　{detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  [不通过] {name}　{detail}" + (f"　— {hint}" if hint else ""))
    return cond


# ── 找窗口：一律按「标题 + 属于指定 pid」找 ────────────────────
def find_window(title, pid=None, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        box = []

        def cb(h, _):
            if win32gui.GetWindowText(h) != title:
                return True
            if not win32gui.IsWindowVisible(h):
                return True
            if pid is not None and win32process.GetWindowThreadProcessId(h)[1] != pid:
                return True
            box.append(h)
            return False

        win32gui.EnumWindows(cb, None)
        if box:
            return box[0]
        time.sleep(0.2)
    return 0


def stray_panels():
    """屏幕上已经开着的「问了吗」主窗口（不论属于哪个进程）。

    ⚠ 三个开窗口的脚本 docstring 里都写着「跑之前请把别的『问了吗』关掉」——
      但那是【给人看的提醒，不是代码里的检查】，靠自觉。
      而多开一个实例的后果很具体：所有实例的面板都贴在右边缘的同一块矩形上，
      截图会截到【压在上面的那个】，OCR 读出来的就是另一个实例的内容 ——
      红的却是「功能」的名字，查起来极费劲。
    """
    hits = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == PANEL_TITLE and win32gui.IsWindowVisible(h):
            hits.append((h, win32process.GetWindowThreadProcessId(h)[1]))
        return True

    win32gui.EnumWindows(cb, None)
    return hits


def require_clean_screen(timeout=3.0):
    """开局自查 + 打印该怎么修。返回 True 表示可以开跑。

    ⚠ 要留一点宽限，不能查一次就下结论：`全部回归.py` 是【串行】跑这三个脚本的，
      上一个脚本刚 `taskkill /F` 完，窗口未必已经消失干净。
      查一次就判的话，会把「上一个脚本刚退出」误报成「你自己开着两个实例」——
      那我等于亲手给跑批种了一个新的偶发失败。
    """
    # 锁屏在【最前面】查：锁着的时候下面那些检查会一条条红给你看，
    # 而它们报的原因全是错的（见 screen_locked 的说明）。
    if not require_unlocked():
        return False

    deadline = time.time() + timeout
    while True:
        stray = stray_panels()
        if not stray or time.time() >= deadline:
            break
        time.sleep(0.3)
    if not stray:
        return True
    pids = "、".join(str(p) for _, p in stray)
    print(f"  ⚠ 屏幕上已经有「{PANEL_TITLE}」在跑（pid {pids}）。")
    print("     两个实例的面板占同一块屏幕矩形，截图会截到压在上面的那个，")
    print("     于是测试读的是【别人的界面】。请先关掉它再跑：")
    print(f"         taskkill /PID {stray[0][1]} /T /F")
    return False


def fg_exe():
    """前台窗口是哪个 exe（小写文件名）。取不到就返回空串。"""
    try:
        h = win32gui.GetForegroundWindow()
        pid = win32process.GetWindowThreadProcessId(h)[1]
        ph = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                                  False, pid)
        if not ph:
            return ""
        try:
            return os.path.basename(win32process.GetModuleFileNameEx(ph, 0)).lower()
        finally:
            win32api.CloseHandle(ph)
    except Exception:
        return ""


class ScreenLocked(Exception):
    """此刻前台是锁屏界面，屏幕上的像素已经不是桌面了。

    ⚠ 它存在的唯一理由，是【让这一份被记成「没验」，而不是「坏了」】。

      开局锁屏时 `require_clean_screen()` 会拒绝开跑（⏸），
      但锁屏发生在【跑的过程中】时，从前没有任何出口。两种下场，都坏：

        · 锁屏【转换中】→ BitBlt 失败 → 崩成一个 mss traceback
          → 回归读不到汇总行，把这一份报成「❌ 通过 0」，看着像产品炸了
        · 锁屏【稳下来之后】→ BitBlt 照样成功，截到的是锁屏壁纸
          （2026-09-15 晚在真机上验过：含副屏在内三个矩形全部截到
            真实内容，std 30~58、上万种颜色，不是黑屏）
          → 脚本会一路 OCR 壁纸跑到底，把「面板上没有这几个字」红一屏，
            **每一条的原因都是错的**

      同一件事（屏幕锁了）不该有两种报法，更不该报成「产品坏了」。

    定义放在这里，不在四个脚本里各写一份 —— 同 `screen_locked()` 的道理。
    """


def lock_bail():
    """跑到一半发现锁屏了：打那句话，收工。

    ⚠ 措辞必须逐字保持「屏幕现在是【锁着的】」——
      `全部回归.py` 靠这个子串把这一份记成 ⏸（没验），而不是 ❌（坏了）。
    """
    print("")
    print("  ⚠ 屏幕现在是【锁着的】—— 跑到一半锁的。")
    print("     这不是产品坏了，是这一份【没跑完】：下面那些项等于没验。")
    print("     解锁后重跑这个脚本就行：")
    print(f"       python \"{os.path.abspath(sys.argv[0])}\"")


def screen_locked():
    """屏幕锁了吗。

    ⚠ 锁屏之后截屏拿到的是一张壁纸（本机是风景照），不是桌面。
      于是所有「面板上有没有这几个字」「笔记窗口在不在框里」的检查
      会一起变红，而它们报的原因全都指错方向 ——
      「把挡在区域上的窗口挪开再跑」这种话，跟「屏幕锁了，谁都跑不了」
      差着十万八千里。真踩过：跑回归的时候人走开了、机器锁了，
      脚本只说「示例笔记窗口不在框选区域里」，白查了一轮。

    判据是【前台窗口属于 LockApp.exe】—— 锁屏界面就是它画的。
    两个看着更该用的判据都不行，都实测过：

      × `OpenInputDesktop()` —— 锁着的时候照样成功返回句柄
        （0x1 / 0x100 / 0x40 / 0x2 四种 access mask 全部拿到非零值），
        根本分不出锁没锁
      × 按窗口标题找「锁屏界面」—— 标题随系统语言变（本机是中文的
        「Windows 默认锁屏界面」，英文机器上是 "Lock screen"）
    """
    return fg_exe() == "lockapp.exe"


def require_unlocked():
    """开跑前的第一道门。返回 True 表示桌面看得见。"""
    if not screen_locked():
        return True
    print("  ⚠ 屏幕现在是【锁着的】，截屏只能拍到锁屏壁纸。")
    print("     先解锁再跑 —— 不然下面每一条「面板上没有这几个字」")
    print("     都会红，而它们报的原因全都不对。")
    return False


# `confirm_lock()` 的两个默认参数。拎出来是因为下面那条断言要把「等多久」
# 说给人听 —— 写死在文案里的话，改了参数文案就开始撒谎
# （2026-09-21 那个「15 秒」和「这几百毫秒」都是这么来的）。
LOCK_TRIES = 12
LOCK_GAP = 0.25


def confirm_lock(tries=LOCK_TRIES, gap=LOCK_GAP):
    """截屏【已经报错】之后，再花几秒问清楚：这是锁屏，还是真截不到。

    返回 True = 锁屏（调用方去 `raise ScreenLocked`）。

    ⚠ 为什么不能当场查一次就下结论：
      **「`BitBlt` 开始报拒绝访问」和「`LockApp.exe` 成为前台窗口」
        不是同一瞬间发生的。** 屏幕正在切换的那一小段里，两个判据一个是真
        一个是假 —— 截屏已经失败了，而 `screen_locked()` 还说着「没锁」。
      只查一次就 `raise`，整个脚本就以一个 mss 的 traceback 崩掉，
      `全部回归.py` 读不到汇总行、把它报成「❌ 通过 0」。
      **那正是这段代码声称已经修好的样子。**
      实测：2026-09-15 18:16 一次，2026-09-16 00:24 又一次，两次同一个位置。

    ⚠ 这条判据【只会往「没验」那边错，不会往「验过了」那边错】：
      等满了也没等到锁屏的话，行为和不写这一段【一模一样】——
      原样把异常抛出去。所以它不可能凭空造出一个「通过」。

    ⚠ 没量过的地方如实写在这儿：**等多久才够，没测过。**
      等待时长就是 `LOCK_TRIES × LOCK_GAP`，那两个数是拍的；
      切换比它慢的话照样会崩。
      ⚠ 这里【故意不写「3 秒」】—— 写死了，改常量的人不会跟着改这行，
        于是它就变成一句在骗人的注释（2026-09-21 的「15 秒」就是这么来的）。
      （2026-09-21 起「实际等的就是 LOCK_TRIES × LOCK_GAP」有断言钉着了，
      见 `t_confirm_lock`；「这个时长够不够」仍然没测，那要有一次真锁屏才量得到。）
      真要更准，`ScreenShotError.details["error_code"]` 里带着 winerror 码
      （「拒绝访问」应当是 5 = ERROR_ACCESS_DENIED，**不随系统语言变**，
      这点比按错误文案匹配强），拿它当判据比硬等时间准得多 ——
      但那得等复现时先量一次再改，**别猜着写**。
    """
    for _ in range(tries):
        if screen_locked():
            return True
        time.sleep(gap)
    return False


def grab(rect, path):
    # ★ 截之前先问一句「现在是不是锁屏界面」。
    #   锁屏【稳下来之后】BitBlt 是【会成功】的 —— 截到的是锁屏壁纸，
    #   不报错。于是下面每一条 OCR 断言都会以「面板上没有这几个字」的样子红掉，
    #   而它们报的原因【全是错的】。见 `ScreenLocked`。
    #   （锁屏【转换中】那一种由下面的 except 兜。）
    from mss.exception import ScreenShotError
    if screen_locked():
        raise ScreenLocked()

    try:
        with mss.MSS() as sct:
            img = sct.grab({"left": rect[0], "top": rect[1],
                            "width": rect[2], "height": rect[3]})
    except ScreenShotError as e:
        # 就在上一句话之后、真正截屏之前锁掉的。
        # 判据用 confirm_lock()，【不是】当场查一次 screen_locked() ——
        # 这两个判据在锁屏切换的那一小段里不一致，只查一次会漏（见它的说明）。
        # 也不是「抛了这个异常就算锁屏」：没锁屏却截不到，那是真问题，
        # 要原样抛出去让人看见。
        if confirm_lock():
            raise ScreenLocked() from e
        raise
    mss.tools.to_png(img.rgb, img.size, output=path)
    return path


def screen_box(hwnd):
    """这个窗口所在那块显示器的 (left, top, right, bottom)。

    ★ 一切「期望几何」都从这里推，**不要**从 `capture.primary_rect()` 推。
      多屏下「主显示器是哪块」和「窗口在哪块」是两件事，本机 2026-09-15
      接了第二块屏之后它们就分家了：

        面板停靠的主屏  (0, 0, 2560, 1440)   ← 用户真正在看的那块
        当时 primary_rect() 给的  (2560, 0, 5120, 1600)   ← 副屏（mss.monitors[1]）

      三个脚本按后面那个坐标去截图，截回来是一片别的屏，
      然后每条 OCR 断言都「找不到字」—— 十条红，没有一条是真的坏了。
      从窗口自己反查就跟得住：接几块屏、面板停在哪块，都对。
      （`知识库端到端联调.py` 一直是读 `GetWindowRect` 的，所以它当时全绿。）

    返回的是 right/bottom 而不是宽高，因为调用方多半要跟 `GetWindowRect()`
    比 —— 它给的也是 right/bottom。两套混着比会在原点非零的屏上悄悄判错。
    """
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    ml, mt, mw, mh = capture.monitor_rect_at((l + r) // 2, (t + b) // 2)
    return (ml, mt, ml + mw, mt + mh)


# ── 用 OCR 在面板上找按钮：位置靠 CSS 排，写死了迟早对不上 ────
_engine = None


def ocr_find(img_path, needle, origin):
    """在截图里找含 needle 的文字块，返回它的中心（屏幕物理坐标）。"""
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    res = _engine(img_path)
    txts = list(res.txts or [])
    boxes = getattr(res, "boxes", None)
    if boxes is None:
        return None
    hit = None
    for t, b in zip(txts, boxes):
        if needle in t.replace(" ", ""):
            hit = b
            break
    if hit is None:
        return None
    xs = [p[0] for p in hit]
    ys = [p[1] for p in hit]
    return (origin[0] + int(sum(xs) / len(xs)),
            origin[1] + int(sum(ys) / len(ys)))


def ocr_text(img_path):
    """把截图里的文字【原样】读出来，一行一条。

    ⚠ 诊断用，不要拿它当断言。`ocr_find` 只回答「在不在」，
      而排查时要问的是「那一行到底写了什么」——「没找到『自动』」和
      「找到了，但写的是『自动 · 待框选』」是两回事，后者说明接线是通的。
    """
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    return [t for t in (list(_engine(img_path).txts or []))]


def activate(hwnd):
    """把窗口提到前台再点。

    ⚠ 不这么做的话，**第一次点击会被 Windows 吃掉** —— 它只用来把窗口激活，
      不往下传给 WebView2，于是按钮像没反应一样。
      实测：后台有别的窗口时必现，单独跑就正常。

    ⚠ **光调 `SetForegroundWindow` 是不够的，它会【静默失败】。**
      Windows 有一条前台锁：只有当【调用进程自己就是前台进程】时，
      `SetForegroundWindow` 才被允许；否则它直接返回 False（pywin32 里连异常都不抛），
      窗口原地不动，而调用方以为已经激活了。
      本脚本的调用方是终端/管道里的 python，多半不是前台进程 —— 所以这条经常踩到。
      症状：`click_until` 要点三四次才生效，偶尔还要多一次；
      或者换了台机器/换了启动方式就时灵时不灵。

      解法是标准做法：把本线程的输入队列**临时接到当前前台窗口那个线程上**，
      这样本线程在系统眼里就「属于」前台了，再调就放行，完了摘掉。
    """
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return
        t_fg = win32process.GetWindowThreadProcessId(fg)[0]
        t_me = win32api.GetCurrentThreadId()
        if t_fg and t_fg != t_me:
            win32process.AttachThreadInput(t_fg, t_me, True)
            try:
                win32gui.SetForegroundWindow(hwnd)
            finally:
                win32process.AttachThreadInput(t_fg, t_me, False)
        else:
            win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.3)


def click(pos):
    """点一下。用 win32 而不是 pyautogui —— 后者内部要过一层动画，
    落点会飘几个像素，框选那边一飘就对不上了。"""
    win32api.SetCursorPos(pos)
    time.sleep(0.2)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.3)


def wheel(pos, notches):
    """在 pos 处滚轮。notches 为正 = 向上滚，负 = 向下滚（一格 = 3 行左右）。

    ⚠ 给「断言只截一屏」用的：面板上的内容排不下时，最后那几张卡片在折线以下，
      截图里根本没有 —— 而它其实好好地渲染出来了。
      不滚的话，这条断言会顶着「功能坏了」的名字红，实际只是它在屏幕外。
      （`grab()` 只能截到当前视口，`PrintWindow` 那条路本机没走通，别绕。）
    """
    win32api.SetCursorPos(pos)
    time.sleep(0.1)
    for _ in range(abs(notches)):
        delta = 120 if notches > 0 else -120
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        time.sleep(0.05)
    time.sleep(0.3)


def type_text(text):
    """把一段文字打进【当前有焦点的】控件（先点一下那个输入框）。

    ⚠ 走 KEYEVENTF_UNICODE，一个字符一次按下+抬起。
      不用虚拟键码 + 键盘布局：本机是中文输入法环境，那样打 ASCII
      会被输入法截胡（打出来是候选框，或者干脆一个字符都进不去）。

    ⚠ 也【不】走剪贴板 + Ctrl+V。看着省事，但这个产品的正业就是
      读用户的剪贴板取词 —— 测试顺手改掉用户的剪贴板是越界，
      真踩过一次「取词测完，用户复制的链接没了」。
    """
    for ch in text:
        code = ord(ch)
        win32api.keybd_event(0, code, win32con.KEYEVENTF_UNICODE, 0)
        win32api.keybd_event(0, code,
                             win32con.KEYEVENTF_UNICODE | win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)
    time.sleep(0.25)


def wait_for(ok, timeout, tick=0.2):
    """反复问 ok() 直到它说是，或者超时。返回最后一次的结果。"""
    deadline = time.time() + timeout
    while True:
        if ok():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(tick)


def wait_laid_out(timeout=LAYOUT_WAIT, logpath=None):
    """等 app 自己打出「窗口已就位」。

    ⚠ 那行是 `main.on_ready` 里 `layout("right")` **之后**打的
      (`main.py` 的 on_ready)，所以它出现 = 窗口已经摆好了。

    ★ 为什么非等不可：pywebview 是【先建窗口、再回调 on_ready】—— 窗口一出现
      `EnumWindows` 就看得见它，而那会儿 `layout()` 可能【还没跑】。
      在窗口期里量几何，量到的是 pywebview 给的初始值，不是产品的摆位结果。

    ★★ 2026-09-21：那个「没能证实的 `rect=(0,0,758,1294)`」**量到了**，
      就是这里说的窗口期。20 毫秒采一次，两次都抓到同一串（下面是 UNaware
      进程读到的虚拟化坐标，×1.5 就是 框选联调 那种 aware 进程读到的）：

        t=0.558s  (101,101,621,1001) 520×900  可见=0   ← create_window 的 520×900
        t=0.600s  (0,0,505,863)               可见=0
        t=0.621s  (0,0,505,863)               可见=1   ← 窗口【已经可见】了
        t=0.643s  (1360,0,1707,1067)          可见=1   ← layout("right") 之后

      `505×863` × 1.5 = `758×1294`（Windows 的虚拟化取整：505.33→505、
      862.67→863），**和那次红的一模一样**。所以：
        · 是解释①（读早了），**不是**解释②（找错窗口）——
          全程只有 1 个同名窗口，类名 `WindowsForms10.Window.8.app…`，就是我们那张
        · 这个状态只存在 **20~40 毫秒**，而且其中大半是不可见的 ——
          所以 0.15 秒采一次的旧探针整个跳过了它，所以它只红过一次
      ★ 顺带说明为什么非等那行日志不可：几何在 `(0,0,505,863)` 上停留的那
        20 毫秒里，窗口【已经是可见的】，任何「看见窗口就算摆好了」的判据
        都拦不住它。

    ⚠ 判据用 app 自己打的那行，**不是**「轮询到 rect 变对为止」——
      后者是把断言要验的东西当成等待条件，等到了再断言它，那条断言就永远绿了。
      这行日志是 app 在摆好之后主动打的，跟「摆得对不对」是两件事，拿它当前置条件才不自欺。

    ⚠ 也**不是**「睡 N 秒」：睡多久都是猜，而这行日志是能证明的。

    ⚠ `logpath` 默认是本文件的 `_app.log`。另外三个开窗脚本各有各的日志
      （`知识库端到端联调.py` 是 `_app_kb.log`…），要用这个助手得把路径传进来 ——
      **别让它们去读别人的日志**：两个进程往同一份日志里写，判据就全建在脏数据上了
      （本文件 `run_clip` 那段的教训）。
    """
    path = logpath or APPLOG

    def ok():
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return any("窗口已就位" in ln for ln in f)
        except FileNotFoundError:
            return False
    return wait_for(ok, timeout, tick=0.1)


def dump_geom(pid):
    """把进程里所有同名窗口连【类名】/ rect / 可见性一起打出来。只在几何对不上时用。

    ★ 为什么要有它：2026-09-21 那轮 `框选联调.py` 读到 rect=(0,0,758,1294)，
      **量了、却不知道量到的是谁**。两种解释都装得下那串数字：
        ① 窗口还没摆好就被读走了
        ② `find_window` 挑中的是【另一个】同名窗口
      而这两件事的修法完全不同（一个是等，一个是改找法）。
      当时日志里只有一行数字，所以只能重跑一遍去猜 —— 这条诊断就是为了
      「下次再红时，日志自己说得出是哪种」。

    ★ 类名是 2026-09-21 补的，而它正是最后破案的那个字段：
      那次 `758×1294` 是「同名窗口几个」答不了的 —— 答案要的是
      **这一行到底是不是我们那张窗**。类名一看就知道
      （本产品的是 `WindowsForms10.Window.…`，WebView2 的辅助窗是别的）。
      没有它，`hwnd=… rect=…` 两行长得一模一样，还是得猜。
    """
    out = []

    def cb(h, _):
        if win32gui.GetWindowText(h) != PANEL_TITLE:
            return True
        try:
            if win32process.GetWindowThreadProcessId(h)[1] != pid:
                return True
        except Exception:
            return True
        out.append(f"hwnd={h} 类名={win32gui.GetClassName(h)} "
                   f"可见={bool(win32gui.IsWindowVisible(h))} "
                   f"rect={win32gui.GetWindowRect(h)}")
        return True

    win32gui.EnumWindows(cb, None)
    return ("　★ 这个进程里同名窗口 " + str(len(out)) + " 个："
            + ("；".join(out) if out else "（一个都没有）"))


def wait_laid_out_or_warn(pid, logpath, timeout=LAYOUT_WAIT):
    """`wait_laid_out` + 没等到时把现场打出来。四个开窗脚本共用这一份。

    ★ 为什么四个都要挂：2026-09-21 之前只有本文件在量几何前等这一下，
      另外三个都是 `find_window` 之后【直接】`GetWindowRect`。它们量到的
      同样是 pywebview 的初始值 —— 区别只是那三个当时没红，所以没人发现。
      「这次没红」不是「这条路径安全」。

    ⚠ `logpath` 必须传【自己那份】日志。传别人的 = 拿另一个进程的启动记录
      当自己的前置条件：对方先到位，这边就跟着放行，而这边可能压根还没摆好
      （两个进程写同一份日志时，本文件 `run_clip` 那段已经吃过一次亏）。

    ⚠ 返回值**不**当断言用 —— 没等到不是「产品坏了」，是「这次没证据说它摆好了」。
      真的没摆好的话，下面那些几何/OCR 断言照样会红，由它们去报。
    """
    if wait_laid_out(timeout, logpath):
        return True
    # ⚠ 秒数用参数，别在文案里写死（2026-09-21 变体实测：传 timeout=3，
    #   文案照样说「15 秒」—— 文案和真等的时间可以各说各话）。
    print(f"       ⚠ {timeout:g} 秒内没等到「窗口已就位」"
          f"（日志：{os.path.basename(logpath)}），几何可能是 pywebview 的初始值。"
          + dump_geom(pid))
    return False


def fg_info():
    """现在前台是哪个窗口 —— 只在失败时打印，用来一眼分辨
    「窗口没抢到前台」和「点到了但产品没反应」这两件完全不同的事。"""
    try:
        h = win32gui.GetForegroundWindow()
        return (f"当前前台窗口 = {win32gui.GetWindowText(h)!r}"
                f"（pid {win32process.GetWindowThreadProcessId(h)[1]}）")
    except Exception as e:
        return f"（读前台窗口失败：{e}）"


def click_until(panel, pos, ok, what, tries=4, wait=2.5):
    """点一个按钮，直到 ok() 说它生效了。返回第几次才成（0 = 一直没成）。

    ⚠ 为什么不能只点一下、也不能「点一下、等一会儿、再点一下」：
      窗口不在前台时，**第一次点击会被 Windows 吃掉** —— 它只用来激活窗口，
      不往下传给 WebView2。
      原来这里写的正是两下版：activate 一次 → 点 → 等 8 秒 → 再点。
      实测（连着跑另一个开窗脚本之后）那两下**都落空** ——
      app 日志里连一行都没有，说明点击根本没进 WebView。
      差别在第二下【没有重新 activate】。所以这里每一下都重新激活。

    ⚠ 判据一律用【可验证的副作用】—— 覆盖层窗口真的建出来了没有、
      设置里落了盘没有、日志多了一条没有。**不是「我点过了」**。
      OCR 会看走眼，副作用不会。
    """
    for i in range(1, tries + 1):
        activate(panel)
        click(pos)
        if wait_for(ok, wait):
            if i > 1:
                print(f"       （「{what}」点到第 {i} 次才生效 ——"
                      f" 前 {i - 1} 次被窗口激活吃掉了）")
            return i
    return 0


def drag(p0, p1, steps=14):
    """按住左键从 p0 拖到 p1，中途分几步 —— 覆盖层靠 mousemove 画选区。

    终点用整除一步步逼近，最后一跳正好落在 p1，不留零头。

    ⚠ 按下左键之前会【再核一次光标还在不在 p0】。上面那句 `SetCursorPos(p0)`
      和真正按下之间隔着 0.25 秒 —— 这期间人手碰一下鼠标，光标就被挪走了，
      于是**按下左键的位置不再是 p0**，覆盖层把人家手放的地方当成了选区起点。

      实测（2026-09-15）：存下来的区域**终点分毫不差（右边界 1220、下边界 720
      全中），起点却在拖框路径外面**（起点 y=109，而这条路径上最小的 y 是 180）
      —— 从几何上就能断定它不来自这条合成轨迹。而它报出来顶着的名字是
      「frame 拖框区域与屏幕物理坐标一致」，**一条功能断言，把「有人碰了鼠标」
      说成了「高 DPI 换算坏了」**。

    ⚠ 健康路径上这一段是**空的**：光标本来就在 p0，查一次就过，不多花时间。
      只有真的被挪走才会重设。所以它只会让「被干扰」的拖框变对，
      不会让正常的拖框变慢或变脆。
    """
    win32api.SetCursorPos(p0)
    time.sleep(0.25)

    # ★ 给「手比程序快」准备的，不是给程序准备的。
    for _ in range(4):
        cur = win32api.GetCursorPos()
        if abs(cur[0] - p0[0]) <= 1 and abs(cur[1] - p0[1]) <= 1:
            break
        # ⚠ 措辞别写死成「有人碰了鼠标」：摆不到位的原因不只有人手，
        #   桌面被别的会话占着（锁屏）时 SetCursorPos 也可能不生效。
        print(f"       ⚠ 光标没摆到 {list(p0)}（现在在 {list(cur)}）—— 重新摆回去。")
        print("         若这一行连着出现，说明有脚本之外的东西在动光标：")
        print("         多半是人手真的碰了鼠标，那就得重跑这一份。")
        win32api.SetCursorPos(p0)
        time.sleep(0.1)

    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.15)
    for i in range(1, steps + 1):
        win32api.SetCursorPos((p0[0] + (p1[0] - p0[0]) * i // steps,
                               p0[1] + (p1[1] - p0[1]) * i // steps))
        time.sleep(0.03)
    time.sleep(0.2)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.2)


# ── 起陪练环境 ────────────────────────────────────────────────
NOTE_SCRIPT_SRC = '''# -*- coding: utf-8 -*-
"""陪练窗口：把示例笔记显示在固定的屏幕位置，供框选。由联调脚本生成。

⚠ 开头那三行不是装饰，缺一样这块区域就【不一定是笔记】：

   1. **先声明 DPI 感知**。不声明的话这个进程会被 Windows 虚拟化，
      `geometry` 里的 1040x540 实际落成 1560x810，整块位置也跟着偏，
      而联调脚本是按 NOTE_RECT 去截的 —— 截到的就不是这个窗口。

   2. **置顶**。不置顶的话，用户随手点一下自己的窗口就把它盖住了，
      截出来的是别人屏幕上正在看的东西。真发生过：某一轮识别出的是
      一篇讲数字人直播的文章，候选词全是 OCR 噪声。
      （框选覆盖层在本窗口之后创建，同样是置顶，会压在它上面，不影响框选。）
"""
import ctypes
import sys
import tkinter as tk

ctypes.windll.shcore.SetProcessDpiAwareness(2)      # ★ 必须在建窗口之前

x, y, w, h = (int(v) for v in sys.argv[1:5])
path = sys.argv[5]

root = tk.Tk()
root.title("示例笔记")
root.geometry("%dx%d+%d+%d" % (w, h, x, y))
root.attributes("-topmost", True)
t = tk.Text(root, font=("Microsoft YaHei UI", 15), wrap="word")
t.insert("1.0", open(path, encoding="utf-8").read())
t.pack(fill="both", expand=True)
root.lift()
root.mainloop()
'''


def start_note():
    with open(NOTE_SCRIPT, "w", encoding="utf-8") as f:
        f.write(NOTE_SCRIPT_SRC)
    return subprocess.Popen(
        [sys.executable, NOTE_SCRIPT,
         str(NOTE_RECT[0]), str(NOTE_RECT[1]),
         str(NOTE_RECT[2]), str(NOTE_RECT[3]), SAMPLE]
    )


def reset_region():
    """把联调那份 settings 重新种一遍，模拟「装好第一次用」。

    ⚠ 区域必须清。不清的话会吃到上一轮留下的区域，
      「框选之后主窗口卡片不更新」这类问题就被掩盖掉了 —— 真发生过。

    ⚠ 种的是 `_e2e_settings.json`，不是 `app\\settings.json`（见 SETTINGS 那里的长注释）。
      所以这里不再是「改一改用户那份」，而是【整份重写】——
      反正那份本来就是我方的东西，没有别人的字段要保。
    """
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump({
            "api_key": "sk-test-fake",     # 假的：② 打不通，也就不会有真调用
            "kb_policy": "dedupe",
            "kb_dir": "",
            "system_prompt": "",
            "seen_notice": True,           # 别让首用告知的遮罩挡住界面
            "region": None,                # ← 就是这一条，本函数存在的理由
            "auto_on": False,
        }, f, ensure_ascii=False, indent=2)


def t_confirm_lock():
    """④ `confirm_lock()` 那条【从没执行过】的分支，真的跑一遍。

    ⚠ 它只在 `grab()` 的 `except ScreenShotError` 里被调到，而那条路要求
      「截屏失败了、屏幕却【没】锁」—— 本机跑了这么多轮一次都没出现过。
      也就是说从写下来到今天，这个循环【一次都没转过】。
      「写了但从没生效」和「还没写」在日志里长得一模一样（L.11 ④），
      所以要么造条件、要么写明没验过 —— 这里选造条件。

    ⚠ 用的是【换判据】不是【造锁屏】：把 `screen_locked` 换成脚本控制的假函数。
      跑的是 `confirm_lock` 自己的真循环。代价如实写在这：
      **「真锁屏时它认不认得出」这一环仍然没验** —— 那要一次真的锁屏，
      而锁屏跑回归是本项目明令禁止的（见 `全部回归.py` 开头那段）。

    ⚠ 这条判据的设计意图是「只会往『没验』那边错」。第①条钉的就是它：
      它要是往另一边错（没锁却说锁了），一次偶发的截屏失败就会被报成
      「锁屏」—— 整个脚本静默跳过，还附一句「这不是产品坏了」。
    """
    global screen_locked
    real = screen_locked
    try:
        # ① 一直没锁：必须返回 False，而且【不能】raise
        screen_locked = lambda: False
        t0 = time.time()
        got = confirm_lock()
        dt = time.time() - t0
        check("确认锁屏：一直没锁时返回 False（不会把一次截屏失败说成「锁屏」）",
              got is False, f"返回了 {got!r}")
        check(f"确认锁屏：等法真是 {LOCK_TRIES}×{LOCK_GAP:g}"
              f"={LOCK_TRIES * LOCK_GAP:g} 秒（docstring 里写的那个数）",
              abs(dt - LOCK_TRIES * LOCK_GAP) < 0.6, f"实际 {dt:.2f}s")

        # ② 第 3 次才锁上 —— 这一条才证明它【在轮询】。
        #    「查一次就下结论」正是它 docstring 里说不能做的那件事；
        #    不钉的话，把循环改成一次性的照样全绿。
        seen = {"n": 0}

        def flaky():
            seen["n"] += 1
            return seen["n"] >= 3

        screen_locked = flaky
        t1 = time.time()
        got2 = confirm_lock()
        dt2 = time.time() - t1
        check("★ 确认锁屏：中途才锁上的认得出（是轮询，不是查一次就下结论）",
              got2 is True and seen["n"] == 3,
              f"返回 {got2!r}，一共查了 {seen['n']} 次")
        check("确认锁屏：认出来就立刻收工，不把 3 秒等满",
              dt2 < 1.5, f"实际 {dt2:.2f}s")
    finally:
        screen_locked = real


def t_drag_guard():
    """④ `drag()` 里那段「光标被挪走了就摆回去」的守卫，真的跑一遍。

    ⚠ 健康路径上它是**空的**（光标本来就在 p0，第一次检查就 break），
      所以从写下来到今天一次都没转过 —— 和 `confirm_lock` 同一个形状。

    ⚠ 造条件的办法：把 `SetCursorPos` 换成一个什么都不做的桩。于是
      「摆光标」失效，守卫那段就真的跑起来了。
      **`mouse_event` 也一起换掉**：不换的话这条测试会真的按下左键、
      在用户当时的光标位置上点一下 —— 测一段代码不该有这个副作用。

    ⚠ 两条都要断言「它走到了按下那一步」（用 mouse_event 的计数），
      不能只断言「没打那行字」—— 那样 `drag` 半路抛异常、一个字没打，
      第二条也会绿。**空转通过和通过长得一样**（本项目老账）。
    """
    real_set = win32api.SetCursorPos
    real_get = win32api.GetCursorPos
    real_evt = win32api.mouse_event
    evt = {"n": 0}
    try:
        win32api.SetCursorPos = lambda p: None            # 摆了也不动
        win32api.mouse_event = lambda *a, **k: evt.__setitem__("n", evt["n"] + 1)

        # ① 起点摆不到（用真光标位置：它不可能正好在 99999,99999）
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drag((99999, 99999), (99999, 99999), steps=2)
        out = buf.getvalue()
        check("★ 拖框守卫：光标摆不到起点时它会吭声（这段代码从没执行过）",
              "光标没摆到" in out, f"守卫那段打了 {out.count('光标没摆到')} 行",
              hint="一行都没打 —— 要么没执行到，要么它不吭声了")
        check("拖框守卫：吭声时把「现在在哪」也说了（不然没法判断是谁在动光标）",
              "现在在" in out, f"实际输出：{out.strip()[:120]!r}")
        check("拖框守卫：吭完声照常往下拖（不是卡在那儿）",
              evt["n"] >= 1, f"mouse_event 被调了 {evt['n']} 次")

        # ② 反向对照：判据说「光标就在起点」时，守卫必须【不】吭声。
        #    没有这一条，「它吭声了」可能只是因为它无条件打那行字。
        #    ⚠ 这里连 GetCursorPos 一起换成桩：拿真光标位置的话，
        #      这 0.25 秒里人手碰一下鼠标就会让这条断言偶发地红。
        win32api.GetCursorPos = lambda: (99999, 99999)
        evt["n"] = 0
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            drag((99999, 99999), (99999, 99999), steps=2)
        out2 = buf2.getvalue()
        check("拖框守卫：反向对照 —— 光标本来就在起点时它【不】吭声",
              "光标没摆到" not in out2 and evt["n"] >= 1,
              f"输出 {out2.strip()[:80]!r}，mouse_event {evt['n']} 次")
    finally:
        win32api.SetCursorPos = real_set
        win32api.GetCursorPos = real_get
        win32api.mouse_event = real_evt


def main():
    print("=" * 62)
    print("问了吗 · 框选 + 识别 联调")
    print("=" * 62)
    if not require_clean_screen():
        return 1

    # ★ 先把两条【从没执行过】的分支跑掉（L.11 ④）。它们不需要窗口，
    #   放在这里是因为下面每条 `if not check(...): return` 都可能提前交卷 ——
    #   摆在最前面，才不会因为它们排在谁后面而跟着一起不跑。
    #   ⚠ 也【不能】摆到 `require_clean_screen()` 前面：那样锁屏时它们会跑，
    #     而 `全部回归.py` 对锁屏那一种报的是「整段没跑」—— 报的就不是实话了。
    print("\n⓪ 两条从没执行过的分支（不需要窗口，先跑掉）")
    t_confirm_lock()
    t_drag_guard()

    open(SAMPLE, "w", encoding="utf-8").write(NOTE_TEXT)
    reset_region()

    log = open(APPLOG, "wb")
    note = start_note()
    time.sleep(2.0)

    # ⚠ WENLEMA_KB：v0.8 起 app 一启动就会建知识库（main.Api.__init__ 里 _connect）。
    #   不指的话，本脚本每跑一次就在用户的 app\ 下留一个 app\kb.db。
    #   本脚本只跑 ①、不调模型，所以那个库是【空的】，不会写进假数据 ——
    #   但它长得和「用户用过一次」一模一样，而且将来给这脚本加一步 AI 时
    #   会开始静默地写脏用户真正的库。另外三个联调脚本都指开了，这里也指开。
    #   和 WENLEMA_BASE_URL 一个性质：留给联调脚本的接口，不是给用户的开关。
    KB_DB = os.path.join(HERE, "_box_kb.db")
    for _p in (KB_DB, KB_DB + "-wal", KB_DB + "-shm"):
        if os.path.exists(_p):
            os.remove(_p)

    # ★ 先【种】一张假的框选底图，看程序起来时会不会把它删掉。
    #
    #   `_shot.png` 是框选底图，内容是【用户整个桌面】—— 决策 33 要求用完即删。
    #   但「用完」这个前提不总成立：覆盖层还没关程序就被杀了（强杀、崩溃、
    #   或者用户从任务栏直接退出），底图就留在盘上了。正常路径下用户
    #   不会再点一次框选，于是它就永远躺在那儿 —— 真发生过，躺了一整天。
    #   所以启动时兜一次底，这条断言就是钉这个兜底的。
    #
    #   ⚠ 种的是【几个字节的假文件】，不是真截图 —— 测试不该往用户盘上
    #     放任何真实屏幕内容，哪怕待会儿会被删掉。
    PLANT = os.path.join(APP, "web", "_shot.png")
    try:
        os.makedirs(os.path.dirname(PLANT), exist_ok=True)
        with open(PLANT, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"planted-by-test")
    except Exception as e:
        print(f"  ⚠ 假底图没种上：{e!r}（下面那条断言会失去意义）")

    env = dict(os.environ, WENLEMA_KB=KB_DB, WENLEMA_SETTINGS=SETTINGS)
    app = subprocess.Popen([sys.executable, "main.py"], cwd=APP, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    print(f"app pid = {app.pid}　记事本 pid = {note.pid}")

    interrupted = False
    try:
        run(app)
    except ScreenLocked:
        # 开局 `require_clean_screen()` 已经查过一次没锁 —— 所以走到这里，
        # 一定是【跑到一半才锁的】。
        interrupted = True
        lock_bail()
    finally:
        for p in (note, app):
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if interrupted or not DONE[0]:
        # ⚠ 不能照常打汇总行、更不能返回 0。上面已经绿了的项是真的，
        #   但整份是【截断的】—— 截断的一份报成「通过 N 项」就是在撒谎，
        #   而且是本项目最忌讳的那种撒谎：把「没验过」和「验过了没问题」
        #   说成同一个样子。
        #
        # ⚠ 锁屏（interrupted）只是截断的【一种】。`run()` 里那七八句
        #   `if not check(...): return` 是另一种，从前一个人都不管 ——
        #   v0.20 的回归里 `知识库端到端联调.py` 就是这么把 45 项静默跳过去的。
        print("-" * 62)
        print(f"截断了：通过 {_ok} 项，不通过 {_fail} 项，后面的没跑")
        if not interrupted:
            print("       （是 run() 里那句 if 没通过就 return 了，不是跑完了）")
        return 1

    print("-" * 62)
    print(f"通过 {_ok} 项，不通过 {_fail} 项")
    return 1 if _fail else 0


def run(app):
    print("\n① 主窗口就位")
    panel = find_window(PANEL_TITLE, app.pid)
    if not check("拿到主窗口句柄", bool(panel)):
        return

    # ★ 程序起来的第一件事就是把上次没删干净的截图清掉（决策 33）。
    #   假底图是 main() 在启动【之前】种的 —— 种的是几个字节的假文件，
    #   不是真截图。这一条能验，靠的是窗口出现【晚于】那次清理
    #   （_clear_shots 在 Shell() 之前，窗口在 on_ready 里才建）。
    check("★ 启动时删掉了上次遗留的框选底图（整个桌面的截图不该留在盘上）",
          not os.path.exists(os.path.join(APP, "web", "_shot.png")),
          hint="还在：app\\web\\_shot.png")

    # ★ 先等 app 自己说「摆好了」，再量几何。理由见 `wait_laid_out` 的说明。
    #   不是断言，是环境说明 —— 真的没摆好的话下面两条照样会红。
    wait_laid_out_or_warn(app.pid, APPLOG)

    r = win32gui.GetWindowRect(panel)
    size = (r[2] - r[0], r[3] - r[1])
    mon = screen_box(panel)                      # 面板所在那块屏（见 screen_box 说明）
    # ⚠ 两条的 detail 在对不上时补一句 dump_geom()：那是「量到的是谁」，
    #   跟上面那串数字是两回事 —— 2026-09-21 红的那次就只留了数字。
    ok_r = r[2] == mon[2] and r[0] == mon[2] - PANEL_W
    check("主窗口贴【所在屏】的右边缘", ok_r,
          f"rect={r}　屏={mon}" + ("" if ok_r else dump_geom(app.pid)))
    ok_h = size == (PANEL_W, mon[3] - mon[1])
    check("主窗口铺满【所在屏】的屏高", ok_h,
          f"size={size}　屏={mon}" + ("" if ok_h else dump_geom(app.pid)))

    # 等 WebView 把界面画出来，否则 OCR 找不到按钮
    origin = (r[0], r[1])
    shot = os.path.join(HERE, "_panel1.png")
    btn = None
    for _ in range(20):
        time.sleep(0.6)
        grab((origin[0], origin[1], size[0], size[1]), shot)
        btn = ocr_find(shot, "框选区域", origin)
        if btn:
            break
    if not check("界面上找到「框选区域」按钮", bool(btn)):
        return
    print(f"       按钮位置 = {btn}")

    print("\n② 点「框选区域」→ 覆盖层铺开")
    ov = [0]

    def overlay_up():
        ov[0] = find_window(OVERLAY_TITLE, app.pid, timeout=0.1)
        return bool(ov[0])

    n = click_until(panel, btn, overlay_up, "框选区域")
    if not check("覆盖层窗口出现", bool(ov[0]),
                 "" if ov[0] else f"点了 {n} 次都没反应。{fg_info()}"):
        return
    time.sleep(1.2)                        # 等 SetWindowPos 把它铺满
    ro = win32gui.GetWindowRect(ov[0])
    # ★ 覆盖层铺的是【点按钮那一刻鼠标所在的那块屏】（决策 10），
    #   也就是面板所在的那块 —— 按钮就是点在上面的。
    #   原来这里写死 `(0, 0, 2560, 1600)`，那是本机单屏时代的笔记本屏尺寸；
    #   接过第二块屏之后它当场过期，而且过期的方式很坑：
    #   覆盖层当时真的开在副屏上（产品 bug，已修），可这条断言是按
    #   老数字判的，红的时候看不出「开错屏了」这四个字。
    want = mon
    check("覆盖层铺满面板所在那块屏", ro == want, f"rect={ro}　期望={want}")

    # 拖框用的是 NOTE_RECT 这组【屏幕物理坐标】，它必须落在覆盖层这块屏里 ——
    # 否则鼠标是在覆盖层外面拖，覆盖层根本收不到事件，
    # 而失败会顶着「坐标换算错了」的名字出现。先把环境说清楚。
    inside = (want[0] <= NOTE_RECT[0]
              and NOTE_RECT[0] + NOTE_RECT[2] <= want[2]
              and want[1] <= NOTE_RECT[1]
              and NOTE_RECT[1] + NOTE_RECT[3] <= want[3])
    if not check("示例笔记落在覆盖层这块屏上", inside,
                 f"笔记={NOTE_RECT}　覆盖层={want}"):
        return

    print("\n③ 拖框 —— 这里验的是高 DPI 坐标换算")
    drag((NOTE_RECT[0], NOTE_RECT[1]),
         (NOTE_RECT[0] + NOTE_RECT[2], NOTE_RECT[1] + NOTE_RECT[3]))
    time.sleep(1.5)

    with open(SETTINGS, encoding="utf-8") as f:
        s = json.load(f)
    got = s.get("region")
    want = [NOTE_RECT[0], NOTE_RECT[1], NOTE_RECT[2], NOTE_RECT[3]]
    print(f"       拖的是 {want}")
    print(f"       存下来 {got}")
    ok = bool(got) and all(abs(a - b) <= 2 for a, b in zip(got, want))
    check("frame 拖框区域与屏幕物理坐标一致（误差 ≤2px）", ok, f"got={got}")
    check("覆盖层已收起", not find_window(OVERLAY_TITLE, app.pid, timeout=0.1))

    # 主窗口要知道框选成功了 —— 不然卡片停在「拖动鼠标框选…」，
    # 用户以为没框上，接着点识别还会被拦下
    shot3 = os.path.join(HERE, "_panel_after.png")
    shown = False
    for _ in range(16):
        time.sleep(0.8)
        grab((origin[0], origin[1], size[0], size[1]), shot3)
        if ocr_find(shot3, "已设置", origin):
            shown = True
            break
    check("主窗口卡片更新成「已设置」", shown,
          "" if shown else "框完 12 秒还是没更新")

    print("\n④ 点「立即识别」→ 截屏 → OCR → 规则初筛")
    shot2 = os.path.join(HERE, "_panel2.png")
    btn2 = None
    for _ in range(12):
        grab((origin[0], origin[1], size[0], size[1]), shot2)
        btn2 = ocr_find(shot2, "立即识别", origin)
        if btn2:
            break
        time.sleep(0.5)
    if not check("找到「立即识别」按钮", bool(btn2)):
        return
    # ⚠ 同 ② 那条：窗口不在前台时第一下点击会被 Windows 吃掉。
    #   原来这里也是「activate 一次 + 点一下」，单跑能过、连着跑就偶发地一条日志都没有。
    #   判据用【后端真的打了 [管线] 没有】—— 那是点击生效的可验证副作用。
    #
    # ⚠ 判据的等待窗口必须留够，而且宁长勿短：
    #   OCR + 初筛实测约 3 秒，但那是在【点完之后】才开始跑的。
    #   窗口开小了，「其实点成功了、只是还没跑完」会被判成「没生效」→ 再点一下 →
    #   日志里出现【两条】[管线]，下面那条「只跑了一次识别」就红了。
    #   这正是 `知识库端到端联调.py` 里记过的那个坑，别在这里重演。
    #   代价只是：真的被吃掉时，多等这十几秒才重试。
    t0 = time.time()

    def pipeline_ran():
        return bool(read_pipeline_lines())

    n = click_until(panel, btn2, pipeline_ran, "立即识别", tries=3, wait=15.0)
    if n == 0:
        print(f"       点了 3 次都没反应。{fg_info()}")

    lines = []
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(0.5)
        lines = read_pipeline_lines()
        if lines:
            break
        if int(time.time() - t0) % 5 == 0:
            print(f"       等后端… 已等 {int(time.time() - t0)}s，"
                  f"日志 {os.path.getsize(APPLOG)} 字节")

    if not check("后端跑完并打印了管线数据", bool(lines), "; ".join(lines)):
        dump_log()
        return
    for ln in lines:
        print(f"       {ln}")
    if check("只跑了一次识别（点一下就该只有一条）", len(lines) == 1,
             f"实际 {len(lines)} 条"):
        pass

    grab((origin[0], origin[1], size[0], size[1]), os.path.join(HERE, "_panel3.png"))
    print("       结果截图：冒烟测试\\_panel3.png")

    print("\n⑤ 截图有没有留痕（笔记内容不该落在盘上）")
    for name, path in (("框选底图 _shot.png", os.path.join(APP, "web", "_shot.png")),
                       ("区域截图 _region.png", os.path.join(APP, "_region.png"))):
        time.sleep(0.5)
        check(f"{name} 已删除", not os.path.exists(path),
              "还在：" + path if os.path.exists(path) else "")

    DONE[0] = True      # ← 跑到这里才算【整份跑完】。见 main() 里那段说明。


def read_pipeline_lines():
    """读 app 日志里的 [管线] 行。"""
    out = []
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "[管线]" in ln:
                    out.append(ln.strip())
    except FileNotFoundError:
        pass
    return out


def dump_log():
    print("       --- app 日志 ---")
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.rstrip()
                if ln and "rapidocr" not in ln.lower():
                    print("       | " + ln)
    except FileNotFoundError:
        print("       | （日志文件不存在）")


if __name__ == "__main__":
    sys.exit(main())
