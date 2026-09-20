# -*- coding: utf-8 -*-
"""快捷键手动通道 —— 全局热键 → Ctrl+C 取词 → 置标记，等前端来取。

    v0.20 新增（决策 50）。用户需求原文：
    「新增自定义快捷键，默认 ctrl+E，使用后可以选取需要解释的内容，
      选择内容后可以解释用户输入的内容，不论中英文」

⚠ **这里绝不跑管线。**
  硬约束 4：自动通道和手动通道必须共用同一条 `runPipeline()`。
  这条线程要是自己调 recognize → explain，那就是第二份实现 ——
  两段式渲染、三桶、`bucketize()` 的顺序全都要跟着再抄一遍，迟早分叉，
  而且分叉了不报错，只是变贵或错位。
  所以它只干一件事：**把用户选中的文字弄到手，然后置一个标记**。
  前端每 500ms 来 poll 一次，拿到标记后去跑【它自己那条】管线。

⚠ **剪贴板是借来的，必须还。**
  取词要临时占用剪贴板（模拟 Ctrl+C）。用户剪贴板里可能躺着他刚复制的
  东西，用完不还，性质就是「这个软件偷我的剪贴板」（决策 18 / 硬约束 6）。
  见 `grab_selection()` 的三道闸。

⚠ **pynput 忽略注入的按键**（`GlobalHotKeys._on_press` 里那句 `if not injected:`）。
  两个后果，都重要：

    · 好事：我们自己注入的那发 Ctrl+C **不会**反过来触发热键，
      「取词 → 触发取词 → …」的死循环天然不存在，不用额外写防护。
    · 坏事：「按 Ctrl+E 真的能触发」这一环**没法自动化验证** ——
      pynput 根本看不见 SendInput 造出来的按键。
      **别写「注入 Ctrl+E 再断言触发了」这种测试，它永远是红的。**
      这一环只能人工验，`快捷键联调.py` 里已如实写明。
"""
import threading
import time

# ⚠ 这两个 import 放在 try 里：热键是【可选】能力，pynput 装不上时
#   整个程序还得能起来（框选通道照常用）。起不来比没热键严重得多。
try:
    import win32api
    import win32clipboard
    import win32con
    from pynput import keyboard as pk
    AVAILABLE = True
    IMPORT_ERR = ""
except Exception as e:                      # pragma: no cover - 取决于装机环境
    AVAILABLE = False
    IMPORT_ERR = repr(e)

# ── 参数 ────────────────────────────────────────────────────────────
DEFAULT_COMBO = "ctrl+e"        # 用户拍板的默认值

# 「用户按下热键」到「我们把 Ctrl+C 发出去」之间，等他松手最多等多久。
# 不松手也照发 —— 那时最坏是这次没抓到字（下面清空那道闸兜着），
# 不会抓错东西，所以超时不是安全问题，不值得卡死在这里。
MOD_WAIT_SEC = 1.0

# 发出 Ctrl+C 之后，等目标程序把内容放进剪贴板。
# 0.6 秒对任何本地编辑器都绰绰有余；等不到就是「他真的没选中东西」。
COPY_WAIT_SEC = 0.6
COPY_POLL_SEC = 0.02

# 剪贴板可能被别的程序短暂占着（OpenClipboard 会失败），重试几次。
CLIP_RETRY = 8
CLIP_RETRY_SEC = 0.03

