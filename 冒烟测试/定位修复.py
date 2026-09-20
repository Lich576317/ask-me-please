# -*- coding: utf-8 -*-
"""决定性测试：pywebview 的坐标不可靠，那就建完窗口后用 Win32 自己摆。

结论只有两种：
  A) SetWindowPos 能精确摆到物理坐标 → 方案 B 存活，定位用 pywin32 兜
  B) 摆不准 → 方案 B 的窗口层确实不可控，考虑换方案 A(Electron)
"""
import os
import time
import ctypes
import threading

# DPI 感知必须在创建任何窗口之前设置
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE_V2
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import win32api
import win32con
import win32gui

HERE = os.path.dirname(os.path.abspath(__file__))
LOGFILE = os.path.join(HERE, "定位修复结果.txt")

_log_lock = threading.Lock()


def log(msg=""):
    with _log_lock:
        print(msg)
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
            f.flush()


def enum_monitors():
    """列出所有显示器（物理坐标）"""
    mons = []
    for h, _, _ in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(h)
        mons.append((info["Monitor"], info.get("Device", ""), info.get("Flags", 0)))
    return mons


open(LOGFILE, "w", encoding="utf-8").close()
log("「问了吗」窗口定位修复测试")
log(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
log()

log("① 显示器清单（物理坐标，本进程已设 PER_MONITOR_AWARE_V2）")
for i, (rect, dev, flags) in enumerate(enum_monitors(), 1):
    primary = " ← 主显示器" if flags & 1 else ""
    log(f"     [{i}] {dev}  rect={rect}  {rect[2]-rect[0]}x{rect[3]-rect[1]}{primary}")
log()


def fix_window():
    time.sleep(1.5)

    hwnd = win32gui.FindWindow(None, "问了吗")
    if not hwnd:
        log("  ✗ 找不到窗口")
        return

    log("② 窗口创建后，pywebview 自己摆的位置")
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    log(f"     rect=({l},{t})-({r},{b})   尺寸 {r-l}x{b-t}")
    if l > 2560:
        log(f"     → x={l} 已经跑到屏幕(宽 2560)外面去了。确认 pywebview 二次缩放了坐标。")
    log()

    # ── 我们按物理坐标自己摆：吸主显示器右边，上下顶满 ──
    mon = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
    ml, mt, mr, mb = mon["Monitor"]
    PANEL_W = 360
    target = (mr - PANEL_W, mt, mr, mb)
    log(f"③ 用 Win32 SetWindowPos 按物理坐标硬摆 → {target}")

    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOPMOST,
        target[0], target[1], target[2] - target[0], target[3] - target[1],
        win32con.SWP_SHOWWINDOW,
    )
    time.sleep(0.6)

    l2, t2, r2, b2 = win32gui.GetWindowRect(hwnd)
    log(f"     摆完 rect=({l2},{t2})-({r2},{b2})   尺寸 {r2-l2}x{b2-t2}")
    log()

    log("④ 判定")
    gap_r, gap_t, gap_b = mr - r2, t2 - mt, mb - b2
    ok_r = abs(gap_r) <= 2
    ok_h = abs(b2 - t2 - (mb - mt)) <= 2
    log(f"     贴右边   : 缝隙 {gap_r}px      {'✓ 通过' if ok_r else '✗ 失败'}")
    log(f"     上下顶满 : 高 {b2-t2} vs {mb-mt}   {'✓ 通过' if ok_h else '✗ 失败'}")
    log()
    if ok_r and ok_h:
        log("  ══════════════════════════════════════════════")
        log("   ★ 结论：SetWindowPos 能精确控制 → 方案 B 存活")
        log("     窗口层只需加一层 pywin32 兜底，不必换方案")
        log("  ══════════════════════════════════════════════")
    else:
        log("  ✗ 结论：连 Win32 都摆不准，窗口层不可控，需换方案")


HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
  body { margin:0; font-family:"Microsoft YaHei UI",sans-serif; background:#f2f6fa;
         color:#000; height:100vh; display:flex; flex-direction:column; }
  .tb { height:40px; display:flex; align-items:center; justify-content:space-between;
        padding:0 12px; background:#e8f0f8; border-bottom:1px solid #dce7f2; flex:0 0 auto; }
  .tb b { font-size:13px; }
  .dot { width:10px;height:10px;border-radius:5px;background:#7fb98f; }
  .bd { flex:1; padding:14px; }
  .c { background:#fff;border:1px solid #e2ecf5;border-radius:12px;padding:12px 14px; }
  .c h3 { font-size:12px;margin:0 0 6px; }
  .c p { font-size:12px;line-height:1.8;margin:0; }
</style></head><body>
  <div class="tb"><b>问了吗 · 定位修复</b><div class="dot"></div></div>
  <div class="bd"><div class="c">
    <h3>这个窗口如果正好贴住屏幕右边、上下顶满</h3>
    <p>说明用 Win32 兜底定位可行 → 方案 B 继续。</p>
  </div></div>
</body></html>"""


def main():
    import webview
    log("─" * 62)
    log("  打开窗口（故意传一组 pywebview 会摆错的坐标）……")
    log("─" * 62)

    webview.create_window(
        "问了吗", html=HTML,
        x=9999, y=0, width=360, height=1600,   # 故意乱传，全靠后面 SetWindowPos 纠正
        frameless=True, on_top=True, easy_drag=False, resizable=False,
    )
    webview.start(fix_window)


if __name__ == "__main__":
    main()
