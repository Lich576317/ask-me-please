# -*- coding: utf-8 -*-
"""「问了吗」窗口壳。

职责：DPI 感知、无边框、始终置顶、吸附屏幕四边、折叠/展开。

★ 核心规矩（决策 28）
    窗口位置一律用 Win32 SetWindowPos 按【物理坐标】自己摆，
    不要相信 pywebview 的 x / y / width / height。

    本机是 150% 缩放，pywebview 会把传入坐标【再乘一次 1.5】——
    传 x=9999 实测落在 x=14998，窗口直接飘到屏幕外，而且不报错。
    详见 进展文档.md 附录 B。
"""
import ctypes
import os
import time

import win32api
import win32con
import win32gui
import win32process

TITLE = "问了吗"

# 左右停靠时的宽度（物理像素）。
#
# ⚠ 这个数不能随便调小。窗口是【物理像素】，而 CSS 拿到的是物理 ÷ 缩放
#   （本机 150% → 380 只剩 253 个 CSS 像素）。标题栏里要横着排下
#   「问了吗」+「自动 · 42s」开关 + 齿轮 + 四个窗口按钮，253 装不下 ——
#   实测「问了吗」会被折成「问了」/「吗」两行，标题栏高度跟着跳。
#   v0.11 从 380 提到 520（= 346 CSS），并在 CSS 侧加了 nowrap 兜底，
#   两道一起上：宽度够让它排得开，nowrap 保证即使不够也是被截、不是换行。
PANEL_W = 520
BAR_H = 320              # 上下停靠时的高度

# 折叠后「只剩一条标题栏」—— 这个数是【CSS 像素】，和 `.titlebar{height:42px}`
# 对齐。**它不是最终的窗口高度**：窗口是物理像素，差一个缩放比。
# 要高度一律走 `collapsed_h_for(hwnd)`，别直接拿这个常量去摆窗口。
#
# ★ 2026-09-24 修（C03 抓出来的）。原来这里写的是
#     COLLAPSED_H = 42     # 折叠后只剩标题栏的高度
#   而 `layout()` 把它**当物理像素**用 —— 同一个 42 有两层错：
#     ① 本意是「标题栏那么高」，可 42 CSS 在 150% 下是 63 物理。
#        42 物理比标题栏自己还矮，「展开」那个按钮会被切掉 ——
#        折叠变成**单程票**，用户只能重启。（`跑50条.py` 的 C03_EXPAND
#        防的就是这件事，它是目前唯一钉着这一条的断言。）
#     ② 就算按物理算，窗口也有个**下限**：pywebview 默认
#        `min_size=(200,100)`，乘 1.5 = 300×150 物理，写进 WinForms 的
#        `MinimumSize`。实测（探针见 进展文档 附录 O）：150 以下一律摆不进去，
#        SetWindowPos 之后回读还是 150。
#   后果：折叠**从来没到过**它自称的高度，而且每点一次，app 日志里就多三行
#     [问了吗] 摆位对不上（第 N 次）：想要 (…, 42)，实际 (…, 150)
#   两处一起修：`main.py` 的 `create_window(min_size=(200,40))` 把下限
#   压到 60 物理，这里再按当前 DPI 把高度算出来。
#
# ⚠ 这个常量此前**没有任何测试覆盖**（2026-09-24 全仓 grep 过「折叠」）。
COLLAPSED_H = 42

EDGES = ("right", "left", "top", "bottom")


def enable_dpi_awareness():
    """必须在创建任何窗口之前调用，否则拿到的全是缩放过的小数字。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE_V2
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def dpi_scale(hwnd):
    """该窗口所在那块屏的缩放比（1.0 = 96 DPI）。

    ⚠ **每次现取，不要缓存**：窗口可以在两块 DPI 不同的屏之间挪，
      冻住的话换屏之后折叠/展开都会摆不到位。
    ⚠ 和 `冒烟测试/跑50条.py` 的 `_scale()` 用的是**同一个 Win32 调用** ——
      那边拿它算四个窗口按钮的坐标。两边算法分叉的话，测试会去点一个
      空位置，而点空的样子是「按了没反应」，**看着像产品坏了**。
    """
    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
    except Exception:
        dpi = 96
    # ⚠ **句柄无效时它返回 0，而不是抛异常**（2026-09-24 实测：
    #   `GetDpiForWindow(0)` → 0）。只写 `except` 兜不住这一种 ——
    #   0 / 96 = 0.0，于是 `collapsed_h_for()` 算出**高度 0**，
    #   窗口被摆成一条零高度的缝，而且**不报错**。
    #   「读不出来」必须退回默认值，不能凭空变出一个 0 来。
    #   50%~500% 之外的读数一律当读失败（本机是 150%）。
    if not (48 <= dpi <= 480):
        dpi = 96
    return dpi / 96.0


def collapsed_h_for(hwnd):
    """折叠后该有的窗口高度（**物理像素**）。

    ★ 这是「折叠成多高」的**唯一实现处**：产品摆位走它，
      `跑50条.py` 的 C03 也走它 —— 断言比的是**产品自己的数**，
      不是测试这边另写一份公式（同「构建期断言必须照抄运行时读取函数」）。
      另写一份的话，改了一处、另一处照样绿。
    """
    return int(COLLAPSED_H * dpi_scale(hwnd))


def _find_own(title):
    """按标题找【属于本进程】的窗口。找不到返回 0。

    ⚠ 为什么要多这一步，而不是直接用 FindWindow：
      FindWindow 只看标题，谁建的都算。同时开着两个「问了吗」时，
      后启动的那个会抓到【先启动那个的】窗口去摆位 ——
      结果就是：旧窗口被摆到正确位置（看起来一切正常），
      新窗口自己留在原地（左上角、尺寸也不对）。
      这里栽过一次，症状是「窗口明明摆好了，但屏幕左上角还多一个」。
    """
    me = os.getpid()
    found = []

    def cb(hwnd, _):
        if win32gui.GetWindowText(hwnd) != title:
            return True
        try:
            if win32process.GetWindowThreadProcessId(hwnd)[1] == me:
                found.append(hwnd)
                return False
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return found[0] if found else 0


def find_window(title, timeout=5.0):
    """按标题等【本进程的】窗口出现，拿到句柄。拿不到返回 0。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _find_own(title)
        if hwnd:
            return hwnd
        time.sleep(0.05)
    return 0


