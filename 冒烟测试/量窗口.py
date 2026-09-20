# -*- coding: utf-8 -*-
"""对活着的窗口用同一把尺子重新量一遍（修掉上次苹果比橘子的错）。"""
import ctypes
import win32api
import win32con
import win32gui

hwnd = win32gui.FindWindow(None, "问了吗")
if not hwnd:
    raise SystemExit("窗口已经不在了（可能被关了）")

# ── 窗口 rect ─────────────────────────────────────────────
l, t, r, b = win32gui.GetWindowRect(hwnd)
w, h = r - l, b - t

# ── 同一个坐标系下的显示器 rect（关键：Apples to Apples）──
mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
info = win32api.GetMonitorInfo(mon)
ml, mt, mr, mb = info["Monitor"]
mw, mh = mr - ml, mb - mt

# ── DPI ──────────────────────────────────────────────────
dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
scale = dpi / 96.0

print("=" * 60)
print("  窗口几何复测（统一坐标系）")
print("=" * 60)
print(f"  显示器 DPI        : {dpi}  → 缩放 {scale * 100:.0f}%")
print(f"  显示器 rect       : ({ml}, {mt}) - ({mr}, {mb})   尺寸 {mw} x {mh}")
print(f"  窗口   rect       : ({l}, {t}) - ({r}, {b})   尺寸 {w} x {h}")
print()

print("  判定（全部用同一坐标系）:")

# 1. 右边缘是否贴住显示器右边
gap_right = mr - r
print(f"    贴右边    : 窗口右边缘 {r} vs 显示器右边缘 {mr}   → 缝隙 {gap_right}px   "
      f"{'✓ 通过' if abs(gap_right) <= 2 else '✗ 失败'}")

# 2. 上下是否顶满
gap_top, gap_bottom = t - mt, mb - b
print(f"    顶住上边  : 缝隙 {gap_top}px    {'✓' if abs(gap_top) <= 2 else '✗'}")
print(f"    顶住下边  : 缝隙 {gap_bottom}px    {'✓' if abs(gap_bottom) <= 2 else '✗'}")

# 3. 属性
ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
print(f"    始终置顶  : {bool(ex & win32con.WS_EX_TOPMOST)}   "
      f"{'✓' if ex & win32con.WS_EX_TOPMOST else '✗'}")
print(f"    无边框    : {not bool(style & win32con.WS_CAPTION)}   "
      f"{'✓' if not style & win32con.WS_CAPTION else '✗'}")

print()
print(f"  窗口宽度换算回逻辑像素: {w / scale:.0f}px  (目标 360px)")