# ── 剪贴板格式：能不能借 ────────────────────────────────────────────
#
# ★★ v0.20 落地时实测推翻了一次设计（这条别再改回去，理由在下面）。
#
# 原来的判据是「**全部**格式都落在文本族内」（`is_text_only`）。
# 它的立场是：我们只能还原文本，但凡还有 HTML / 图片 / 注册格式，
# 还原完就缺一块，宁可不做。听着很稳，**但它会让这个功能在
# 飞书和 Obsidian 里一次都用不了** —— 而那两个 App 正是需求原文点名的：
#
#     「在飞书/Obsidian 里选中一段字，按 Ctrl+E」
#
# 因为**飞书和 Obsidian 都是 Chromium/Electron 套壳的**，它们的
# `Clipboard::WriteText` 在写 `CF_UNICODETEXT` 的同时还会写一份
# `HTML Format` 和几个 Chromium 内部注册格式（Chromium internal source RFH
# token / Web Custom MIME Data Format / source URL）。
# 于是「用户从飞书里复制一段纯文字」在剪贴板里长这样：
#
#     13(CF_UNICODETEXT) + 16 + 1 + 7 + 49336("HTML Format") + 49637 + 49638 + 49819
#
# 严格判据在最后四个上一票否决 —— **每一次按都会走到拒绝分支**。
# 本机实测（2026-09-20）：探测开始时剪贴板里正是这 4 个注册格式，
# 一个文本格式都没有。（那次是复制网页内容留下的。）
#
# —— 判据改成下面两条，分界线是「**丢了赔不赔得起**」：
#
#   ① **必须有一份文本**。没有文本就什么都还原不了，直接拒绝。
#      代表场景：在资源管理器里复制了一堆文件（CF_HDROP）、或者复制了一张图。
#      这两种本来就跟「解释选中的文字」无关，拒绝是对的，而且提示要给出路。
#   ② **不能有【文件和图片】**。这些丢了不可恢复，而且和文字无关，
#      留着它们说明用户手上那件事根本不是"选一段字来解释"。
#
#   HTML / RTF / 注册格式则**容忍** —— 它们是【同一段文字的另一种说法】
#   （Chromium 就是这么写的），丢掉的是排版，不是内容。
#
# ⚠️ 这条到底放弃了什么，必须说清楚，别读成"什么都不会丢"：
#   用户剪贴板里那份「富文本版」在取词之后会变成纯文本版。
#   主路径上这不是损失（他刚选中的就是这段字，我们原样还了文字）。
#   次路径上有损失（剪贴板里躺着五分钟前复制的一段带格式的东西，
#   他跑去别处选字按了快捷键 —— 那份带格式的没了）。
#   这是**Ctrl+C 取词这条路天生的代价**，决策 18 已经接受了它；
#   换来的是一条能用的通道。要无损得做整套多格式快照，本次不做。
TEXT_FAMILY = frozenset({
    win32con.CF_TEXT if AVAILABLE else 1,
    win32con.CF_OEMTEXT if AVAILABLE else 7,
    win32con.CF_UNICODETEXT if AVAILABLE else 13,
    win32con.CF_LOCALE if AVAILABLE else 16,
})

# 丢了不可恢复、且与「选中的文字」无关的那几样：文件、位图、图元文件、声音。
# ⚠ CF_HDROP 在资源管理器复制文件时出现；CF_DIB / CF_DIBV5 是图片；
#   Word 之类的 OLE 程序复制带图内容时也走这几个。
LOSSY = frozenset({
    win32con.CF_HDROP if AVAILABLE else 15,
    win32con.CF_DIB if AVAILABLE else 8,
    win32con.CF_DIBV5 if AVAILABLE else 17,
    win32con.CF_BITMAP if AVAILABLE else 2,
    win32con.CF_PALETTE if AVAILABLE else 9,
    win32con.CF_METAFILEPICT if AVAILABLE else 3,
    win32con.CF_ENHMETAFILE if AVAILABLE else 14,
    win32con.CF_WAVE if AVAILABLE else 12,
})

# 拒绝时给用户看的话。★ 必须写清楚【下一步怎么办】——
#   实测这台机器上剪贴板常年挂着几个注册格式，也就是说这条提示
#   用户【一按就会碰到】，不是理论分支。
MSG_NO_TEXT = ("剪贴板里现在是图片或文件，不是文字。取词要临时借它一下，"
               "怕弄丢你的东西就不动手了 —— 先随便复制一段文字再按，"
               "或者改用面板上的「框选区域」。")
MSG_NO_SELECTION = "没抓到文字 —— 可能没选中内容，或者按快捷键时修饰键还没松开。"


# ══════════ 剪贴板 ══════════

def _clip_open():
    """打开剪贴板，失败重试。返回 True/False，绝不抛。"""
    for _ in range(CLIP_RETRY):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(CLIP_RETRY_SEC)
    return False


