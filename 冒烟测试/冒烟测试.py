# -*- coding: utf-8 -*-
"""「问了吗」技术选型冒烟测试 —— 方案 B:Python + pywebview

只验 5 件事能不能通，不写任何业务逻辑：
    □ 1. 无边框窗口 + 吸到屏幕右边 + 始终置顶
    □ 2. 全局快捷键（在别的窗口里按也能触发）
    □ 3. 截取屏幕指定区域
    □ 4. 模拟 Ctrl+C + 还原剪贴板
    □ 5. 后台线程跑 OCR，不冻结界面

结果写到同目录的 结果.txt
"""
import os
import sys
import time
import ctypes
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
LOGFILE = os.path.join(HERE, "结果.txt")

# ── DPI 感知必须在创建任何窗口之前设置 ────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE_V2
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32
SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)

_log_lock = threading.Lock()


def log(msg=""):
    with _log_lock:
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


# ══════════════════════════════════════════════════════════
# 窗口尺寸：吸在屏幕右边，上下顶满
# ══════════════════════════════════════════════════════════
PANEL_W = 360
WIN_X = SCREEN_W - PANEL_W
WIN_Y = 0
WIN_H = SCREEN_H

open(LOGFILE, "w", encoding="utf-8").close()
log("「问了吗」技术选型冒烟测试")
log(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
log(f"屏幕：{SCREEN_W} x {SCREEN_H}")
log(f"目标窗口：x={WIN_X}, y={WIN_Y}, 宽={PANEL_W}, 高={WIN_H}")


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
    region = {"left": 0, "top": 0, "width": min(600, SCREEN_W), "height": min(400, SCREEN_H)}
    try:
        t = time.perf_counter()
        with mss.mss() as sct:
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

    # 4a —— 剪贴板存/取/还原 能不能跑通
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

    # 4b —— 按键能不能真的发出去
    try:
        t = time.perf_counter()
        pyautogui.hotkey("ctrl", "c")
        dt = time.perf_counter() - t
        result(True, f"Ctrl+C 按键已发出，耗时 {dt:.2f}s（无异常）")
    except Exception as e:
        result(False, f"发送按键失败：{type(e).__name__}: {e}")

    log()
    log("  ⚠ 注意：以上只验证了『机制可用』。")
    log("     真正的端到端（在飞书里选中字 → 按快捷键 → 拿到那段字）")
    log("     需要人工确认，无法自动测。")


# ══════════════════════════════════════════════════════════
# 测试 5：后台线程跑 OCR，不冻结界面
# ══════════════════════════════════════════════════════════
_ocr_ready = threading.Event()


def test_ocr(shot):
    head(5, "后台线程跑 OCR")
    if not shot:
        result(False, "没有截图，跳过")
        return

    try:
        from rapidocr import RapidOCR
    except ImportError as e:
        result(False, f"rapidocr 没装上：{e}")
        return

    # 引擎只建一次（决策：常驻复用）
    t = time.perf_counter()
    engine = RapidOCR()
    log(f"  引擎创建：{time.perf_counter() - t:.2f}s")
    _ocr_ready.set()

    holder = {}

    def work():
        t0 = time.perf_counter()
        try:
            r = engine(shot)
            holder["txts"] = list(r.txts or [])
            holder["dt"] = time.perf_counter() - t0
        except Exception as e:
            holder["err"] = f"{type(e).__name__}: {e}"

    # 关键：OCR 跑在子线程里，主线程立刻返回
    t = time.perf_counter()
    th = threading.Thread(target=work, daemon=True)
    th.start()
    main_blocked = time.perf_counter() - t

    result(main_blocked < 0.05,
           f"主线程启动 OCR 只阻塞 {main_blocked * 1000:.1f}ms —— 界面不会被冻结")

    th.join(timeout=30)
    if "err" in holder:
        result(False, f"OCR 异常：{holder['err']}")
    else:
        txts = holder.get("txts", [])
        result(len(txts) > 0,
               f"后台 OCR 完成，耗时 {holder.get('dt', 0):.2f}s，识别 {len(txts)} 行")
        for line in txts[:5]:
            log(f"       {line}")


# ══════════════════════════════════════════════════════════
# 测试 1：无边框 + 吸右边 + 置顶
# ══════════════════════════════════════════════════════════
def check_window():
    head(1, "无边框窗口 + 吸到屏幕右边 + 始终置顶")
    time.sleep(1.5)                       # 等窗口真正出来

    try:
        import win32gui
        import win32con
    except ImportError as e:
        result(False, f"pywin32 没装上：{e}")
        return

    hwnd = win32gui.FindWindow(None, "问了吗")
    if not hwnd:
        result(False, "找不到窗口句柄")
        return

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top

    # 位置：右边缘是否贴住屏幕右边
    docked_right = abs(right - SCREEN_W) <= 2
    full_height = abs(h - SCREEN_H) <= 2
    result(docked_right, f"贴住屏幕右边：right={right}，屏幕宽={SCREEN_W}")
    result(full_height, f"上下顶满：高={h}，屏幕高={SCREEN_H}")

    # 置顶
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    on_top = bool(ex & win32con.WS_EX_TOPMOST)
    result(on_top, f"始终置顶（WS_EX_TOPMOST）={on_top}")

    # 无边框：没有标题栏样式位
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    frameless = not (style & win32con.WS_CAPTION)
    result(frameless, f"无边框（无 WS_CAPTION）={frameless}")

    log()
    log(f"  实际 rect：({left}, {top}) - ({right}, {bottom})  尺寸 {w}x{h}")


# ══════════════════════════════════════════════════════════
# 测试 2：全局快捷键
# ══════════════════════════════════════════════════════════
HOTKEY_HITS = []


def test_hotkey():
    head(2, "全局快捷键")
    try:
        from pynput import keyboard
    except ImportError as e:
        result(False, f"pynput 没装上：{e}")
        return

    def on_activate():
        HOTKEY_HITS.append(time.strftime("%H:%M:%S"))
        log(f"  ★ 收到全局快捷键！({HOTKEY_HITS[-1]})")

    try:
        listener = keyboard.GlobalHotKeys({"<ctrl>+<alt>+q": on_activate})
        listener.daemon = True
        listener.start()
        result(True, "已注册全局快捷键 Ctrl+Alt+Q")
        log()
        log("  ⚠ 这一项必须人工验：")
        log("     1) 点开任意别的窗口（浏览器、飞书都行）")
        log("     2) 按 Ctrl+Alt+Q")
        log("     3) 若下面出现『★ 收到全局快捷键』，即通过")
    except Exception as e:
        result(False, f"注册快捷键失败：{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════
# 界面
# ══════════════════════════════════════════════════════════
HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    background: #f2f6fa; color: #000; height: 100vh;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .titlebar {
    height: 40px; display: flex; align-items: center; justify-content: space-between;
    padding: 0 10px 0 14px; background: #e8f0f8;
    border-bottom: 1px solid #dce7f2; flex: 0 0 auto;
  }
  .titlebar b { font-size: 13px; font-weight: 600; letter-spacing: .3px; }
  .btns { display: flex; gap: 6px; }
  .btns span {
    width: 22px; height: 22px; border-radius: 11px; background: #d6e4f2;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; color: #2b4a6b; cursor: pointer; user-select: none;
  }
  .btns span:hover { background: #c3d8ec; }
  .body { flex: 1 1 auto; overflow-y: auto; padding: 14px; }
  .card {
    background: #fff; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px;
    border: 1px solid #e2ecf5;
  }
  .card h3 { font-size: 12px; font-weight: 600; margin-bottom: 6px; color: #2b4a6b; }
  .card p { font-size: 12px; line-height: 1.7; }
  .pill {
    display: inline-block; font-size: 11px; padding: 2px 9px; border-radius: 999px;
    background: #e3f1e8; color: #1e6b3a; margin-right: 5px;
  }
  .hint { font-size: 11px; color: #5b7a96; }
</style></head><body>
  <div class="titlebar">
    <b>问了吗 · 冒烟测试</b>
    <div class="btns"><span>—</span><span>▢</span><span>✕</span></div>
  </div>
  <div class="body">
    <div class="card">
      <h3>这个窗口在验证什么</h3>
      <p>无边框 / 吸附屏幕右边 / 上下顶满 / 始终置顶</p>
    </div>
    <div class="card">
      <h3>全局快捷键</h3>
      <p>在任何别的窗口里按 <b>Ctrl + Alt + Q</b>，<br>去看 结果.txt 有没有出现「★ 收到全局快捷键」。</p>
    </div>
    <div class="card">
      <h3>配色与圆角预演</h3>
      <p><span class="pill">浅色</span><span class="pill">冷色系</span><span class="pill">圆角</span><span class="pill">黑色字体</span></p>
    </div>
  </div>
</body></html>"""


def main():
    import webview

    # 先跑不需要界面的三项
    shot = test_capture()
    test_clipboard()
    test_ocr(shot)
    test_hotkey()

    log()
    log("=" * 62)
    log("  现在打开窗口，测第 1 项……")
    log("=" * 62)

    window = webview.create_window(
        "问了吗", html=HTML,
        x=WIN_X, y=WIN_Y, width=PANEL_W, height=WIN_H,
        frameless=True, on_top=True, easy_drag=False,
        resizable=False,
    )

    webview.start(check_window)

    log()
    log("=" * 62)
    log(f"  窗口已关闭。全局快捷键命中次数：{len(HOTKEY_HITS)}")
    log("=" * 62)


if __name__ == "__main__":
    main()
