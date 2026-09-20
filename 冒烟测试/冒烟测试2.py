# -*- coding: utf-8 -*-
"""「问了吗」冒烟测试 v2 —— 修掉 v1 的三处问题后重跑

v1 的三个问题:
  1. 窗口定位拿物理坐标比逻辑坐标（苹果比橘子）→ 改用 Win32 SetWindowPos 自摆
  2. mss.mss() 已废弃 → 改用 mss.MSS()
  3. 第 5 项阈值 0.05s 是我拍脑袋定的，量的是「线程启动」不是「界面卡不卡」→ 重新设计

结果写到同目录的 结果2.txt
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
LOGFILE = os.path.join(HERE, "结果2.txt")

PANEL_W = 360

_lock = threading.Lock()


def log(msg=""):
    with _lock:
        print(msg)
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
            f.flush()


def head(n, title):
    log()
    log("=" * 62)
    log(f"  测试 {n}：{title}")
    log("=" * 62)


def result(ok, detail=""):
    log(f"  【{'通过' if ok else '失败'}】 {detail}")


# ── 主显示器物理尺寸 ──────────────────────────────────────
_mon = win32api.GetMonitorInfo(win32api.EnumDisplayMonitors()[0][0])["Monitor"]
MON_L, MON_T, MON_R, MON_B = _mon
MON_W, MON_H = MON_R - MON_L, MON_B - MON_T

open(LOGFILE, "w", encoding="utf-8").close()
log("「问了吗」技术选型冒烟测试 v2（方案 B：Python + pywebview）")
log(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
log(f"主显示器（物理坐标）：{MON_W} x {MON_H}")


# ══════════════════════════════════════════════════════════
# 测试 3：截取屏幕指定区域
# ══════════════════════════════════════════════════════════
def test_capture():
    head(3, "截取屏幕指定区域")
    try:
        import mss
    except ImportError as e:
        result(False, f"mss 没装上：{e}")
        return None

    shot = os.path.join(HERE, "capture.png")
    region = {"left": MON_L, "top": MON_T,
              "width": min(600, MON_W), "height": min(400, MON_H)}
    try:
        t = time.perf_counter()
        with mss.MSS() as sct:                      # v2 修正：mss.MSS()，不再用废弃的 mss.mss()
            img = sct.grab(region)
            mss.tools.to_png(img.rgb, img.size, output=shot)
        dt = time.perf_counter() - t
        result(True, f"截取 {img.size[0]}x{img.size[1]} 区域，耗时 {dt:.2f}s → capture.png")
        return shot
    except Exception as e:
        result(False, f"截屏异常：{type(e).__name__}: {e}")
        return None


# ══════════════════════════════════════════════════════════
# 测试 4：模拟 Ctrl+C + 还原剪贴板
# ══════════════════════════════════════════════════════════
def test_clipboard():
    head(4, "模拟 Ctrl+C + 还原剪贴板")
    try:
        import pyperclip
        import pyautogui
    except ImportError as e:
        result(False, f"依赖没装上：{e}")
        return

    pyautogui.FAILSAFE = False

    try:
        original = pyperclip.paste()
        pyperclip.copy("__问了吗_哨兵值__")
        got = pyperclip.paste()
        pyperclip.copy(original)
        restored = pyperclip.paste()
        ok = (got == "__问了吗_哨兵值__") and (restored == original)
        result(ok, "剪贴板 写入→读取→还原 往返正常" if ok else "剪贴板往返结果不符")
    except Exception as e:
        result(False, f"剪贴板操作异常：{type(e).__name__}: {e}")

    try:
        t = time.perf_counter()
        pyautogui.hotkey("ctrl", "c")
        dt = time.perf_counter() - t
        result(True, f"Ctrl+C 按键已发出，耗时 {dt:.2f}s（无异常）")
    except Exception as e:
        result(False, f"发送按键失败：{type(e).__name__}: {e}")

    log()
    log("  ⚠ 以上只验证了『机制可用』。真正的端到端")
    log("     （在飞书里选中字 → 按快捷键 → 拿到那段字）需要人工确认。")


# ══════════════════════════════════════════════════════════
# 测试 5：OCR 期间，主线程到底卡不卡
#     v2 重新设计：不再量「线程启动耗时」（那是我拍脑袋的阈值，
#     而且量错了对象）。改为：OCR 在后台跑的同时，主线程每 10ms
#     打一次卡，记录最大间隔。间隔大 = 主线程被饿着 = 真冻结。
# ══════════════════════════════════════════════════════════
def test_ocr(shot):
    head(5, "OCR 期间主线程是否被冻结")
    if not shot:
        result(False, "没有截图，跳过")
        return

    try:
        from rapidocr import RapidOCR
    except ImportError as e:
        result(False, f"rapidocr 没装上：{e}")
        return

    t = time.perf_counter()
    engine = RapidOCR()
    log(f"  引擎创建：{time.perf_counter() - t:.2f}s（只建一次，常驻复用）")

    holder = {"stop": False}
    gaps = []

    def worker():
        t0 = time.perf_counter()
        try:
            r = engine(shot)
            holder["txts"] = list(r.txts or [])
            holder["dt"] = time.perf_counter() - t0
        except Exception as e:
            holder["err"] = f"{type(e).__name__}: {e}"
        finally:
            holder["stop"] = True

    # 主线程心跳：模拟界面在 OCR 期间还要响应
    def heartbeat():
        last = time.perf_counter()
        while not holder["stop"]:
            time.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout=30)
    hb.join(timeout=2)

    if "err" in holder:
        result(False, f"OCR 异常：{holder['err']}")
        return

    txts = holder.get("txts", [])
    result(len(txts) > 0, f"后台 OCR 完成，耗时 {holder.get('dt', 0):.2f}s，识别 {len(txts)} 行")

    if gaps:
        worst = max(gaps) * 1000
        avg = sum(gaps) / len(gaps) * 1000
        log(f"  主线程心跳：{len(gaps)} 次，平均间隔 {avg:.1f}ms，最长卡顿 {worst:.1f}ms")
        result(worst < 300, f"OCR 期间主线程最长卡顿 {worst:.0f}ms")
        if worst >= 300:
            log("     → 主线程被 GIL 抢占较久。但 WebView2 的界面跑在**独立进程**，")
            log("       渲染不会跟着卡；受影响的只是 Python 侧的响应速度。")

    for line in txts[:5]:
        log(f"       {line}")


# ══════════════════════════════════════════════════════════
# 测试 1：窗口定位（v2 修正：用 Win32 自己摆）
# ══════════════════════════════════════════════════════════
def check_window():
    time.sleep(1.5)

    hwnd = win32gui.FindWindow(None, "问了吗")
    if not hwnd:
        head(1, "窗口")
        result(False, "找不到窗口句柄")
        return

    head(1, "无边框 + 吸右边 + 上下顶满 + 始终置顶")

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    log(f"  pywebview 自己摆的：({l},{t})-({r},{b})")
    if l > MON_R:
        log(f"     → x={l} 在屏幕(宽 {MON_W})外，确认 pywebview 二次缩放了坐标")

    # 按物理坐标自己摆
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOPMOST,
        MON_R - PANEL_W, MON_T, PANEL_W, MON_H - MON_T,
        win32con.SWP_SHOWWINDOW,
    )
    time.sleep(0.6)

    l2, t2, r2, b2 = win32gui.GetWindowRect(hwnd)
    log(f"  Win32 摆完：({l2},{t2})-({r2},{b2})  尺寸 {r2-l2}x{b2-t2}")
    log()

    result(abs(MON_R - r2) <= 2, f"贴住屏幕右边：缝隙 {MON_R - r2}px")
    result(abs(b2 - t2 - MON_H) <= 2, f"上下顶满：高 {b2-t2} vs {MON_H}")

    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    on_top = bool(ex & win32con.WS_EX_TOPMOST)
    result(on_top, f"始终置顶（WS_EX_TOPMOST）={on_top}")

    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    frameless = not (style & win32con.WS_CAPTION)
    result(frameless, f"无边框（无 WS_CAPTION）={frameless}")


# ══════════════════════════════════════════════════════════
# 测试 2：全局快捷键
# ══════════════════════════════════════════════════════════
HOTKEY_HITS = []
_window_ref = {}


def test_hotkey():
    head(2, "全局快捷键")
    try:
        from pynput import keyboard
    except ImportError as e:
        result(False, f"pynput 没装上：{e}")
        return

    def on_activate():
        ts = time.strftime("%H:%M:%S")
        HOTKEY_HITS.append(ts)
        log(f"  ★ 收到全局快捷键！({ts})  第 {len(HOTKEY_HITS)} 次")
        # 同时把界面点绿，肉眼可见
        try:
            w = _window_ref.get("w")
            if w:
                w.evaluate_js(
                    "var b=document.getElementById('banner');"
                    "b.textContent='★ 快捷键已触发 ×" + str(len(HOTKEY_HITS)) + "';"
                    "b.classList.add('on');"
                )
        except Exception:
            pass

    try:
        listener = keyboard.GlobalHotKeys({"<ctrl>+<alt>+q": on_activate})
        listener.daemon = True
        listener.start()
        result(True, "已注册全局快捷键 Ctrl+Alt+Q")
        log()
        log("  ⚠ 这一项必须人工验：")
        log("     1) 点开任意别的窗口（浏览器、飞书都行）")
        log("     2) 按 Ctrl+Alt+Q")
        log("     3) 右侧面板的圆点会变绿，同时本文件出现『★ 收到全局快捷键』")
    except Exception as e:
        result(False, f"注册快捷键失败：{type(e).__name__}: {e}")


HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:"Microsoft YaHei UI","Microsoft YaHei",sans-serif;background:#f2f6fa;
       color:#000;height:100vh;display:flex;flex-direction:column;overflow:hidden}
  .titlebar{height:40px;display:flex;align-items:center;justify-content:space-between;
       padding:0 10px 0 14px;background:#e8f0f8;border-bottom:1px solid #dce7f2;flex:0 0 auto}
  .titlebar b{font-size:13px;font-weight:600;letter-spacing:.3px}
  .btns{display:flex;gap:6px}
  .btns span{width:22px;height:22px;border-radius:11px;background:#d6e4f2;
       display:flex;align-items:center;justify-content:center;font-size:11px;color:#2b4a6b;
       cursor:pointer;user-select:none}
  .btns span:hover{background:#c3d8ec}
  .body{flex:1 1 auto;overflow-y:auto;padding:14px}
  .card{background:#fff;border-radius:12px;padding:12px 14px;margin-bottom:10px;
       border:1px solid #e2ecf5}
  .card h3{font-size:12px;font-weight:600;margin-bottom:6px;color:#2b4a6b}
  .card p{font-size:12px;line-height:1.7}
  .pill{display:inline-block;font-size:11px;padding:2px 9px;border-radius:999px;
       background:#e3f1e8;color:#1e6b3a;margin-right:5px}
  .hint{font-size:11px;color:#5b7a96}
  .banner{margin-top:10px;padding:16px 12px;border-radius:12px;background:#eef3f8;
       color:#7b8fa3;font-size:16px;font-weight:700;text-align:center;letter-spacing:.5px}
  .banner.on{background:#3ec46d;color:#fff}
</style></head><body>
  <div class="titlebar">
    <b>问了吗 · 冒烟测试 v2</b>
    <div class="btns"><span>—</span><span>▢</span><span>✕</span></div>
  </div>
  <div class="body">
    <div class="card">
      <h3>窗口</h3>
      <p>无边框 / 吸附右边 / 上下顶满 / 始终置顶</p>
    </div>
    <div class="card">
      <h3>全局快捷键</h3>
      <p>在任意别的窗口里按 <b>Ctrl + Alt + Q</b></p>
      <p class="hint" style="margin-top:6px">触发后下面整块横幅会变绿</p>
      <div class="banner" id="banner">等待快捷键…</div>
    </div>
    <div class="card">
      <h3>配色与圆角预演</h3>
      <p><span class="pill">浅色</span><span class="pill">冷色系</span>
         <span class="pill">圆角</span><span class="pill">黑色字体</span></p>
    </div>
  </div>
</body></html>"""


def main():
    import webview

    shot = test_capture()
    test_clipboard()
    test_ocr(shot)
    test_hotkey()

    log()
    log("=" * 62)
    log("  打开窗口，测第 1 项……")
    log("=" * 62)

    w = webview.create_window(
        "问了吗", html=HTML,
        x=0, y=0, width=PANEL_W, height=MON_H,      # 位置随便传，后面 Win32 摆正
        frameless=True, on_top=True, easy_drag=False, resizable=False,
    )
    _window_ref["w"] = w
    webview.start(check_window)

    log()
    log("=" * 62)
    log(f"  窗口已关闭。全局快捷键命中次数：{len(HOTKEY_HITS)}")
    log("=" * 62)


if __name__ == "__main__":
    main()