def clipboard_formats():
    """当前剪贴板里有哪些格式（整数列表）。空剪贴板返回 []；**读不到返回 None**。

    ★ 必须把「空」和「读不到」分开，别都返回 []（v0.20 落地时改的）。
      混在一起的话，别的程序正占着剪贴板（OpenClipboard 失败）会被读成
      「空的」→ 闸一放行 → 我们清空、复制、再"还原"成一个空剪贴板：
      **用户的剪贴板被清掉了，而且全程不报错**。
      这正是那道闸门本来要防的事，却从"读失败"这个侧门漏过去了。
    """
    if not AVAILABLE:
        return None
    if not _clip_open():
        return None
    try:
        out = []
        f = win32clipboard.EnumClipboardFormats(0)
        while f:
            out.append(f)
            f = win32clipboard.EnumClipboardFormats(f)
        return out
    except Exception:
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def safe_to_borrow(formats):
    """能不能借这个剪贴板。返回 (ok, reason)。

    判据两条，见上面 TEXT_FAMILY / LOSSY 那段的长说明：

      ① 空剪贴板（[]）→ **放行**。没东西可弄丢，借它一下没有代价。
         ⚠ 它和「有图片」是两回事，别合并成一条判断 —— 合并了就变成
           「空剪贴板也不让用」，而用户刚开机、还没复制过任何东西时
           剪贴板就是空的，那是完全正常的使用姿势。
      ② 有内容 → 必须【含一份文本】，且【不含文件和图片】。
      ③ 读不到（None）→ **拒绝**。读不到就没法备份，没法备份就不能借。
         见 clipboard_formats() 里「空」和「读不到」必须分开那段。

    返回的 reason 是给用户看的话（拒绝时）。
    """
    if formats is None:
        return False, ("剪贴板被别的程序占着，这会儿读不出来。"
                       "等一下再按，或者改用面板上的「框选区域」。")
    fmts = list(formats)
    if not fmts:
        return True, ""
    if any(f in LOSSY for f in fmts):
        return False, MSG_NO_TEXT
    if not any(f in TEXT_FAMILY for f in fmts):
        # 有内容、没有文件图片、又没有文本 —— 比如只剩 HTML 和注册格式
        # （Chromium 复制网页图片时就是这样）。还是不敢碰：那样我们
        # 借完就什么都还不了，等于把用户剪贴板清空了。
        return False, MSG_NO_TEXT
    return True, ""


def read_text():
    """读剪贴板里的文本。读不到返回 None（**空串 "" 是有效值，不是 None**）。

    ⚠ 先 Unicode 再 ANSI 回退，这不是可选的讲究：
      有的老程序只放 CF_TEXT（不放 CF_UNICODETEXT）。只读前者的话，
      那种剪贴板我们备份到的是 None，还原时就写成空串 ——
      **把人家原本的文字弄丢了**，而且闸一明明放行了（它是文本族）。
      回退这一层就是补这个洞。
    """
    if not AVAILABLE or not _clip_open():
        return None
    try:
        # ⚠ 两个格式必须在【同一个 try】里挨着试，不能拆成两个 try 块。
        #   拆开的话，第一条读成功就直接 return 了，第二个 try 的 finally
        #   根本不会执行 —— 剪贴板被我们一直占着，别的程序全复制不了。
        for fmt in (win32con.CF_UNICODETEXT, win32con.CF_TEXT):
            try:
                return win32clipboard.GetClipboardData(fmt)
            except Exception:
                continue
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def write_text(text):
    """写 CF_UNICODETEXT。成功返回 True。"""
    if not AVAILABLE or not _clip_open():
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text or "")
        return True
    except Exception:
        return False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def clear_clipboard():
    """清空剪贴板（连格式一起）。"""
    if not AVAILABLE or not _clip_open():
        return False
    try:
        win32clipboard.EmptyClipboard()
        return True
    except Exception:
        return False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


# ══════════ 注入 ══════════

def _mods_down():
    """Ctrl / Alt / Shift / Win 里还有没有按着的。"""
    for vk in (0x11, 0x12, 0x10, 0x5B, 0x5C):     # Ctrl Alt Shift LWin RWin
        try:
            if win32api.GetAsyncKeyState(vk) & 0x8000:
                return True
        except Exception:
            return False
    return False


