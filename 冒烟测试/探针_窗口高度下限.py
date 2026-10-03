# -*- coding: utf-8 -*-
"""窗口能被摆到多矮?—— 量「折叠」那条链路的下限在哪。

⚠️ **不并进 `全部回归.py`**（同 `验锁屏判据.py` 的性质）：它是**一次性探针**，
   用来在排查时把一条曲线量出来，不是每轮都要跑的断言。
   常驻的那两条断言在 `跑50条.py` 的 C03 里（见 `进展文档.md` 附录 O.4）。

背景（2026-09-24，附录 O）：
    C03 报「折叠之后到不了目标高度」，而**说对不上的是产品自己**
    （`shell.set_rect()` 摆完回读核对，打 `[问了吗] 摆位对不上…`）。
    这个探针把同一个窗口按一串高度挨个摆一遍，把**回读到的真实高度**打出来 ——
    下限在哪一眼就能看出来。

    ★ 当时量到 **150**：pywebview 默认 `min_size=(200, 100)` × 1.5 = 300×150
      写进 WinForms 的 `MinimumSize`。修法给 `create_window` 传了
      `min_size=(200, 40)`，下限降到 **60**（本机 150% 下）。

    ⚠️ 修完之后，这个探针**原来的结论文案就变成假话了** ——
      它写死的是「`COLLAPSED_H = 42` 落在下限以下，折叠根本做不到」，
      而修完产品已经不再问 42 了（走 `shell.collapsed_h_for()` = 63）。
      **判据被改掉时，诊断它的代码必须跟着一起改**（同附录 K.4：
      诊断代码会变成它自己当初要消灭的那个错误）。
      所以现在它**不写死任何结论**，而是**去问产品要那个数**再比。

    $ python "Desktop\\问了吗\\冒烟测试\\探针_窗口高度下限.py"
"""
import importlib.util
import os
import sys
import time

import win32api
import win32con
import win32gui

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location(
    "runner50", os.path.join(HERE, "跑50条.py"))
R = importlib.util.module_from_spec(spec)
sys.modules["runner50"] = R
spec.loader.exec_module(R)

# 想试的高度（物理像素）。42 = 改之前的 `shell.COLLAPSED_H`（CSS 数被当物理用），
# 320 = `shell.BAR_H`（上下停靠时的高度）。
HEIGHTS = [42, 50, 60, 63, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 200, 320]

CASE_W = 520            # 用 `shell.PANEL_W` 的宽度去试，和真折叠时一致


def main():
    app, panel, rect0 = R.bring_up("PROBE")
    if not panel:
        print("窗口没起来，探针没法跑")
        return 1

    try:
        l, t, _, _ = rect0
        want_h = R.main.shell_mod.collapsed_h_for(panel)
        scale = R._scale(panel)
        print(f"面板 hwnd={panel}　初始 rect={rect0}（高 {rect0[3] - rect0[1]}）")
        print(f"本机缩放：{scale}")
        print(f"★ 产品要的折叠高度（`shell.collapsed_h_for`）：{want_h} 物理像素")
        print(f"Windows 说的最小追踪尺寸："
              f"x={win32api.GetSystemMetrics(win32con.SM_CXMINTRACK)} "
              f"y={win32api.GetSystemMetrics(win32con.SM_CYMINTRACK)}")
        print()
        print(f"{'想要高':>8}　{'实际高':>8}　对得上吗")
        print("-" * 34)

        # ⚠️ 收的是**摆进去了**的那些高度，不是摆不进去的 ——
        #   第一版记的是「第一个失败的高度」，于是它把 42 报成下限。
        #   而 42 恰恰是那个**被夹上去**的值：它自己摆不进去，
        #   更矮的只会更摆不进去。**下限是能摆进去的最小那个数。**
        placeable = []
        for h in HEIGHTS:
            want = (l, t, l + CASE_W, t + h)
            win32gui.SetWindowPos(panel, win32con.HWND_TOPMOST,
                                  l, t, CASE_W, h, win32con.SWP_SHOWWINDOW)
            time.sleep(0.15)
            got = win32gui.GetWindowRect(panel)
            real = got[3] - got[1]
            ok = "✓" if got == want else "✗"
            if got == want:
                placeable.append(h)
            print(f"{h:>8}　{real:>8}　{ok}")
            # 摆回全高，免得下一个高度是从「已经被夹过」的状态出发
            win32gui.SetWindowPos(panel, win32con.HWND_TOPMOST,
                                  l, t, CASE_W, 1600, win32con.SWP_SHOWWINDOW)
            time.sleep(0.1)

        print()
        floor = min(placeable) if placeable else None
        if floor is None:
            print("★ 试过的高度**一个都摆不进去** —— 那下限比最小的那个还高。")
        else:
            print(f"★ 下限在 {floor} 物理像素（比它矮的一律被夹住，回读还是 {floor}）。")
            if floor > min(HEIGHTS):
                print(f"  （{min(HEIGHTS)} 和它以下的都摆不进去，"
                      f"说明下限确实卡在这里）")
        # ★ 结论**现算**，不写死 —— 见文件头那段。
        if floor is None:
            print(f"⚠ 量不出下限，没法判断产品要的 {want_h} 够不够得着。")
        elif want_h >= floor:
            print(f"★ 产品要 {want_h}，在下限之上 —— **折叠够得着**。")
        else:
            print(f"★★ 产品要 {want_h}，却在下限 {floor} 之下 —— "
                  f"**折叠到不了它自称的高度**（这是真缺陷，见附录 O）。")
        return 0
    finally:
        R.kill(app)


if __name__ == "__main__":
    sys.exit(main())
