# -*- coding: utf-8 -*-
"""快捷键手动通道联调 —— 函数级，不开窗口、不联网、不花钱。

跑起来：
    python "Desktop\\问了吗\\冒烟测试\\快捷键联调.py"

本文件盯的是四件事：
  1. 「显示写法 `Ctrl+E` ⇄ pynput 写法 `<ctrl>+e`」两个方向【各自】互逆
  2. 剪贴板那道闸门的判据（`safe_to_borrow`）—— 它决定这条通道能不能用
  3. 借剪贴板的三道闸：能不能借 / 先清空 / 还回去
  4. 「转换只有一份实现」这条规矩有没有被违反

────────────────────────────────────────────────────────
⚠️ **两件测不了的事，如实写在这里，别当成测过了**：

  ① **「在飞书里选中文字、按 Ctrl+E、热键真的响」整条** ——
     pynput 的 `GlobalHotKeys._on_press` 里有 `if not injected:`，
     它【刻意忽略注入的按键】。任何 `SendInput` 造出来的 Ctrl+E
     它都看不见，所以「注入一次再断言触发了」这种测试**永远是红的**。
     这一环只能人工验。
     （同一句话的另一面是好事：我们自己注入的 Ctrl+C 不会反过来触发自己。）

  ② **真·按键注入（`send_copy`）本身** —— 同上。
     下面所有取词用例都走 `grab_selection(send_copy_fn=...)` 那个口子，
     验的是**闸门逻辑**，不是 SendInput。这不叫 mock 掉被测对象，
     被测对象本来就是闸门。

────────────────────────────────────────────────────────
⚠️ 剪贴板那组会**真的动这台机器的剪贴板**（它是全局资源）。
   所以：
     · 跑之前先看当前剪贴板能不能安全借用（用产品自己那个判据）。
       不能（里面是图片/文件）就**整组不跑**，绝不动它 ——
       理由和产品拒绝取词是同一条：赔不起的东西不碰。
     · 能跑的话，全程 `try/finally` 把用户原来的内容还回去。
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

# ⚠ `▸` 这类字符 GBK 编不出来，输出被重定向时 print 当场抛 UnicodeEncodeError。
#   （`知识库联调.py` 第一版就栽在这儿，app/main.py 开头也有同一段。）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import hotkey as hk                                                # noqa: E402

# 「出口自检」那把共用的尺子（见 `尺子.py`）。
sys.path.insert(0, HERE)
import 尺子                                                       # noqa: E402

try:
    import win32clipboard as wc
    import win32con
    HAVE_WIN32 = True
except Exception:
    HAVE_WIN32 = False

PASS, FAIL, SKIP = [], [], []

# ★ 整组没跑时打这一行。`全部回归.py` 认它，把它记成 ⏸ 而不是 ❌ ——
#   「环境不让验」和「验了发现坏的」是两件事，修法完全不同。
#   （同 `框选联调.py` 那句「屏幕现在是【锁着的】」的用法。）
SKIP_MARK = "剪贴板里不是纯文字，取词那组没跑"


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [通过] {name}")
    else:
        FAIL.append((name, detail or "断言为假"))
        print(f"  [失败] {name}　→ {detail}")


def skip(name, why):
    """没测。⚠️ **不算通过** —— 汇总里单列，且脚本返回 1。

    这条是照 `全部回归.py` 对锁屏那套来的：
    「没验过」和「验过了没问题」绝不能长成同一个样子。
    """
    SKIP.append((name, why))
    print(f"  [没测] {name}　→ {why}")


# ⚠️ 这里原来有个 `guard()`，只探【一个方向】（假断言记不记失败）。
#    2026-09-21 换成 `尺子.gate()`，那个是**两个方向**都探的
#    （真断言记不记通过、假断言记不记失败），而且和另外三份脚本
#    共用同一份实现 —— 免得这里又长成「第五种写法」。
#    老守门员那条规矩照旧，别丢：**不能写在 try 里。** 这个项目栽过一次 ——
#    守门断言和被守的代码套在同一个 try 里，前者抛异常时后者跟着缺席，
#    而汇总照样打绿的。


# ══════════════════════════════════════════════════════════
# 1. 组合键：显示写法 ⇄ pynput 写法
# ══════════════════════════════════════════════════════════

def t_combo():
    print("\n▸ 组合键字符串互转")

    check("Ctrl+E → <ctrl>+e", hk.to_pynput("Ctrl+E") == "<ctrl>+e",
          repr(hk.to_pynput("Ctrl+E")))
    check("<ctrl>+e → Ctrl+E", hk.to_display("<ctrl>+e") == "Ctrl+E",
          repr(hk.to_display("<ctrl>+e")))

    # ★ 两个方向【各测一次】。「转过去再转回来」不等于恒等 ——
    #   而那种不恒等平时看不出来，只在用户改了键之后发作：
    #   界面显示一个键、实际注册的是另一个。
    for c in ("Ctrl+E", "Alt+Shift+F5", "Ctrl+0", "Win+Shift+A", "Ctrl+F12"):
        py = hk.to_pynput(c)
        check(f"互逆：{c}", py is not None and hk.to_display(py) == c,
              f"pynput={py!r} → 转回来={hk.to_display(py) if py else None!r}")

    # 用户是手打的，大小写和空格都得容忍
    check("手打的 'ctrl + e' 也认", hk.to_pynput("ctrl + e") == "<ctrl>+e",
          repr(hk.to_pynput("ctrl + e")))
    check("'CTRL+E' 也认", hk.to_pynput("CTRL+E") == "<ctrl>+e",
          repr(hk.to_pynput("CTRL+E")))

    for c in ("E", "Ctrl", "Ctrl+Shift", "Ctrl+Ctrl+E", "Ctrl+;", "Ctrl+F13",
              "", None, "Ctrl+Enter", "+"):
        check(f"非法组合被拒：{c!r}", hk.to_pynput(c) is None,
              repr(hk.to_pynput(c)))

    # ⚠ Ctrl+Ctrl+E 的【理由】必须说对。曾经它掉进了「这个键不支持」那条 ——
    #   用户照着那句话去改主键，怎么改都不对，因为问题出在修饰键上。
    ok, why, _ = hk.validate("Ctrl+Ctrl+E")
    check("Ctrl+Ctrl+E 的拒绝理由说的是【重复修饰键】，不是「键不支持」",
          (not ok) and ("重复" in why), why)

    ok, why, _ = hk.validate("Ctrl+Shift")
    check("全是修饰键时理由是「不能只有修饰键」", (not ok) and ("修饰键" in why), why)

    ok, why, disp = hk.validate("ctrl+e")
    check("validate 把 Ctrl+E 规范化成显示写法", ok and disp == "Ctrl+E", repr(disp))

    check("Ctrl+C 会被标成危险组合（和取词注入的键撞车）",
          hk.is_risky("Ctrl+C") is True)
    check("默认组合 Ctrl+E 不危险", hk.is_risky(hk.DEFAULT_COMBO) is False)
    check("默认组合就是用户拍板的 ctrl+e",
          hk.DEFAULT_COMBO == "ctrl+e", repr(hk.DEFAULT_COMBO))


# ══════════════════════════════════════════════════════════
# 2. 闸一：能不能借这个剪贴板
# ══════════════════════════════════════════════════════════

def t_gate():
    print("\n▸ 闸一：能不能借剪贴板")
    if not HAVE_WIN32:
        skip("闸一判据", "没有 win32clipboard")
        return

    T = win32con.CF_UNICODETEXT
    check("空剪贴板【放行】（没东西可弄丢；刚开机时剪贴板就是空的）",
          hk.safe_to_borrow([])[0] is True)
    check("只有文本 → 放行", hk.safe_to_borrow([T])[0] is True)
    check("整个文本族 → 放行",
          hk.safe_to_borrow([13, 16, 1, 7])[0] is True)

    # ★★★ 这一条是本次改动的核心，别删。
    #   飞书和 Obsidian 都是 Chromium/Electron 套壳的，复制纯文字时
    #   会额外写一份 HTML Format 和几个内部注册格式。
    #   如果谁把判据改回「全部格式都落在文本族里」，**这里立刻红** ——
    #   而在那之前，功能会在需求点名的两个 App 里一次都用不了。
    check("★ 飞书/Obsidian 复制纯文字（文本 + HTML + 注册格式）→ 必须放行",
          hk.safe_to_borrow([13, 16, 1, 7, 49336, 49637, 49638, 49819])[0] is True,
          "严格版判据会把这条毙掉 —— 那等于功能在这两个 App 里不存在")

    check("资源管理器复制文件（CF_HDROP）→ 拒绝",
          hk.safe_to_borrow([15])[0] is False)
    check("文本 + 文件 → 拒绝（文件赔不起）",
          hk.safe_to_borrow([13, 15])[0] is False)
    check("复制了一张图（CF_DIB）→ 拒绝", hk.safe_to_borrow([8])[0] is False)
    check("只有 HTML 和注册格式、没有文本 → 拒绝（借完什么都还不回去）",
          hk.safe_to_borrow([49336, 49637])[0] is False)

    # ⚠ 「读不到」和「空」必须分开。混着的话，别的程序占着剪贴板会被读成
    #   「空的」→ 放行 → 我们清空、复制、"还原"成一个空剪贴板：
    #   用户的剪贴板没了，而且全程不报错。
    check("读不到（None）→ 拒绝，不能当成空剪贴板",
          hk.safe_to_borrow(None)[0] is False,
          "读不到就没法备份，没法备份就不能借")

    ok, why = hk.safe_to_borrow([15])
    check("拒绝时会告诉用户下一步怎么办（不是只说「不行」）",
          ("框选" in why), why)


# ══════════════════════════════════════════════════════════
# 3. 真剪贴板：三道闸
# ══════════════════════════════════════════════════════════

def _fmts():
    return hk.clipboard_formats()


def _backup_clipboard():
    """备份用户当前的剪贴板。借不动（判据不放行）就返回哨兵，整组不跑。"""
    f = _fmts()
    ok, _ = hk.safe_to_borrow(f)
    if not ok:
        return None
    return {"was_empty": not f, "text": hk.read_text()}


def _restore_clipboard(b):
    if b.get("was_empty"):
        hk.clear_clipboard()
    else:
        hk.write_text(b.get("text") or "")


def _set_nontext_clipboard():
    """造一个「非文本」剪贴板：注册一个自定义格式放进去。

    返回 True 表示造出来了。**造不出来要说没测，别拿 mock 假装测过。**
    """
    try:
        fmt = wc.RegisterClipboardFormat("问了吗联调测试格式")
        if not fmt:
            return False
        wc.OpenClipboard()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardData(fmt, b"\x00\x01\x02\x03")
        finally:
            wc.CloseClipboard()
        return True
    except Exception as e:
        print(f"    （造非文本剪贴板失败：{e!r}）")
        return False


def t_clipboard():
    print("\n▸ 真剪贴板：借、清空、还回去")
    if not HAVE_WIN32:
        skip("剪贴板三道闸", "没有 win32clipboard")
        return
    if not hk.AVAILABLE:
        skip("剪贴板三道闸", f"热键不可用：{hk.IMPORT_ERR}")
        return

    b = _backup_clipboard()
    if b is None:
        # ★ 整组不跑。理由和产品拒绝取词是同一条：**赔不起的东西不碰。**
        #   把用户的图/文件清掉再"还原"成空剪贴板，比不测糟糕得多。
        print(f"    ⚠ {SKIP_MARK} —— 当前剪贴板里有图片/文件，不能动它。")
        print("      要看这组：随便复制一段文字，再跑一次这个脚本。")
        for n in ("备份/还原", "先清空（防陈旧剪贴板）", "非文本拒绝",
                  "取词失败也要还原"):
            skip(f"剪贴板：{n}", SKIP_MARK)
        return

    print(f"    （现在的剪贴板：{'空的' if b['was_empty'] else '有文字'}，"
          f"格式 {_fmts()}）")

    try:
        # ── 用例 1：备份 → 取词 → 还原 ────────────────────────
        hk.write_text("原来的内容")
        r = hk.grab_selection(send_copy_fn=lambda: hk.write_text("选中的字"))
        check("取词拿到了选中的字", r.get("ok") and r.get("text") == "选中的字",
              repr(r))
        check("★ 取完把用户原来的剪贴板还回去了",
              hk.read_text() == "原来的内容",
              f"现在是 {hk.read_text()!r}，应该是 '原来的内容'")

        # ── 用例 2：先清空 ───────────────────────────────────
        # ★ 这条防的是：用户【根本没选中东西】时，Ctrl+C 什么也不做，
        #   剪贴板里上一次的内容原封不动，我们却把它当成"他选中的话"读走。
        #   那时候不报错，用户只会奇怪「它怎么解释了这个？」。
        hk.write_text("上一次的旧内容")
        r = hk.grab_selection(send_copy_fn=lambda: None)   # 模拟：没选中
        check("★ 没选中东西时报的是「没抓到文字」",
              (not r.get("ok")) and r.get("reason") == hk.MSG_NO_SELECTION,
              repr(r.get("reason")))
        check("★ 没把上一次的剪贴板内容当成选中文字读走",
              r.get("text") != "上一次的旧内容", repr(r.get("text")))
        check("没选中这条路上，剪贴板也还原了",
              hk.read_text() == "上一次的旧内容", repr(hk.read_text()))

        # ── 用例 3：非文本剪贴板 → 拒绝，且**碰都不碰** ────────
        if _set_nontext_clipboard():
            before = _fmts()
            r = hk.grab_selection(send_copy_fn=lambda: hk.write_text("不该出现"))
            check("非文本剪贴板 → 拒绝执行", not r.get("ok"), repr(r))
            check("★ 拒绝时【没有动过】剪贴板（格式集合前后一致）",
                  _fmts() == before, f"{before} → {_fmts()}")
        else:
            skip("剪贴板：非文本拒绝", "这台机器上造不出非文本剪贴板（如实记，没测）")

        # ── 用例 4：取词中途失败，也要还回去 ──────────────────
        hk.write_text("还得还的")
        def _boom():
            raise RuntimeError("模拟注入失败")
        r = hk.grab_selection(send_copy_fn=_boom)
        check("注入失败时不假装成功", not r.get("ok"), repr(r))
        check("★ 注入失败也把剪贴板还回去了（还原在 finally 里）",
              hk.read_text() == "还得还的", repr(hk.read_text()))

    finally:
        _restore_clipboard(b)
        print(f"    （已还原用户剪贴板，格式 {_fmts()}）")


# ══════════════════════════════════════════════════════════
# 4. 唯一实现处：转换不许有第二份
# ══════════════════════════════════════════════════════════

def t_single_source():
    print("\n▸ 唯一实现处")

    hk_src = open(os.path.join(APP, "hotkey.py"), encoding="utf-8").read()
    check("转换函数住在 hotkey.py 里",
          "def to_pynput" in hk_src and "def to_display" in hk_src)

    js = open(os.path.join(APP, "web", "app.js"), encoding="utf-8").read()
    # 前端【组装】一个显示写法（从键盘事件拼 Ctrl+Shift+E）是可以的，
    # 但它不该知道 pynput 那套 `<ctrl>+` 写法 —— 知道了就是第二份转换。
    check("app.js 里没有 pynput 写法（<ctrl> 之类）",
          "<ctrl>" not in js and "<alt>" not in js and "<shift>" not in js)

    main_src = open(os.path.join(APP, "main.py"), encoding="utf-8").read()
    check("main.py 里也没有第二份转换",
          "<ctrl>" not in main_src and "def to_pynput" not in main_src)
    check("main.py 走的是 hotkey 模块的默认值，没自己写一遍 'ctrl+e'",
          "hotkey_mod.DEFAULT_COMBO" in main_src)

    d = open(os.path.join(APP, "prompt.py"), encoding="utf-8").read()
    check("两份提示词都在后端（前端不各写一份）",
          "DEFAULT_PROMPT_ANY" in d and "DEFAULT_PROMPT " in d)
    check("★ 英文那份默认提示词【原封不动】还在（自动通道的行为没被带动）",
          "只解释英文" in d or "英文术语" in d)


# ══════════════════════════════════════════════════════════

def main():
    print("═" * 62)
    print("问了吗 · 快捷键联调（v0.20）")
    print("═" * 62)
    if not hk.AVAILABLE:
        print(f"⚠ 热键不可用（{hk.IMPORT_ERR}）—— 下面的取词用例会跳过。")

    # ★ 先验尺子：`check()` 是这 50 项断言的唯一出口，它坏成
    #   「无条件记通过」的话 50 项一起变绿，而项数对账还照样通过。
    # ⚠️ 摆在任何 try 之外（老守门员那条规矩）。
    if not 尺子.gate(check, *尺子.list_snapshot(PASS, FAIL), label="快捷键联调"):
        return 2

    t_combo()
    t_gate()
    t_clipboard()
    t_single_source()

    print("\n" + "═" * 62)
    print(f"通过 {len(PASS)} 项、不通过 {len(FAIL)} 项、没测 {len(SKIP)} 项")
    if FAIL:
        print("\n失败明细：")
        for name, detail in FAIL:
            print(f"  · {name}　→ {detail}")
    if SKIP:
        print("\n没测明细（**这不是通过**）：")
        for name, why in SKIP:
            print(f"  · {name}　→ {why}")

    print("""
⚠ 本文件【两条测不了的】，别当成测过了：
  · 「在飞书里选中文字、按 Ctrl+E、热键真的响」——
    pynput 刻意忽略注入按键（`if not injected:`），SendInput 造出来的
    Ctrl+E 它看不见，这一环只能人工验。
  · 真·Ctrl+C 注入本身 —— 同上。取词用例走的是 grab_selection 的
    `send_copy_fn` 口子，验的是闸门逻辑。
""")
    # 没测也算没通过 —— 少验多少项就是少验多少项。
    return 1 if (FAIL or SKIP) else 0


if __name__ == "__main__":
    sys.exit(main())