def wait_mods_released(timeout=MOD_WAIT_SEC):
    """等用户把手松开。

    ★ 为什么必须等：他按 Ctrl+E 的那一瞬间，Ctrl 还压在底下。
      这时我们立刻注入 Ctrl+C，等于在他的 Ctrl 上又叠了一层 ——
      更麻烦的是我们注入的 Ctrl【抬起】会把他物理按着的 Ctrl 一起带起来，
      目标程序收到的按键序列就乱了。
      超时也照发：最坏这次抓不到字（清空那道闸兜着），不会抓错。

    返回等了多久（秒），给日志用。
    """
    if not AVAILABLE:
        return 0.0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if not _mods_down():
            return time.perf_counter() - t0
        time.sleep(0.01)
    return time.perf_counter() - t0


def send_copy():
    """注入一发 Ctrl+C。这是【默认】的取词动作，测试可以换成别的。"""
    kb = pk.Controller()
    with kb.pressed(pk.Key.ctrl):
        kb.press("c")
        kb.release("c")


# ══════════ 取词 ══════════

def grab_selection(send_copy_fn=None):
    """借剪贴板抓一次当前选中的文字。

    返回 {"ok": bool, "text": str, "reason": str, "waited": float}

    ★ `send_copy_fn` 是给测试留的口子：默认就是真注入 Ctrl+C。
      测试传一个「什么都不做」（模拟没选中）或「往剪贴板写点什么」
      （模拟选中了）的假动作，就能把下面几道闸逐条钉死 ——
      **被测的是闸门逻辑，不是 SendInput 本身**，所以这不算 mock 掉被测对象。
      真·按键注入那一环本来就测不了（见模块开头 pynput 那段）。

    三道闸，顺序不能换：

      闸一（能不能借）  见 safe_to_borrow()：必须有文本，且不含文件/图片。
                        不合格 → 直接拒绝，**碰都不碰**。
      闸二（先清空）    清掉再复制。★ 不清的话，「用户根本没选中东西」时
                        Ctrl+C 什么也不做，剪贴板里【上一次的内容】原封不动，
                        我们就会把那份陈年旧货当成他选中的话读走 ——
                        而且完全不报错，用户只会觉得「它怎么解释了这个？」。
      闸三（还回去）    无论成败，finally 里把备份还原。
    """
    if not AVAILABLE:
        return {"ok": False, "text": "", "reason": f"热键不可用（{IMPORT_ERR}）",
                "waited": 0.0}

    send_copy_fn = send_copy_fn or send_copy

    # ── 闸一 ─────────────────────────────────────────────────────
    fmts = clipboard_formats()
    ok, reason = safe_to_borrow(fmts)
    if not ok:
        return {"ok": False, "text": "", "reason": reason, "waited": 0.0}

    was_empty = not fmts
    backup = read_text()            # 空剪贴板 → None；有文本 → 字符串（可能是 ""）

    # ⚠ 闸一的补丁：格式说有文本，可就是读不出来。
    #   这时候 backup 是 None，而还原那一步对 None 的处理是
    #   `write_text(backup if backup is not None else "")` —— 也就是
    #   **把用户的文字写成一个空串**：他的剪贴板内容没了，而且闸一放行过。
    #   读不出备份就不借，这是「宁可不做也不做半截」的同一条。
    if not was_empty and backup is None:
        return {"ok": False, "text": "",
                "reason": "剪贴板里的文字读不出来，没法先备份，这次就不借了。",
                "waited": 0.0}

    try:
        # ── 闸二 ─────────────────────────────────────────────────
        # 两种情况下都要清（空剪贴板清了也无害），所以不分支。
        # 真正需要区分 was_empty 的是下面的【还原】——
        # 「原本什么都没有」要还原成「什么都没有」，不是还原成一个空字符串。
        clear_clipboard()

        waited = wait_mods_released()

        try:
            send_copy_fn()
        except Exception as e:
            return {"ok": False, "text": "", "reason": f"复制失败：{e}",
                    "waited": waited}

        # 等目标程序把内容放进来
        text = None
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < COPY_WAIT_SEC:
            text = read_text()
            if text:
                break
            time.sleep(COPY_POLL_SEC)

        if not text:
            return {"ok": False, "text": "", "reason": MSG_NO_SELECTION,
                    "waited": waited}

        return {"ok": True, "text": text, "reason": "", "waited": waited}
    finally:
        # ── 闸三 ─────────────────────────────────────────────────
        # ⚠ 还原放在 finally 里，且【不依赖上面任何一条 return 路径】。
        #   中间任何一步抛了，用户的剪贴板照样得回去。
        try:
            if was_empty:
                clear_clipboard()       # 原本就没有 → 还原成「没有」，不是还原成 ""
            else:
                write_text(backup if backup is not None else "")
        except Exception as e:
            print(f"[热键] ⚠ 剪贴板还原失败：{e!r}", flush=True)


