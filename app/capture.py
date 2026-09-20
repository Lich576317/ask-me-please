# -*- coding: utf-8 -*-
"""屏幕截取。

⚠ 坐标系约定（本项目的头号坑，见 进展文档 附录 B）
    本文件所有坐标一律是【物理像素】，取自 mss。
    不要混用 GetSystemMetrics —— 它在 150% 缩放下给出的数字和 mss 对不上，
    而且它只认「主屏」和「虚拟桌面」两个数，多屏下压根表达不了「哪一块」。

    框选覆盖层传回来的坐标是【归一化 0~1】的，正是为了绕开这个问题：
    在 JS 侧就除以视口尺寸，Python 侧再乘显示器物理尺寸，中间不经过任何 DPI 换算。

    ⚠ **归一化是【相对某一块屏】的**，所以「哪一块」必须一路传到底：
      pick_region 定下这块屏 → 底图截的是它 → 覆盖层铺在它上面 →
      set_region 拿同一个 rect 换算回来。中途换一块，框就飞到别的屏上去了。
"""
import mss
import mss.tools

import win32api
import win32con


def monitor_rect_at(x, y):
    """含屏幕坐标 (x, y) 那块显示器的物理 rect → (left, top, width, height)。

    ★ 多显示器下**不能拿 `mss.monitors[1]` 当主屏** —— 那是 mss 自己的编号，
      和 Windows 认定的「主显示器」不是一回事。本机实测（2026-09-15，接了第二块屏后）：

          mss.monitors  [0] 虚拟桌面 (0,0)    5120×1600
                        [1] DISPLAY1 (2560,0) 2560×1600   ← 副屏
                        [2] DISPLAY5 (0,0)    2560×1440   ← 真正的主屏

      `monitors[1]` 拿到的是【副屏】。真踩过：面板在主屏停靠，
      而框选覆盖层开在副屏上 —— 用户点了「框选区域」，自己这块屏幕上
      什么变化都没有，看着像按钮坏了。

    ⚠ 这里借 win32 认屏，但数字和 mss 完全一致（都是物理像素，进程已设 DPI 感知）：
      上面那块主屏 mss 给 (0,0,2560,1440)，GetMonitorInfo 也给 (0,0,2560,1440)。
      所以混用是安全的。不安全的只有 GetSystemMetrics。

      鼠标不在任何一块屏上时（理论上不会），DEFAULTTONEAREST 给最近的一块 ——
      正好是我们想要的兜底。
    """
    mon = win32api.MonitorFromPoint((int(x), int(y)),
                                    win32con.MONITOR_DEFAULTTONEAREST)
    l, t, r, b = win32api.GetMonitorInfo(mon)["Monitor"]
    return (l, t, r - l, b - t)


def cursor_monitor_rect():
    """鼠标现在在哪块屏上 → 那块屏的物理 rect。

    决策 10「多显示器：跟随鼠标所在屏幕」就是这一句。
    """
    x, y = win32api.GetCursorPos()
    return monitor_rect_at(x, y)


def primary_rect():
    """**Windows 认定的**主显示器的物理 rect → (left, top, width, height)。

    ⚠ 别退回 `sct.monitors[1]`。那个下标在多屏下指向的是「mss 排在第一块」的屏，
      实测就是副屏 —— 本项目为此红过十条测试。要认主屏，认 `is_primary` 这个字段
      （mss 一直有，只是原来没用它）。
    """
    with mss.MSS() as sct:
        mons = sct.monitors[1:]
        for m in mons:
            if m.get("is_primary"):
                return (m["left"], m["top"], m["width"], m["height"])
        m = mons[0]                      # 兜底：老版本 mss 没这个字段
        return (m["left"], m["top"], m["width"], m["height"])


def grab_full(out_path, level=6, monitor=None):
    """截【一整块】显示器存成 png。给框选覆盖层当底图用。

    level 是 PNG 压缩级别：0 最快但文件巨大（全屏能到 8MB+，
    经本地 HTTP 传给覆盖层会明显卡顿），6 慢一点但小得多。

    monitor 不给就截主屏。**多屏下必须显式给** ——
    覆盖层铺在哪块屏上，底图就得是哪块，否则用户看到的是另一块屏的画面，
    框出来的坐标对不上他眼睛看到的东西。
    """
    l, t, w, h = monitor or primary_rect()
    with mss.MSS() as sct:
        img = sct.grab({"left": l, "top": t, "width": w, "height": h})
        mss.tools.to_png(img.rgb, img.size, output=out_path, level=level)
        return img.size


def grab_region(rect, out_path, level=6):
    """截屏幕的指定物理区域。rect = (left, top, width, height)。"""
    left, top, w, h = rect
    with mss.MSS() as sct:
        img = sct.grab({"left": left, "top": top, "width": w, "height": h})
        mss.tools.to_png(img.rgb, img.size, output=out_path, level=level)
        return img.size


def grab_region_bytes(rect):
    """截屏幕的指定物理区域 → 原始 RGB 字节。**不落盘。**

    给自动通道用。它每 2 秒截一帧，只为看「画面变没变」——
    落盘的话每 2 秒产生一个文件，而决策 33 要求截图不留痕：
    `recognize()` 有 finally 删图，但 `close_app()` 结尾那句 `os._exit(0)`
    **会跳过所有 finally**。高频率截屏会把这个窗口放大到不能忽略。
    走内存就没有「文件没删掉」这回事。

    ⚠ **每次都用【当前线程】新建一个 mss 实例，用完立刻关。不许缓存、不许跨线程复用。**
      这条是拿一次真实的静默失败换来的，很反直觉，别省这一步：

      `mss.MSS()` 在构造时就 `GetWindowDC(0)` 并把它存下来当源 DC。**这个 DC 归
      【调用它的那个线程】所有** —— 线程一退出，Windows 就把它收回去。此后拿这个
      实例去 BitBlt，会失败而且 `GetLastError()` 是 0，报错就只有一句
      「Windows graphics function failed (no error provided): BitBlt」，
      连个错误码都不给。

      真踩到的形状：pywebview 每次 js_api 调用都新开一条线程，`auto_set()` 里那次
      立刻 tick 先建了实例 → 这条线程随即退出 → DC 被收走 → 之后监视线程每 2 秒
      复用这个实例，**永远失败**，开关照常变色，只是再也不触发。

      实测新建+关闭只要 0.66ms，而抓一帧要 6.1ms —— 缓存省下的那点根本不值当
      （占比 11%，而整条 tick 有 2 秒的预算）。
    """
    left, top, w, h = rect
    with mss.MSS() as sct:
        return sct.grab({"left": left, "top": top,
                         "width": w, "height": h}).rgb


def normalize_to_rect(nx0, ny0, nx1, ny1, monitor=None):
    """归一化坐标（0~1）→ 屏幕物理 rect。

    输入是覆盖层传来的比例值，可能是反着拖的（从右下往左上），所以先排序。

    ⚠ **monitor 必须是 `grab_full` 和 `_fit_overlay` 用的同一块屏。**
      三者不一致的话，框出来的区域会平移、甚至整个落到另一块屏上 ——
      而存进 settings 的数字看着很正常，要到截屏识别那一步才发现框错了地方。
    """
    ml, mt, mw, mh = monitor or primary_rect()
    x0, x1 = sorted((nx0, nx1))
    y0, y1 = sorted((ny0, ny1))
    left = ml + int(round(x0 * mw))
    top = mt + int(round(y0 * mh))
    width = int(round((x1 - x0) * mw))
    height = int(round((y1 - y0) * mh))
    return (left, top, width, height)