def set_rect(hwnd, rect, tries=3, gap=0.05):
    """按物理坐标摆放窗口，**摆完回读核对**，对不上就再摆。

    为什么要回读：曾经遇到过一次摆完变成 380×100 的情况（同一份代码
    重跑三次都复现不出来）。这类偶发如果不兜住，用户看到的就是
    「侧栏只剩一条缝」，而且毫无线索。现在对不上会重试并打印。

    ⚠ 回读到的 GetWindowRect 是【窗口】rect，不是客户区 ——
      本程序是无边框窗口，两者一致。
    """
    l, t, w, h = (int(v) for v in rect)
    want = (l, t, l + w, t + h)
    got = None
    for i in range(tries):
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, l, t, w, h, win32con.SWP_SHOWWINDOW
        )
        time.sleep(gap)
        got = win32gui.GetWindowRect(hwnd)
        if got == want:
            return True
        print(f"[问了吗] 摆位对不上（第 {i + 1} 次）："
              f"想要 {want}，实际 {got}", flush=True)
    return False


def place_window(title, rect, timeout=5.0):
    """按【物理坐标】摆放任意窗口（用标题找）。

    和 Shell._place 是同一套做法 —— 位置绝不交给 pywebview。
    框选覆盖层用它铺满屏幕。
    """
    hwnd = find_window(title, timeout)
    if not hwnd:
        return False
    return set_rect(hwnd, rect)


class Shell:
    """管住一个无边框置顶窗口的位置与显隐。"""

    def __init__(self):
        self.hwnd = 0
        self.edge = "right"
        self.collapsed = False
        self._rect = None          # 展开时的 rect，折叠后用它还原

    # ── 拿句柄 ────────────────────────────────────────────
    def attach(self, timeout=6.0):
        """等 pywebview 把窗口建出来，再抓句柄。"""
        self.hwnd = find_window(TITLE, timeout)
        return bool(self.hwnd)

    # ── 显示器 ────────────────────────────────────────────
    def monitor_rect(self):
        """当前窗口所在显示器的物理 rect。

        用 MonitorFromWindow 而不是主显示器，这样将来接多显示器（决策 10）
        时能自然跟随鼠标所在屏幕，不用改这里。
        """
        mon = win32api.MonitorFromWindow(self.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(mon)
        return info["Monitor"]          # (left, top, right, bottom)

    # ── 摆位（唯一入口）────────────────────────────────────
    def _place(self, x, y, w, h):
        """按物理坐标摆放。所有位置变更都走这里，别绕开。

        实际摆位 + 回读核对在 set_rect 里，这里只记 state。
        """
        self._rect = (int(x), int(y), int(w), int(h))
        return set_rect(self.hwnd, (x, y, w, h))

    def layout(self, edge=None):
        """按停靠边算出该有的 rect，摆过去。"""
        if edge:
            self.edge = edge
        ml, mt, mr, mb = self.monitor_rect()
        mw, mh = mr - ml, mb - mt

        if self.collapsed:
            w = PANEL_W if self.edge in ("left", "right") else mw
            # ★ 走 `collapsed_h_for`，别直接用常量 —— 它是 CSS 像素，
            #   窗口要的是物理像素（见那个常量上面的长注释）。
            h = collapsed_h_for(self.hwnd)
        elif self.edge in ("left", "right"):
            w, h = PANEL_W, mh
        else:
            w, h = mw, BAR_H

        if self.edge == "right":
            x, y = mr - w, mt
        elif self.edge == "left":
            x, y = ml, mt
        elif self.edge == "top":
            x, y = ml, mt
        else:                                   # bottom
            x, y = ml, mb - h

        self._place(x, y, w, h)

    # ── 对外动作 ──────────────────────────────────────────
    def dock(self, edge):
        if edge not in EDGES:
            return False
        self.collapsed = False
        self.layout(edge)
        return True

    def collapse(self):
        """折叠成一条标题栏。再点一次还原。"""
        if self.collapsed:
            self.collapsed = False
            self.layout()
        else:
            self.collapsed = True
            self.layout()
        return self.collapsed

    def current_rect(self):
        """读回真实 rect —— 用来验证摆位确实生效了。"""
        return win32gui.GetWindowRect(self.hwnd)

    def is_topmost(self):
        ex = win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
        return bool(ex & win32con.WS_EX_TOPMOST)

    def is_frameless(self):
        style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_STYLE)
        return not (style & win32con.WS_CAPTION)