# ══════════ 快捷键字符串 ⇄ pynput 格式 ══════════════════════════════
#
# ★ 这两个方向【只有这一份实现】。别在 main.py 或前端再写一遍 ——
#   两处各写一份的话，改了默认值只改一处，界面显示 Ctrl+E 而实际注册的是别的键，
#   而且完全不报错，用户只会觉得「这快捷键时灵时不灵」。

_MODS = ("ctrl", "alt", "shift", "cmd")          # pynput 那边的名字
_MOD_ALIAS = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "cmd": "cmd", "win": "cmd", "super": "cmd", "meta": "cmd",
}
_MOD_SHOW = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"}

# 允许当主键的：字母、数字、F1~F12。
# 不收别的，是为了「显示用 Ctrl+E ⇄ pynput 用 <ctrl>+e」能真的互逆 ——
# 符号键（+ - = 等）在显示格式里会和分隔符打架，收进来就得写转义，
# 而转义规则一旦不对称，就是上面说的那种静默失灵。
_LETTERS = set("abcdefghijklmnopqrstuvwxyz")
_DIGITS = set("0123456789")
_FKEYS = {f"f{i}" for i in range(1, 13)}


def to_pynput(combo):
    """显示格式 → pynput 格式。`Ctrl+E` → `<ctrl>+e`。非法返回 None。

    小写、空格都容忍（`ctrl + e` / `CTRL+E` 都认），因为用户是手打的。
    """
    parts = [p.strip().lower() for p in str(combo or "").split("+")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None                 # 必须带修饰键，见 validate()

    *mods, key = parts
    mods = [_MOD_ALIAS.get(m) for m in mods]
    if any(m is None for m in mods):
        return None
    if len(set(mods)) != len(mods):
        return None                 # Ctrl+Ctrl+E 这种

    if key in _FKEYS:
        k = f"<{key}>"
    elif len(key) == 1 and (key in _LETTERS or key in _DIGITS):
        k = key
    else:
        return None

    return "+".join(f"<{m}>" for m in mods) + "+" + k


def to_display(combo):
    """pynput 格式 → 显示格式。`<ctrl>+e` → `Ctrl+E`。非法返回 None。

    ⚠ 必须和 to_pynput() 严格互逆，`快捷键联调.py` 里两个方向各断言一次。
      只测一个方向是不够的 —— 「转过去再转回来」不等于恒等，
      而这种不恒等平时看不出来，只在用户改了键之后发作。
    """
    parts = [p.strip().lower() for p in str(combo or "").split("+")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None

    *mods, key = parts
    out_mods = []
    for m in mods:
        name = m[1:-1] if m.startswith("<") and m.endswith(">") else m
        if name not in _MODS:
            return None
        out_mods.append(_MOD_SHOW[name])

    if key.startswith("<") and key.endswith(">"):
        key = key[1:-1]
    if key in _FKEYS:
        out_key = key.upper()
    elif len(key) == 1 and (key in _LETTERS or key in _DIGITS):
        out_key = key.upper()
    else:
        return None

    return "+".join(out_mods) + "+" + out_key


def validate(combo):
    """校验用户录进来的组合。返回 (ok, reason, 规范化的显示写法)。

    ⚠ 三条拒绝理由都不是洁癖，各有具体后果：
      · 不给修饰键（就一个 `E`）—— 注册上去之后，用户在飞书里**打一个字母 e
        就会触发取词**。这不是「不太好用」，是把人家的输入法搅烂。
      · 全修饰键（`Ctrl+Shift`）—— pynput 收得了，但永远触发不了，纯哑巴。
      · 认不出的主键 —— 见上面 _LETTERS 的说明（转义不对称会静默失灵）。
    """
    parts = [p.strip().lower() for p in str(combo or "").split("+")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return False, "快捷键至少要有一个修饰键（Ctrl / Alt / Shift / Win）加一个主键", ""
    *mods, key = parts
    mods_n = [_MOD_ALIAS.get(m) for m in mods]
    if any(m is None for m in mods_n):
        return False, "修饰键只认 Ctrl / Alt / Shift / Win", ""
    if key in _MODS or key in _MOD_ALIAS:
        return False, "最后一下要按一个字母、数字或 F1~F12，不能只有修饰键", ""
    if len(set(mods_n)) != len(mods_n):
        # ⚠ 单列一条，别让它掉进下面那句「这个键不支持」——
        #   理由是错的，用户会照着改主键，怎么改都不对。
        return False, "同一个修饰键重复了（比如 Ctrl+Ctrl+E）", ""
    if to_pynput(combo) is None:
        return False, "这个键不支持。主键请用 A~Z、0~9 或 F1~F12", ""

    disp = to_display(to_pynput(combo))
    if disp is None:                # 理论上到不了，真到了说明两个函数不互逆
        return False, "这个键不支持", ""
    return True, "", disp


def is_risky(combo):
    """这个组合值不值得警告一句。目前只有一条：它撞了 Ctrl+C。

    ⚠ 不是「不许设」，是「要告诉他」：
      取词本身就要注入 Ctrl+C，用户自己把热键也设成 Ctrl+C 的话，
      每次他自己复制都会触发一次解释。死循环是没有的
      （pynput 忽略注入按键，见模块开头），但那个体验明显不是他要的。
    """
    return to_pynput(combo) == "<ctrl>+c"


# ══════════ 热键 ══════════

class Hotkey:
    """全局热键 + 取词槽。

    线程模型（和 auto.Watcher 一个套路）：
      · pynput 的 Listener 自己是一条线程，回调跑在里面。
      · 取词要花半秒上下，**不能占着回调线程** —— 那会让热键在那半秒里
        完全没反应，连按两下第二下就被吞了。所以取词甩给一条一次性线程，
        外面用 `_busy` 挡住重入。
      · poll() 只读标量，前端每 500ms 来一次。
    """

    def __init__(self, combo=DEFAULT_COMBO):
        self._lock = threading.Lock()
        self._listener = None
        self._combo = ""            # 显示写法，如 "Ctrl+E"
        self._pending = None        # 待前端取走的 {"seq", "text", "reason"}
        # ★ 最近一次【成功】取到的原文，一直留着直到被下一次覆盖。
        #   它和 _pending 是两件事，别合并：
        #     · _pending 是「有个通知还没送达」，前端 ack 之后就清了；
        #     · _last_text 是「这一次要解释的就是这段字」，
        #       recognize(mode="clip") 真的去读它。ack 清掉 pending 之后
        #       前端才去跑管线，那时还得有字可读。
        self._last_text = ""
        self._seq = 0
        self._busy = False
        self._suspended = False     # 录制改键时挂起，见 suspend()
        self._err = ""

        ok, reason, disp = validate(combo)
        self._combo = disp if ok else to_display(to_pynput(DEFAULT_COMBO)) or "Ctrl+E"
        if not ok:
            self._err = f"快捷键设置无效（{reason}），已退回默认 {self._combo}"
            print(f"[热键] ⚠ {self._err}", flush=True)
        if AVAILABLE:
            self._register()
        else:
            self._err = f"热键不可用：{IMPORT_ERR}"
            print(f"[热键] ⚠ {self._err}", flush=True)

    # ── 注册 ────────────────────────────────────────────────────────
    def _register(self):
        py = to_pynput(self._combo)
        if py is None:
            return False
        try:
            if self._listener is not None:
                self._listener.stop()
            self._listener = pk.GlobalHotKeys({py: self._on_hotkey})
            self._listener.daemon = True        # 别拦着 os._exit(0)
            self._listener.start()
            print(f"[热键] 已注册 {self._combo}（pynput: {py}）", flush=True)
            return True
        except Exception as e:
            self._err = f"注册失败：{e!r}"
            print(f"[热键] ⚠ {self._err}", flush=True)
            self._listener = None
            return False

    def _on_hotkey(self):
        """热键被按下 —— 跑在 pynput 的监听线程里。

        ⚠ 这里【只】起一条线程就返回。任何耗时动作（等松手 1 秒 + 等剪贴板
          0.6 秒）放这儿，都会让热键在那一秒半里完全聋掉。
        """
        with self._lock:
            if self._suspended or self._busy:
                return
            self._busy = True
        threading.Thread(target=self._worker, name="wl-hotkey", daemon=True).start()

    def _worker(self):
        try:
            r = grab_selection()
            self._seq += 1
            entry = {"seq": self._seq}
            if r.get("ok"):
                entry["text"] = r.get("text") or ""
            else:
                entry["reason"] = r.get("reason") or "取词失败"
            with self._lock:
                self._pending = entry
                if r.get("ok"):
                    self._last_text = entry["text"]
            if r.get("ok"):
                print(f"[热键] 取到 {len(entry.get('text') or '')} 字"
                      f"（等了 {r.get('waited', 0):.2f}s 松手）", flush=True)
            else:
                print(f"[热键] 取词失败：{entry.get('reason')}", flush=True)
        except Exception as e:
            # 诊断代码必须比被测代码更不容易出错（auto.py 同款告诫）
            print(f"[热键] ⚠ 取词异常：{e!r}", flush=True)
            with self._lock:
                self._seq += 1
                self._pending = {"seq": self._seq, "reason": f"取词出错：{e}"}
        finally:
            with self._lock:
                self._busy = False

    # ── 联调后门 ────────────────────────────────────────────────────
    def seed(self, text):
        """假装刚刚取到 `text`，走一遍和真取词**完全一样**的下游。

        ⚠ **这不是给用户的功能，是给联调脚本的接口**（同 `WENLEMA_BASE_URL`
          那一族：正常启动时它不存在，行为与没有它时一字不差）。

        为什么必须有它：「在飞书里选中字、按 Ctrl+E、热键真的响」这一整条
        **没法自动化验** —— pynput 的 `GlobalHotKeys._on_press` 里写着
        `if not injected:`，它【刻意忽略注入的按键】，任何 `SendInput`
        造出来的 Ctrl+E 它都看不见。

        但它挡住的只是**最前面那一跳**（键盘事件 → `_worker`）。
        从 `_pending` 往后全是可验的，而且是真正容易写错的那一段：
        poll → ack → 前端 runPipeline('clip') → recognize('clip')
        → 空候选不早退 → 中文条目经 extras 上屏。
        seed() 从 `_worker` 的出口接进去，把这一段整条留在了测试里。
        （所以它写的是和 `_worker` 同一组赋值，不是另一套捷径 ——
          绕开一半的「后门」验的是后门自己，不是产品。）
        """
        text = (text or "").strip()
        with self._lock:
            self._seq += 1
            self._pending = {"seq": self._seq, "text": text}
            if text:
                self._last_text = text
        print(f"[热键] （联调）注入选中 {len(text)} 字", flush=True)

    # ── 给前端 ──────────────────────────────────────────────────────
    def poll(self):
        """前端每 500ms 来一次。**只读，不消费** —— 取走得靠 ack(seq)。

        ⚠ **为什么不消费**（v0.20 定的，别改回去）：
          前端拿到选段后要调 runPipeline()，而那句第一行是
          `if (running) return` —— 自动通道正在跑的时候（explain 最长 60 秒）
          这一下会被挡掉。如果 poll 是 pop 语义，文字就被取走了、
          管线没跑、**面板上什么都不会说**：用户按了快捷键，然后什么都没发生。
          改成「读了还在、确认了才清」，前端就能在 busy 时先不 ack，
          把「已抓到选段，等上一轮跑完」如实显示出来，下一拍接着处理。

        ⚠ 还有一条是性能硬要求：**只读标量、绝不阻塞**。
          pywebview 每次 js_api 调用都新开一条【非守护线程】——
          这里要是卡住，就会攒出一堆挂着的非守护线程，
          正好把 os._exit(0) 要绕开的那个 bug 原样搬回来。
          —— 以后往这个方法里加任何阻塞动作，都等于把那个 bug 加回来。
        """
        with self._lock:
            p = self._pending
            return {
                "ok": True,
                "seq": (p or {}).get("seq", 0),
                "text": (p or {}).get("text", ""),
                "reason": (p or {}).get("reason", ""),
                "combo": self._combo,
                "busy": self._busy,
                "suspended": self._suspended,
                "available": AVAILABLE,
                "err": self._err,
            }

    def state(self):
        """只要组合和可用性，不要待处理的通知。给 get_settings 用。

        ★ 前端画界面时【一律用这里回读的值】，不要用自己刚才输入的那个 ——
          改键失败时 set_combo 会保留旧组合，前端拿乐观值画就会显示一个
          实际没生效的键（`markSettle` 用后端回读值，同一个道理）。
        """
        with self._lock:
            return {"combo": self._combo, "available": AVAILABLE,
                    "err": self._err, "suspended": self._suspended}

    def last_text(self):
        """最近一次成功取到的原文。`recognize(mode="clip")` 读它。

        ⚠ 它是【单槽】，两次取词之间会被覆盖。理论上「第一次还没跑完
          第二次就来了」会让第一次用上第二次的文字。实际上有 `_busy`
          （取词期间不重入）和前端那把 running 锁（同一时刻只跑一条管线）
          两重挡着，窗口很窄；而且真发生了也只是「解释了他刚选的另一段」，
          不是静默错数据。**不值得为它加一套队列**，但要记在这里。
        """
        with self._lock:
            return self._last_text

    def ack(self, seq):
        """前端说「第 seq 条我用上了，可以清掉」。

        ★ 必须比对 seq：不比对的话，一条【过期的】ack 会把用户
          刚刚按出来的那一条抹掉 —— 又是「按了没反应」，而且更难查。
        """
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_seq"}
        with self._lock:
            if self._pending and self._pending.get("seq") == seq:
                self._pending = None
                return {"ok": True, "cleared": True}
        return {"ok": True, "cleared": False}

    def suspend(self):
        """挂起热键 —— 录制改键时必须先调这个。

        ★ 不挂的话，用户在录制界面里按下的那一下会**真的触发一次取词**：
          半秒后弹出「没抓到文字」，同时屏幕上他正在录的那串键
          被目标程序原样收走。用户会觉得这功能坏了。
        """
        with self._lock:
            self._suspended = True
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        print("[热键] 已挂起（录制中）", flush=True)
        return {"ok": True}

    def resume(self):
        with self._lock:
            self._suspended = False
        if AVAILABLE:
            self._register()
        return {"ok": True}

    def set_combo(self, combo):
        """换一个组合并重新注册。校验不过就【原样保留旧的】。

        ★ **无论成败都必须解除挂起**（录制态退出 = 用户录完了，
          不管录进来的那个键合不合法）。
          早退那条路上漏掉它，后果很具体：用户在录制界面里按了个
          不合法的键（比如光按一个 `E`），校验失败、组合保持旧的没错，
          **但热键从此一直是挂起的** —— 面板上显示着 Ctrl+E，
          按下去一点反应都没有，重启才好。这是最坏的一种：
          界面说的和实际做的不是一回事，而且不报错。
        """
        was_suspended = self._suspended
        with self._lock:
            self._suspended = False

        ok, reason, disp = validate(combo)
        if not ok:
            print(f"[热键] ⚠ 拒绝 {combo!r}：{reason}", flush=True)
            if was_suspended:
                # 挂起时 listener 已经被 stop 掉了，驳回也要把它装回去，
                # 否则「解除挂起」只是个标记，热键照样是哑的。
                if AVAILABLE:
                    self._register()
            return {"ok": False, "reason": reason}

        self._combo = disp
        self._err = ""
        if AVAILABLE:
            if not self._register():
                return {"ok": False, "reason": self._err or "注册失败"}
        print(f"[热键] 快捷键改为 {disp}", flush=True)     # ★ click_until 的判据
        return {"ok": True, "combo": disp, "risky": is_risky(disp)}


def start(combo=DEFAULT_COMBO):
    """起一个 Hotkey。返回它（不是线程）—— 前端要 poll 它。"""
    return Hotkey(combo)
