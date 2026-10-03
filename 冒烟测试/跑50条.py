# -*- coding: utf-8 -*-
"""跑「测试用例50条.md」里那些**现在就能跑**的用例。

跑起来：
    python "Desktop\\问了吗\\冒烟测试\\跑50条.py"                 # 全跑
    python "Desktop\\问了吗\\冒烟测试\\跑50条.py" --logic-only     # 只跑不开窗那一半

────────────────────────────────────────────────────────
★ 这份脚本和已有的十份联调**不是替代关系**，别读重了。

十份联调是**贴着代码验**：`kb.import_md` 的每个分支、`_tk_run` 的每个返回、
点按钮重不重试……  这是**贴着用户的一天验**：从「双击图标」到「关掉程序」
这条真实路径上，用户看得见的东西对不对。

判据也换了：联调问「这个函数在这个输入下返回什么」，
这里问「用户真的看到那句话了吗」。
本项目栽过的坑里最贵的几个恰好都在这个缝里 ——
绿行撒谎、两处闸门有一处够不着、界面写着「只解释库内」却画库外卡片。

────────────────────────────────────────────────────────
★ 三条不许松的规矩（本项目自己的老账）：

  1. **每条报「通过 / 不通过 / 跑不了」，三种，不许合并。**
     把「跑不了」算进「通过」＝ 把没验的说成验过了。

  2. **判据的 detail 必须说清楚「看到了什么」。**
     红是红了、detail 是空的，看着像那么回事，跟真因毫无关系 ——
     那就只剩「重跑一遍碰运气」这一条路（2026-09-24 刚栽过）。

  3. **每一条守卫，都要能被弄红。**本文件末尾 `--selfcheck` 那节
     专门干这个：把被测的那句改坏，看它是不是真的红。
     跑绿不算数，配的变体不红，它就是装饰品。

────────────────────────────────────────────────────────
⚠️ 本文件**全程不碰**用户的真数据：
   · settings 走 `WENLEMA_SETTINGS` → `_50_settings.json`
   · 知识库走 `WENLEMA_KB`       → `_50_kb.db`
   而且 D 组自己核一遍这件事（真 `app\\settings.json` 的哈希前后一致）。
"""

import contextlib
import ctypes
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import win32api
import win32con
import win32gui
import win32process

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

# ⚠ `▸` / `★` 这类字符 GBK 编不出来，输出被重定向时 print 当场抛
#   UnicodeEncodeError（`尺子.py` 的文件头记着这件事）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ★ 必须在 import main 之前把 settings 指到临时文件。
TMP_SETTINGS = os.path.join(HERE, "_50_settings.json")
TMP_KB = os.path.join(HERE, "_50_kb.db")
REAL_SETTINGS = os.path.join(APP, "settings.json")
REAL_KB = os.path.join(APP, "kb.db")
os.environ["WENLEMA_SETTINGS"] = TMP_SETTINGS
os.environ["WENLEMA_KB"] = TMP_KB
# ★ 兜底：万一哪条断言忘了把 BASE_URL 指到环回口，**绝不许**它悄悄打去
#   api.deepseek.com（那会花用户的钱）。所以这里先把 BASE_URL 按到
#   一个环回口上；C 组两条各自在 try/finally 里再指向自己的那一个。
os.environ.setdefault("WENLEMA_BASE_URL", "http://127.0.0.1:1")

import 尺子                                                        # noqa: E402
import main                                                        # noqa: E402
import ai                                                          # noqa: E402
# ★ 2026-09-24 加：C19 那条「盘上存的是什么写法」必须问**产品自己**，
#   不能由测试这边拍一个字符串去比 —— 见 `t_c19` 里那段注释。
import hotkey as hk                                                # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail="", hint=""):
    if cond:
        PASS.append(name)
        print(f"  [通过] {name}" + (f"　{detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  [不通过] {name}" + (f"　{detail}" if detail else ""))
        if hint:
            print(f"          └─ {hint}")
    return bool(cond)


def skip(name, why):
    """记一条【没验】—— 第三个判决。

    ★ 为什么加它（2026-09-24）：
      这个文件原来只有「通过 / 不通过」两个抽屉。于是一条
      **前置条件没成立、压根没测到**的断言，只有两个下场，都坏：

        · 塞进「通过」→ 它和真验过了一模一样（本项目最忌讳的那件事）
        · 干脆不记   → 报告里少几条，而**少掉的那几条和通过长得也一样**

      C03 就是第一种：窗口压根没折叠，后面「折叠状态下『展开』按钮
      还点得到」却报了通过 —— 因为「展开」在**已经全高**的窗口上
      当然什么都不用做。**它绿的是「无事发生」，不是「功能好使」。**
      C04 是第二种：确认框没找到时，里面那 4 条一句都不吭地消失了，
      而汇总只报「通过 N 项」—— N 少了几，没人知道。

    ⚠ 所以「没验」和「通过」必须分开数、分开打、而且**一样要算进退出码**：
      一份有没验项的报告，不许以 0 收场。
    """
    SKIP.append(name)
    print(f"  [没验] {name}　{why}")


def depended(cond, names, why):
    """前置条件不成立时，把**依赖它的那几条**登记成「没验」。

    ★ 为什么拎成函数，而不是就地写几个 `if`：
      这是本项目里**反复出现**的一种形状 —— C03 有、C04 有，
      以后每加一条分层用例还会再遇到。就地写的话，各处措辞会慢慢分叉，
      而分叉的样子正是「有的地方记了没验、有的地方静默丢了」——
      静默丢掉是**看不见**的那一种，最难查。

    ⚠ 调用方一律写成 `if not depended(...): ...正常往下跑...`
      （和 `if not check(...): return` 同一个读法：**条件为真 = 出事**）。
      写成 `if depended(...): continue` 也行，但两种读法混用迟早看反 ——
      而看反的样子是**把没验的当验过了**。

    → 条件成立（= 前置条件不成立，已经登记了没验）返回 True。
    """
    if not cond:
        for n in names:
            skip(n, why)
        return True
    return False


def section(t):
    print("\n" + "─" * 62)
    print(t)
    print("─" * 62)


def sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


# ══════════════════════════════════════════════════════════
# 假服务器 —— 只为了把「失败形状」造出来，一个真请求都不发
# ══════════════════════════════════════════════════════════

SCEN = {"code": 200, "raw": "{}", "sleep": 0.0}
RAW = {"body": b"", "auth": None, "path": None}


class TinyFake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, raw):
        b = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except (ConnectionResetError, BrokenPipeError):
            # ★ 超时那一条**必然**走到这里：客户端等不住先挂了。
            #   不吞的话，它会在 stderr 刷一屏 traceback，
            #   而那一屏跟被测的东西毫无关系（AI接入联调 里也吞了）。
            pass

    def do_GET(self):
        RAW["path"] = self.path
        RAW["auth"] = self.headers.get("Authorization")
        if SCEN["sleep"]:
            time.sleep(SCEN["sleep"])
        self._send(SCEN["code"], SCEN["raw"])

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        RAW["body"] = self.rfile.read(n)
        RAW["auth"] = self.headers.get("Authorization")
        RAW["path"] = self.path
        if SCEN["sleep"]:
            time.sleep(SCEN["sleep"])
        self._send(SCEN["code"], SCEN["raw"])


def start_fake():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), TinyFake)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def dead_port():
    """拿一个**确定没人监听**的端口。

    ⚠ 不能写死 127.0.0.1:1 —— 有些环境里 1 号端口是特权端口，
      拒绝的方式是「超时」不是「拒绝连接」，那一条会慢到把整份脚本拖垮。
      这里现开一个 socket 拿到系统临时端口，**立刻关掉**再用 ——
      拿到手的那个号刚刚还活着、现在没人听，拒绝连接会立刻返回。
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ══════════════════════════════════════════════════════════
# B 组 · 模型接入：C09
# ══════════════════════════════════════════════════════════
#
# 用例原文：
#   9 | 拔网线（或断 WiFi）→ 点「接入」| 报**网络**错误，**不许**报成
#     「认证失败」（OpenClaw 那套把 400 误标成 auth 的坑，本项目自己别有）| 🟢
#
# ★ 「点接入」那一跳在界面上，但**界面显示的正是 `r.msg` 本身**
#   （`app.js` 的 `$('key-msg').textContent = r.msg;`，一个调用点）。
#   所以这里量 `msg` 就是量用户看到的那句话，不是绕过界面另说一套。
#
# ★★ **两处调用点 = 两个守卫目标**（本项目的老规矩）：
#     · `check_key()`  —— 「接入」按钮走的就是它
#     · `_post_chat()` —— 框选/自动/取词真的去问的时候走它
#   只验前者的话，后者哪天被改成「认证失败」照样全绿。

KEY_FOR_TEST = "sk-CANARY-9f3a7c1e5b2d4086"     # 一眼能搜到的假 Key

# 判据单独拎出来 —— 为的是 `--selfcheck` 能拿一个**坏输入**喂给它，
# 当场证明「这条判据真的会红」。判据写在 `check(...)` 里面的话，
# 想弄红它就只能去改产品源码，那是两码事。
_NOT_AUTH = ("认证", "Key 不对", "失效", "余额")


def _offline_msg_ok(msg):
    """断网时这句话，能不能算「报的是网络」。"""
    return "网络" in (msg or "") and not any(w in (msg or "") for w in _NOT_AUTH)


def t_c09():
    section("C09 · 断网时必须报「网络」，不许报成「认证失败」")

    p = dead_port()
    old = ai.BASE_URL
    ai.BASE_URL = f"http://127.0.0.1:{p}"
    try:
        # ── 调用点一：「接入」按钮 ───────────────────────────
        r = ai.check_key(KEY_FOR_TEST)
        msg = r.get("msg") or ""
        check("★ 接入按钮这条路：连不上时报的是「网络」",
              r.get("ok") is False and _offline_msg_ok(msg),
              f"msg={msg!r}",
              hint="断网被报成别的，用户下一步就会去重填 Key —— 白折腾")

        # ── 调用点二：真的去问的时候 ─────────────────────────
        r2 = ai.explain(KEY_FOR_TEST, "sys", {"terms": ["RAG"]})
        msg2 = r2.get("msg") or ""
        check("★ 走 ② 那条路（框选/自动/取词）连不上时，报的也是「网络」",
              r2.get("ok") is False and _offline_msg_ok(msg2),
              f"msg={msg2!r}")
    finally:
        ai.BASE_URL = old


# ══════════════════════════════════════════════════════════
# K 组 · 安全与隐私：C49
# ══════════════════════════════════════════════════════════
#
# 用例原文：
#   49 | 断网状态下跑一次（真 Key 已填）| 任何报错信息、日志、界面文案里
#      **都不出现 Key 明文** | 🟢
#
# ★ 把它做成「各种失败形状都走一遍」而不是只走断网那一种：
#   泄漏通常不是出在「连不上」那条路上，而是出在**报错要带原文**那几条
#   （`ai.py` 的 `detail=resp.text[:300]`）。只试断网的话，
#   恰好绕开了最该看的那几条。
#
# ⚠️ 测不了的一条，如实写在这里：
#   `detail` 在 500 / 未知状态码时是**服务端返回的原文前 300 字**。
#   要是那个服务端自己把 Authorization 回显进 body，我们就会照原样显示它。
#   **那一半不归本项目管**（换谁来都得显示服务端的报错），
#   这里验的是「**我们自己**不往里面塞 Key」。


def _leaks(msg, detail, key=KEY_FOR_TEST):
    """这段报错里有没有 Key 明文。判据拎出来是为了 `--selfcheck` 喂得进坏输入。"""
    return key in f"{msg or ''}\n{detail or ''}"


def t_c49():
    section("C49 · 失败路径不许漏 Key 明文")

    srv, port = start_fake()
    old = ai.BASE_URL
    ai.BASE_URL = f"http://127.0.0.1:{port}"
    shapes = [
        (401, '{"error":{"message":"Authentication Fails"}}', "401 Key 不对"),
        (402, '{"error":{"message":"Insufficient Balance"}}', "402 余额不足"),
        (429, '{"error":{"message":"Rate limit"}}', "429 限流"),
        (500, '{"error":{"message":"server exploded"}}', "500 服务端错"),
        (418, 'not json at all', "未知状态码 + 非 JSON"),
        (200, '{"choices":[{"message":{"content":"{半个 JSON"}}]}', "回复结构不对"),
    ]
    try:
        leaked_in_msg, leaked_in_detail, leaked_in_wire = [], [], []
        for code, raw, label in shapes:
            SCEN.update(code=code, raw=raw, sleep=0.0)
            RAW.update(body=b"", auth=None)
            r = ai.explain(KEY_FOR_TEST, "sys", {"terms": ["RAG"]})
            if _leaks(r.get("msg"), r.get("detail")):
                leaked_in_msg.append(label)
            if KEY_FOR_TEST.encode() in (RAW["body"] or b""):
                leaked_in_wire.append(label)

        check("★★ 六种失败形状，报错信息里一次都没出现过 Key 明文",
              not leaked_in_msg,
              f"试了 {len(shapes)} 种：401/402/429/500/未知/坏 JSON",
              hint=f"漏了：{leaked_in_msg}")

        # ★ 这条是「Key 不许进请求体」那条硬约束的下游形状。
        #   `ai.py` 的文件头写着它必须是**单独一个参数**，不许塞进 body
        #   再过滤掉 —— 那种写法一旦过滤条件写漏，Key 就跟着 body 走了。
        check("★★ Key 从来没进过请求体（只在 Authorization 头上）",
              not leaked_in_wire,
              f"逐条看了 {len(shapes)} 次请求的 body 原文",
              hint=f"漏了：{leaked_in_wire}")

        # ── 反向对照：证明上面两条**够得着** ──────────────────
        # ⚠ 没有这一段，上面两条在一个「`explain` 压根没发请求」的坏世界里
        #   **恒绿** —— 本项目的老账（全绿的断言必须能被弄红）。
        SCEN.update(code=200, raw=json.dumps({
            "choices": [{"message": {"content": '{"entries":[]}'}}]},
            ensure_ascii=False), sleep=0.0)
        RAW.update(body=b"", auth=None)
        ai.explain(KEY_FOR_TEST, "sys", {"terms": ["RAG"]})
        check("★★ 反向对照：假服务器**真的收到了**请求，且 Key 真的在头上",
              RAW["auth"] == "Bearer " + KEY_FOR_TEST and bool(RAW["body"]),
              f"auth={RAW['auth']!r}　body {len(RAW['body'])} 字节",
              hint="这条要是红，上面两条「没漏 Key」就是在一个没发请求的"
                   "世界里恒绿的 —— 那不算验过")
    finally:
        ai.BASE_URL = old
        srv.shutdown()

    # ── 断网那一种（用例原文写的就是它）────────────────────
    p = dead_port()
    ai.BASE_URL = f"http://127.0.0.1:{p}"
    try:
        r = ai.check_key(KEY_FOR_TEST)
        check("★ 断网这一种：msg 和 detail 里都没有 Key 明文",
              not _leaks(r.get("msg"), r.get("detail")),
              f"detail={r.get('detail')!r}")
    finally:
        ai.BASE_URL = old


# ══════════════════════════════════════════════════════════
# A 组 · 启动与窗口：C05
# ══════════════════════════════════════════════════════════
#
# 用例原文：
#   5 | 关掉程序 → 重新启动 | 知识库条数、当前策略、区域设置、快捷键、
#     提示词**全都还是关之前那样** | 🟢
#
# ★ 「重新启动」必须**真起一个子进程**去读，不能在同一个进程里调两次
#   `load_settings()` —— 那样验的是「函数幂等」，不是「关掉再打开」。
#   本项目在这上面栽过：进程内存里那份和盘上那份**不是一回事**，
#   而「关掉再打开还在」这句话说的是盘上那份。

C05_VALUES = {
    "kb_policy": "whitelist",
    "kb_dir": r"D:\我的导出目录",
    "system_prompt": "自定义的英文那份",
    "system_prompt_any": "自定义的中英不限那份",
    "system_prompt_detail": "自定义的详情那份",
    "hotkey": "ctrl+shift+k",
    "seen_notice": True,
    "region": [140, 260, 900, 480],
    "auto_on": True,
    "auto_settle_sec": 60,
}


def _missing(got):
    return [k for k, v in C05_VALUES.items() if got.get(k) != v]


def _all_kept(got):
    return not _missing(got)


def _missing_text(got):
    return "、".join(f"{k}={got.get(k)!r}（该是 {C05_VALUES[k]!r}）"
                     for k in _missing(got))


def t_c05():
    section("C05 · 关掉再打开，设置全都还在")

    payload = dict(main.DEFAULT_SETTINGS)
    payload.update(C05_VALUES)
    payload["api_key"] = KEY_FOR_TEST
    main.save_settings(payload)

    # ★ 真起一个干净的子进程去读。环境里只给它 WENLEMA_SETTINGS。
    env = dict(os.environ, WENLEMA_SETTINGS=TMP_SETTINGS)
    code = (
        "import json,sys;sys.path.insert(0,r'%s');import main;"
        "s,first=main.load_settings();"
        "print(json.dumps({'s':s,'first':first},ensure_ascii=False))" % APP
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env=env)
    if not check("★ 干净的子进程真的把设置读出来了",
                 out.returncode == 0 and out.stdout.strip(),
                 f"退出码 {out.returncode}　stdout={out.stdout.strip()[:80]!r}　"
                 f"stderr={out.stderr.strip()[-200:]!r}"):
        return
    got = json.loads(out.stdout.strip())["s"]

    check("★★ 十个自定义项**一个不少**地活过了重启",
          _all_kept(got),
          _missing_text(got) or f"{len(C05_VALUES)} 项全对",
          hint="少一个就是「关之前那样」这句话没做到")

    check("★ 那把 Key 也活着（而且是解出来的明文，不是盘上那串密文）",
          got.get("api_key") == KEY_FOR_TEST,
          f"读回来 {len(got.get('api_key') or '')} 个字符")

    on_disk = open(TMP_SETTINGS, encoding="utf-8").read()
    check("★★ 盘上那份里**没有** Key 明文（重启读得回来 ≠ 盘上是明文）",
          KEY_FOR_TEST not in on_disk,
          f"盘上文件 {len(on_disk)} 字节")


# ══════════════════════════════════════════════════════════
# A 组 · 启动与窗口（开窗那一半）：C02 / C03 / C04
# ══════════════════════════════════════════════════════════
#
# ★ 借 `框选联调.py` 的手，不自己再写一份。
#   它那套窗口/OCR/点击工具是**已经被验过的**（它自己有一节 `_selftest`），
#   这里再抄一份的话，抄出来的那份没人验，而它坏了的样子是
#   「产品按钮不见了」—— 最难查的一种红。

APPLOG = os.path.join(HERE, "_50_app.log")


def _load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


BOX = _load("boxlink", os.path.join(HERE, "框选联调.py"))

# ── 标题栏那四个窗口按钮的位置 ────────────────────────────
# ⚠ 它们是**纯图标**，OCR 认不出来（`index.html` 里白纸黑字写着这件事：
#   「标题栏那四个窗口按钮就没法用 OCR 找，只能靠位置」）。
#   所以只能按 CSS 算 —— 下面这几个数直接抄自 `style.css`，
#   改了样式就得回来改它们，否则点空。
BAR_H = 42          # .titlebar height
ICO = 26            # .ico width / height
IGAP = 4            # .tb-actions gap
PAD_R = 8           # .titlebar padding-right
WINBTN = {"move": 1, "min": 2, "max": 3, "close": 4}    # 从右往左数


def _scale(hwnd):
    """该窗口所在屏的缩放（CSS 像素 → 物理像素）。

    ⚠ 不能写死 1.5。窗口 rect 是【物理】像素、CSS 是【逻辑】像素，
      这台机器是 150%，换个 100% 的屏上写死的 1.5 会把四个按钮全点空，
      而点空的样子是「按了没反应」—— 看着像产品坏了。
    """
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
    except Exception:
        dpi = 96
    return (dpi or 96) / 96.0


def winbtn(rect, hwnd, which):
    """标题栏按钮中心的屏幕物理坐标。"""
    s = _scale(hwnd)
    n = WINBTN[which]
    x = rect[2] - (PAD_R + ICO / 2 + (4 - n) * (ICO + IGAP)) * s
    y = rect[1] + BAR_H / 2 * s
    return (int(x), int(y))


def monitor_of(hwnd):
    info = win32api.GetMonitorInfo(
        win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
    return info["Monitor"]


def look(rect, needle, tag, tries=1, wait=0.5):
    """在 rect 这块屏幕区域里找一段文字，返回它的屏幕中心；找不到返回 None。"""
    path = os.path.join(HERE, f"_50_{tag}.png")
    for i in range(tries):
        BOX.grab((rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]), path)
        pos = BOX.ocr_find(path, needle, (rect[0], rect[1]))
        if pos:
            return pos
        if i < tries - 1:
            time.sleep(wait)
    return None


def seed_50_settings(**over):
    data = dict(main.DEFAULT_SETTINGS)
    note = BOX.NOTE_RECT
    data.update(region=[note[0], note[1], note[2], note[3]],
                seen_notice=True, api_key="sk-test-fake", kb_dir="")
    data.update(over)
    with open(TMP_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def launch_app(extra=None):
    env = dict(os.environ,
               WENLEMA_SETTINGS=TMP_SETTINGS,
               WENLEMA_KB=TMP_KB,
               WENLEMA_BASE_URL=f"http://127.0.0.1:{dead_port()}")
    env.update(extra or {})
    open(APPLOG, "wb").close()
    log = open(APPLOG, "wb")
    return subprocess.Popen([sys.executable, "main.py"], cwd=APP, env=env,
                            stdout=log, stderr=subprocess.STDOUT)


def kill(app):
    subprocess.run(["taskkill", "/PID", str(app.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── 「这一条跑到头了没有」 ────────────────────────────────
# ★ 开窗脚本里每一个 `if not check(...): return` 都是【静默交卷】——
#   它跳过的项数，报告里一个字都不提。本项目为此栽过：v0.20 第一次回归里
#   ⑧ 那句 return 吞掉 45 项，脚本照样报「通过 24 项，不通过 4 项」。
#   所以四条各留一面旗子，`case_end()` 写在**最后一条断言之后**。
#   ⚠ 加新断言时把代码写在这句 `case_end()` **前面** —— 写在后面等于白写。
_GUI_CASES = ("C02", "C03", "C04", "C19")
_started, _done = set(), set()


def case_begin(tag):
    _started.add(tag)


def case_end(tag):
    _done.add(tag)


def truncation(started, done):
    """哪几条【开了头没收尾】。

    ★ 拎成函数是为了 `--selfcheck` 能喂坏输入 ——
      它和 `main_()` 里报数用的**是同一个表达式**（同一份实现，
      不是抄一遍）。抄一份的话，改了一处、另一处照样绿。
    """
    return [t for t in _GUI_CASES if t in started and t not in done]


def bring_up(tag):
    """起一个干净实例并等它摆好。返回 (app, panel, rect) 或 None。

    ★ **每一相一份日志**（`_50_app_<tag>.log`），不共用一个文件名。
      第一版是共用的、每次起 app 就清空 —— 于是 C03 红了的当下 C04 一启动，
      C03 那份日志就没了。等跑完回头查，只剩最后一条的日志，
      **前面几条红成什么样永远查不到**。
      本来只想省一个文件名，代价是「出问题的那一条恰恰是唯一查不了的」。
    """
    global APPLOG
    APPLOG = os.path.join(HERE, f"_50_app_{tag}.log")
    app = launch_app()
    panel = BOX.find_window(BOX.PANEL_TITLE, app.pid)
    if not panel:
        check("★ 窗口起来了（否则下面每一条都无从谈起）", False,
              f"pid={app.pid}　退出码="
              f"{'还活着' if app.poll() is None else app.poll()}")
        kill(app)
        return None
    BOX.wait_laid_out_or_warn(app.pid, APPLOG)
    return app, panel, win32gui.GetWindowRect(panel)


def _activate_until_foreground(hwnd, tries=4):
    """把窗口弄成**真·前台**，返回成没成。

    ★★ 为什么非要有这一步（2026-09-24）：这一批开窗用例原来是
       「`BOX.activate(panel)` → `BOX.click(…)`」一下就完事，
       而这正是 `CLAUDE.md` 第 4 条**明令禁止**的写法：
       > 点按钮一律走 `click_until`，不许「`activate` 一次 + `BOX.click` 一下」

       原因 `框选联调.activate()` 的注释里写得很清楚：
       **窗口不在前台时，第一次点击会被 Windows 吃掉** —— 它只用来激活窗口，
       不往下传给 WebView2，于是按钮像没反应一样。
       而 `SetForegroundWindow` 有一条**前台锁**：调用进程自己不是前台进程时
       直接返回 False（pywin32 里**连异常都不抛**），窗口原地不动，
       调用方却以为已经激活了。本脚本的调用方是终端里的 python，多半不是前台。

    ★ 那为什么不是直接用 `click_until`？因为**判据要选对**：
      `click_until` 的 ok 是**业务副作用**（覆盖层出来了、设置落盘了），
      它会把这三种情况混成同一句话「点到第 N 次才生效」：
        · 前 N-1 次点击**被窗口激活吃掉了**
        · 点击每次都送到了，只是**产品真的没反应**（这种情况它返回 0）
        · **这个按钮是有状态的**（`btn-move` 是 `menu.hidden = !menu.hidden`，
          点两次等于没点），重试会自己把自己取消掉
      前两种的修法完全相反（一个是测试环境、一个是产品代码）。
      而这里的判据 `GetForegroundWindow() == hwnd` **又便宜（不用 OCR）
      又正是「点击会不会被吃掉」的原因本身**。

    ⚠ 它**不当断言用**：没弄成前台不是「产品坏了」，是「这一下根本没点到」。
      调用方拿到 False 之后要把它登记成**没验**（`depended`），
      不许让它顶着功能断言的名字红 —— 那正是本项目最不想要的那种红。
    """
    for _ in range(tries):
        BOX.activate(hwnd)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    return False


def _fg_detail(hwnd):
    """失败时打的那半句 —— 说清楚「现在前台是谁」，别让人去猜。"""
    try:
        cur = win32gui.GetForegroundWindow()
        who = win32gui.GetWindowText(cur)
        return f"前台是 {who!r}（hwnd={cur}），不是面板（hwnd={hwnd}）"
    except Exception as e:            # noqa: BLE001
        return f"（读前台窗口失败：{e}）"


# ★ C02 后面四条断言的名字只在这里写一次 —— 理由同下面 C03 那段
#   （`check` 和 `skip` 是两个调用点，名字抄两份迟早对不上）。
C02_MENU = "★ 点了「移动/换边」之后，停靠菜单真的弹出来了"
C02_LEFT = "★★ 窗口真的吸到屏幕左边去了（left == 屏左边界）"
C02_W = "★ 靠左之后宽度还是 520（换边不该改宽度）"
C02_H = "★ 靠左之后仍然铺满整屏高度（重排了，不是缩成一小块）"


def t_c02():
    section("C02 · 点「移动/换边」→ 停靠左侧，窗口自己吸附过去")
    case_begin("C02")

    got = bring_up("C02")
    if not got:
        return
    app, panel, rect0 = got
    try:
        mon = monitor_of(panel)
        check("★ 先确认它一开始是贴在【右边】的（不然下面量的是什么就说不清）",
              rect0[2] == mon[2], f"rect={rect0}　屏右边界={mon[2]}")

        # ⚠ 这块条带按 `.popover` 的 CSS 算出来，不是拍一个「200 够了吧」：
        #   `.popover { top: 46px; padding: 6px }`，里面 4 个按钮各约 30px 高、
        #   间隔 2px。**切短了**的话「停靠左侧」还在、最下面那条「停靠底部」被切掉 ——
        #   而症状是「菜单里没这一项」，看着像产品少做了功能。
        #   这里宁可多给几像素：多出来的部分只是空白，不会误命中别的东西。
        dock_h = int((46 + 6 + 4 * 32 + 4 * 2 + 6 + 4) * _scale(panel))
        strip = (rect0[0], rect0[1], rect0[2], rect0[1] + dock_h)

        # ★ 走 `click_until`（项目老规矩，`CLAUDE.md` 第 4 条）。
        #   返回值 `n` = 点到第几次才生效，是**诊断**：见 C03 那段的长注释。
        n = BOX.click_until(
            panel, winbtn(rect0, panel, "move"),
            lambda: bool(look(strip, "停靠左侧", "dock", tries=1)),
            "移动/换边", tries=3, wait=1.5)
        # ⚠️ 这里**不能**拿 `n` 直接当结论：
        #   「移动/换边」是个**开关**（`app.js:91` `menu.hidden = !menu.hidden`）。
        #   万一某一轮 `ok()` 的 OCR 恰好没认出来，下一轮那一下就会把它**关掉** ——
        #   `click_until` 于是报「点到第 2 次才生效」，而事实相反。
        #   所以结论一律落在**最后那次 `look`**上（菜单现在在不在），
        #   `n` 只进 detail 供人诊断。
        hit = look(strip, "停靠左侧", "dock", tries=4)
        check(C02_MENU, bool(hit),
              f"找的是菜单里那行「停靠左侧」—— OCR 认得的是字，"
              f"认不出图标，所以菜单项一改文案这条就得跟着改；"
              f"click_until 点到第 {n} 次才成（0 = 三次都没落进 WebView）")
        # ★ 原来这里是个裸 `return` —— 于是后面三条**一声不吭地消失了**，
        #   汇总只报「通过 N 项」，N 比该跑的少三，没人知道。
        #   2026-09-24 实测就撞上了：那次的收尾行只说「C02 中途 return 交卷了」，
        #   既没说少了哪几条、也没把它们算进「没验」。
        #   现在改成登记成没验（同 C03/C04 的处理）。
        if depended(bool(hit), [C02_LEFT, C02_W, C02_H],
                    "停靠菜单没弹出来（见上面那条）—— "
                    "「点菜单项 → 窗口换边」这一跳压根没发生，这三条等于没测到"):
            case_end("C02")
            return

        BOX.click(hit)
        ok = BOX.wait_for(
            lambda: win32gui.GetWindowRect(panel)[0] == mon[0], 8)
        rect1 = win32gui.GetWindowRect(panel)
        check(C02_LEFT,
              ok and rect1[0] == mon[0],
              f"点之前 {rect0}　点之后 {rect1}　屏左边界={mon[0]}")
        check(C02_W,
              rect1[2] - rect1[0] == 520,
              f"宽 {rect1[2] - rect1[0]} 物理像素")
        check(C02_H,
              rect1[1] == mon[1] and rect1[3] == mon[3],
              f"上下 {rect1[1]}~{rect1[3]}　屏 {mon[1]}~{mon[3]}")
        case_end("C02")
    finally:
        kill(app)


# ★ C03 后面几条断言的**名字只在这里写一次**。
#   因为「跑到了就 check、没跑到就 skip」是**两个调用点**，
#   名字抄两份的话，改了一处、另一处还留着旧名字 ——
#   而症状是「skip 的名字和 check 的名字对不上」，
#   在两份输出里各看各的，永远发现不了。
#   （本项目那句「同一个字段有几个调用点就是几个守卫目标」。）
C03_COLLAPSE = "★ 点「—」之后窗口收成了细条"
C03_TITLEBAR = "★★ 折叠后正好是标题栏那么高（规格：.titlebar 的 42 CSS）"
C03_EXPAND = "★★ 折叠状态下「展开」按钮还点得到，点完铺回整屏"
C03_POS = "★ 展开后位置没跑（还在原来那条边上）"


def t_c03():
    section("C03 · 折叠成一条 / 再展开")
    case_begin("C03")

    got = bring_up("C03")
    if not got:
        return
    app, panel, rect0 = got
    try:
        mon = monitor_of(panel)
        # ★ 走 `click_until` —— 项目老规矩，`CLAUDE.md` 第 4 条：
        #   「点按钮一律走 click_until，不许『activate 一次 + BOX.click 一下』」。
        #
        #   ★★ 而且它**顺手把诊断也做了**：返回值就是「点到第几次才生效」。
        #      2026-09-24 这一批实测，前两轮用的都是单点版，C02/C03/C04 连红两轮，
        #      而三张截图里光标**都精确停在按钮上**（悬停高亮 + 出 tooltip），
        #      前台也确认过了 —— 也就是说「点空」和「产品没反应」
        #      这两种解释都装得下现场，光看红的那一行分不出来。
        #      `n` 就是分界：
        #        n >= 2 → 前 n-1 次**点击没落进 WebView**（环境/激活问题）
        #        n == 0 → 三次都送到了，产品就是没反应（那才是产品的红）
        #      ⚠ `btn-min` 打的是 `api().collapse()` → `main.Api.collapse()`
        #        （`main.py:369`），它**一行日志都不打** ——
        #        所以「日志里没有」在这里**证明不了任何事**，
        #        `CLAUDE.md` 第 5 条那条「看 app 日志有没有那行业务日志」
        #        在这条路径上没有日志可查。
        #      ★ 但 `shell.set_rect()` 摆完会**回读核对**，对不上会打
        #        `[问了吗] 摆位对不上…` —— 那不是「点击送到了」的判据，
        #        是**折叠本身成没成**的判据（2026-09-24 就是靠它破的案）。
        #
        # ★★★ 2026-09-24 修：`ok()` 原来判的是 `高度 <= 60`（一个**拍出来的数**），
        #   而结论比的是同一件事 —— **两个问题被混成了一个**：
        #     · 「折叠这件事发生了没有」→ 决定 `click_until` 要不要再点一次
        #     · 「折叠到了产品自己说的那个高度没有」→ 这才是结论
        #   混在一起的后果现场就撞上了：窗口**真的折了**（1600 → 150，
        #   产品日志白纸黑字 `摆位对不上…实际 (2040, 0, 2560, 150)`），
        #   而 `ok()` 因为 150 > 60 说没折 → 又点两下 → `shell.collapse()`
        #   是**开关**（`shell.py:256`：折了就展开），后两下把它**折回来**了
        #   → 最后量到 1600，报告成「折叠没发生」。
        #   **判据站错了位置，就会把已经发生的事说成没发生。**
        #   现在：`ok()` 只问「高度明显变小了没有」（纯重试用）。
        h0 = rect0[3] - rect0[1]
        n = BOX.click_until(
            panel, winbtn(rect0, panel, "min"),
            lambda: (win32gui.GetWindowRect(panel)[3] -
                     win32gui.GetWindowRect(panel)[1]) < h0 // 2,
            "折叠", tries=3, wait=2.0)
        r1 = win32gui.GetWindowRect(panel)
        h1 = r1[3] - r1[1]

        # ★★ 下面四条的前置条件都是「折叠这件事真的发生了」。
        #
        #   ⚠ 2026-09-24 实测踩到：C03_COLLAPSE 红着（窗口还是全高），
        #     而 C03_EXPAND / C03_POS **照样报了通过**。原因是「展开」那条
        #     的判据是「点完之后高度 == 屏高」—— 窗口本来就已经是全高的，
        #     点什么都不会变，于是它**无事可做地绿了**。
        #     它绿的是「没有发生变化」，而它顶着的名字是
        #     「折叠状态下『展开』按钮还点得到」。
        #     **这正是本项目最怕的那种绿：名字说着一个没被验过的事实。**
        #   ⚠ 条件用的是 `n`（「折叠发生了没有」），**不是**「折到目标高度没有」
        #     —— 后者是下面两条 check 的事。分开之后，就算高度数不对，
        #     只要窗口确实矮下去了，「展开」这一跳就**真的能测**。
        if depended(bool(n),
                    [C03_COLLAPSE, C03_TITLEBAR, C03_EXPAND, C03_POS],
                    "点了三次，窗口高度没有明显变化（没折起来）—— "
                    "这四条都是在【折叠状态】上做的，一条都没测到"):
            case_end("C03")
            return

        # ① 产品说它要折成多高，它做到了没有。
        #    ★ 问**产品自己**要这个数（`shell.collapsed_h_for` 是唯一实现处），
        #      不在这儿另写一份公式 —— 另写一份的话，改了一处、另一处照样绿。
        #    ⚠ 2026-09-24 之前这里读的是常量 `shell_mod.COLLAPSED_H`，
        #      而那个常量是 **CSS 像素**、窗口要的是物理像素 —— 拿 42 去比
        #      150% 屏上的物理高度，**恒红**（那一轮就是这么红的）。
        want_h = main.shell_mod.collapsed_h_for(panel)
        check(C03_COLLAPSE,
              h1 == want_h,
              f"{h0} → {h1} 物理像素高　产品说要 {want_h}"
              f"（`shell.collapsed_h_for`）　"
              f"click_until 点到第 {n} 次才成",
              hint="折是折了的（高度确实小下去了）却到不了目标 —— 看 app 日志里"
                   "有没有 `[问了吗] 摆位对不上`：有的话是【窗口有高度下限】"
                   "（`main.py` 的 `create_window(min_size=…)` 就是压它的），"
                   "没有的话才是折叠本身没跑")

        # ② ★★★ 上面那条**是自证**，这一条才是真的规格。
        #    ① 比的是产品自己的函数：万一 `collapsed_h_for` 被改坏成别的数，
        #    **而窗口也跟着摆成那个数**，两边一起变、① 照样绿 ——
        #    那正是「验证器的四种失效模式」里的**自证**。
        #    「折叠成一条标题栏」是一条**规格**，规格得由测试这边独立钉住：
        #    用本文件自己那份 `.titlebar` 高度（`BAR_H`，见上面那段
        #    「改了样式就得回来改它们」的常量块）乘上实测缩放。
        #    ⚠ 两边是**两份独立的声明**：产品那份改了、或者谁把折叠改成
        #      别的高度，这条就该红，然后**由人来决定**改产品还是改规格 ——
        #      不该由产品自己说了算。这正是它和 ① 的区别。
        spec_h = int(BAR_H * _scale(panel))
        check(C03_TITLEBAR,
              h1 == spec_h,
              f"折叠后 {h1} 物理像素高　"
              f"本文件那份 `.titlebar` 规格是 {BAR_H} CSS × "
              f"缩放 {_scale(panel)} = {spec_h}")

        # ★ 折叠之后窗口只剩标题栏那么高 —— 所以这里要单独确认
        #   **「展开」那个按钮还在够得着的地方**，
        #   不然折叠就成了单程票，用户只能重启。
        #   ⚠ 「折到对的高度」和「折完还能展开」是两件事：上面①②量的是前者，
        #     这条走的是后者，所以它**不跟着①②的结论走**。
        #
        #   ★ 同样走 `click_until`。⚠ 这个不是开关（`app.js:112`
        #     打的是 `api().dock(state.edge || 'right')`，**幂等**），
        #     所以 `back_n` 可以放心当结论用 —— 和上面 C02/C19 那两个不同。
        back_n = BOX.click_until(
            panel, winbtn(r1, panel, "max"),
            lambda: (win32gui.GetWindowRect(panel)[3] -
                     win32gui.GetWindowRect(panel)[1] == mon[3] - mon[1]),
            "展开", tries=3, wait=2.0)
        back = bool(back_n)
        r2 = win32gui.GetWindowRect(panel)
        check(C03_EXPAND,
              back,
              f"展开后高 {r2[3] - r2[1]}　屏高 {mon[3] - mon[1]}；"
              f"click_until 点到第 {back_n} 次才成（0 = 三次都没落进 WebView）",
              hint="这条防的是「折叠容易、展开没门」—— 用户只能重启程序")
        check(C03_POS,
              r2[0] == rect0[0] and r2[1] == rect0[1],
              f"折叠前 {rect0[:2]}　展开后 {r2[:2]}")
        case_end("C03")
    finally:
        kill(app)


# 同 C03：这几个名字是 `check` 和 `skip` **两个调用点**共用的，只写一次。
C04_BOX = "★★ 确认框是【画在面板里面】的（不是原生 confirm 弹到别处）"
C04_BTNS = "★ 确认框上连「确定/取消」两个按钮都在（不是只有一个死胡同）"
C04_STILL = "★★ 点「取消」之后窗口还在（确认框是能反悔的）"
C04_GONE = "★ 而且确认框自己也收起来了（不是叠在那儿挡着）"
C04_NATIVE = "★ 全程没有冒出原生对话框（#32770）"


def t_c04():
    section("C04 · 点「✕」弹的是【页面内】的确认框，点「取消」窗口还在")
    case_begin("C04")

    got = bring_up("C04")
    if not got:
        return
    app, panel, rect0 = got
    try:
        # ★ 走 `click_until`（项目老规矩，`CLAUDE.md` 第 4 条）。
        #   ⚠ 这条没有日志可查：`app.js:118` 的 `btn-close` 只做
        #     `$('closebox').hidden = false`，**一行后端调用都没有**。
        #     所以「日志里没有」在这里证明不了任何事 —— 判据只能落在
        #     「面板里到底画没画出那个框」上，`n` 就是唯一的分辨手段。
        #   ★ 它**不是开关**（点几下都只是再设一次 `hidden = false`），
        #     所以重试是幂等的，`n` 可以放心当结论用（C02 那个开关不行）。
        n = BOX.click_until(
            panel, winbtn(rect0, panel, "close"),
            lambda: bool(look(rect0, "关闭问了吗", "closebox", tries=1)),
            "关闭", tries=3, wait=1.5)

        # ★ 判据必须是「这句字出现在【面板自己那块矩形里】」。
        #   只判「屏幕上有『关闭问了吗』」的话，一个原生 confirm 弹在别处
        #   也照样绿 —— 而弹原生框正是这条用例要防的那件事
        #   （位置由 WebView2 说了算，面板是一条窄栏时它挂在边上）。
        hit = look(rect0, "关闭问了吗", "closebox", tries=4)
        check(C04_BOX, bool(hit),
              f"在面板那块矩形 {rect0} 里找「关闭问了吗」；"
              f"click_until 点到第 {n} 次才成（0 = 三次都没落进 WebView）",
              hint="找不到的话，看一眼冒烟测试\\_50_closebox.png 里到底画了什么")

        # ★ 下面几层原来写的是 `if hit:` / `if cancel:`。那个写法的毛病是
        #   **静默丢行**：确认框没找到时，里面那几条一句都不吭地消失了，
        #   汇总只报「通过 N 项」—— N 比该跑的少了几，**没人知道**。
        #   而「少了几项」和「那几项通过了」在报告上长得一模一样。
        #   （同全部回归那句「检查覆盖面 < 结论覆盖面 ＝ 把『没检查』
        #     伪装成『检查过了』」。）
        #   现在改成：条件不成立就把那几条【登记成没验】，一条都不许消失。
        if not depended(bool(hit), [C04_BTNS, C04_STILL, C04_GONE],
                        "确认框根本没找到（原因见上面那条红）—— "
                        "后面这几条都是在它身上做的，等于没测到"):
            check(C04_BTNS,
                  bool(look(rect0, "取消", "closebox2")) and
                  bool(look(rect0, "确定", "closebox3")),
                  "OCR 分别找「取消」和「确定」")

            cancel = look(rect0, "取消", "closebox4")
            # ⚠ 这一层【不 return】：最后那条「有没有原生对话框」不依赖
            #    `cancel`，它是**独立**的一条，不该陪着一起不跑。
            if not depended(bool(cancel), [C04_STILL, C04_GONE],
                            "面板上没找到「取消」这两个字 —— 点不到就验不了"):
                BOX.click(cancel)
                time.sleep(0.8)
                still = BOX.find_window(BOX.PANEL_TITLE, app.pid)
                check(C04_STILL,
                      bool(still),
                      f"进程{'还活着' if app.poll() is None else '没了'}")
                again = look(rect0, "关闭问了吗", "closebox5")
                check(C04_GONE,
                      not again, "「关闭问了吗」这行字已经不在屏幕上了")
        # ★ C04_NATIVE 依赖的是【点击有没有落进 WebView】，**不是**确认框出没出来 ——
        #   点击压根没发生的话，「没有原生对话框」是**空转绿**：
        #   什么都没点，当然没有框。上面那句注释说它「独立」，
        #   独立的是 `cancel` 那一路，不是这一路 —— 2026-09-24 改 `click_until`
        #   时才发现原来那句把两件事混成了一件。
        if depended(bool(n), [C04_NATIVE],
                    "三次点击一次都没落进 WebView（见 C04_BOX 那行）—— "
                    "「没有原生对话框」会变成空转绿：什么都没点，当然没有框"):
            case_end("C04")
            return
        check(C04_NATIVE,
              not _native_dialogs(app.pid),
              f"本进程的 #32770 窗口：{_native_dialogs(app.pid) or '一个都没有'}")
        case_end("C04")
    finally:
        kill(app)


def _native_dialogs(pid):
    """本进程里类名是 #32770 的窗口 —— 那就是 Windows 的原生对话框。

    ★ 加这条是因为「弹的是页面内的框」这句话**光看正面看不出来**：
      两者都会在屏幕上出现「关闭问了吗」四个字。要否掉原生那一半，
      得去问「有没有一个原生对话框窗口被建出来过」。
    """
    out = []

    def cb(h, _):
        try:
            if win32gui.GetClassName(h) == "#32770" and \
                    win32process.GetWindowThreadProcessId(h)[1] == pid:
                out.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


# ══════════════════════════════════════════════════════════
# D 组 · 快捷键录制：C19
# ══════════════════════════════════════════════════════════
#
# 用例原文：
#   19 | 操作台 → 快捷键 → 「录制新快捷键」→ 按 Ctrl+Shift+K |
#      录制成功、`hotkey-now` 显示新键；旧键失效、新键生效 | 🟢
#
# ★ 这一条**能自动化**，和 15~18 那四条不一样，值得说清楚为什么：
#   录制读的是**前端 JS 的 keydown**（`app.js` 的 `startRecording`），
#   那是一条普通的窗口消息，注入的按键走的是同一条路。
#   而 15~18 要的「按热键真的触发取词」走的是 **pynput 的全局钩子**，
#   它刻意忽略注入按键（防误触），所以那四条结构上验不了，只能人手。
#   —— 「能不能自动化」这件事本身就是**两条不同的通道**，别混成一句
#      「按键的事测不了」。


# ★ 同 C02/C03/C04：名字只写一次，`check` / `skip` 两个调用点共用。
C19_REC_BTN = "★ 操作台里找得到「录制新快捷键」这个按钮"
C19_SHOWN = "★★ 录完之后面板上显示的真是 Ctrl+Shift+K"
C19_SAVED = "★★ 新的快捷键真的落盘了（不是只改了界面上的字）"


def t_c19():
    section("C19 · 录制一个新快捷键，Ctrl+Shift+K")
    case_begin("C19")

    got = bring_up("C19")
    if not got:
        return
    app, panel, rect0 = got
    try:
        # ★ 点击之前先确认真前台 —— 见 `_activate_until_foreground` 的注释。
        if depended(_activate_until_foreground(panel),
                    [C19_REC_BTN, C19_SHOWN, C19_SAVED],
                    f"窗口抢不到前台（{_fg_detail(panel)}）—— "
                    "后面的点击都会被吃掉，三条都等于没测到"):
            case_end("C19")
            return
        # ★ 原来是 `if not _open_console(...): return` —— 裸 return，
        #   后面三条一声不吭地消失（同 C02 那处，本项目反复栽的那一类）。
        if depended(_open_console(rect0, panel),
                    [C19_REC_BTN, C19_SHOWN, C19_SAVED],
                    "操作台没进去（见上面那条红）—— 「快捷键取词」那张卡"
                    "压根没露过面，三条都等于没测到"):
            case_end("C19")
            return

        # ⚠ 操作台比一屏长（Key / 知识库 / 自动通道 / 快捷键 / 屏蔽词 / 提示词
        #   六张卡）。「快捷键」是第 4 张，**可能不在视口里** ——
        #   而找不到的样子是「按钮没了」，看着像产品坏了。
        #   所以往下滚几格再找，并说明是滚过才找到的。
        rec = _find_scrolling(rect0, "录制新快捷键", "hkrec")
        check(C19_REC_BTN, bool(rec),
              "OCR 在面板那块矩形里找这五个字（找不到就往下滚 3 格再找）")
        if depended(bool(rec), [C19_SHOWN, C19_SAVED],
                    "操作台里找不到「录制新快捷键」这个按钮 —— "
                    "点不到它，后面两条等于没测到"):
            case_end("C19")
            return

        # ⚠ 滚过之后焦点可能不在面板上了，点之前再确认一次前台。
        _activate_until_foreground(panel)
        # ★ 走 `click_until`（项目老规矩，`CLAUDE.md` 第 4 条）。
        #   判据用「按钮自己那行字变没变」—— `app.js:353` 起录之后
        #   那句会变成「按下组合键…（Esc 取消）」，是**只有录起来才会出现**的字
        #   （同第 13 条那条「挑只有那一个渲染点才会出现的字」）。
        #   ⚠ 录起来之后前台会变（全局钩子在听），所以每轮 `click_until`
        #     都会重新激活一次 —— 那是对的，不是多余动作。
        n_rec = BOX.click_until(
            panel, rec,
            lambda: bool(look(rect0, "按下组合键", "hkrec2", tries=1)),
            "录制新快捷键", tries=3, wait=1.5)
        # ⚠ 这个按钮**也是开关**（`app.js:400`：`if (recording) stopRecording();
        #   else startRecording();`）—— 同 C02 那处，`n_rec` 不能当结论：
        #   `ok()` 的 OCR 要是漏认了一轮，下一轮那一下就把它**关掉**了。
        #   结论一律落在**最后那次 `look`** 上（现在到底在不在录制态），
        #   `n_rec` 只进 detail 供人诊断。
        began = look(rect0, "按下组合键", "hkrec2b", tries=2)
        if depended(bool(began), [C19_SHOWN, C19_SAVED],
                    f"点了 {n_rec or 3} 次都没能让它进入录制态"
                    "（按钮上那行字始终不是「按下组合键…」）—— "
                    "录制压根没开始，后面两条等于没测到"):
            case_end("C19")
            return
        time.sleep(0.2)
        # 按下 Ctrl+Shift+K。⚠ 注入的按键 WebView2 收得到（它只是一条窗口消息），
        #   这跟 pynput 那条全局钩子是两回事。
        _press_combo()
        time.sleep(1.0)

        shown = look(rect0, "Ctrl+Shift+K", "hknew", tries=4)
        check(C19_SHOWN, bool(shown),
              f"OCR 在面板里找「Ctrl+Shift+K」；"
              f"点「录制新快捷键」用了 {n_rec} 次才进入录制态",
              hint="找不到的话看冒烟测试\\_50_hknew.png：要是显示的是 "
                   "「Ctrl+E」或者「按下组合键…」，说明录制那一跳没接上")

        # ★ 盘上那份也要跟着变。「界面上写着新键、盘上还是旧键」
        #   正是本项目最怕的那类：看着好了，重启一开又回去了。
        with open(TMP_SETTINGS, encoding="utf-8") as f:
            saved = json.load(f)
        on_disk = saved.get("hotkey")
        # ★★★ 2026-09-24 修：这条原来是
        #        saved.get("hotkey") == "ctrl+shift+k"     ← 小写全拼
        #     **产品是对的，写错的是这条断言。** 实测那一轮打出来的是
        #     `盘上 hotkey='Ctrl+Shift+K'`，而 app 日志同一行是
        #     `[快捷键] 改为 Ctrl+Shift+K` —— 两边都好好地存着，
        #     是**测试这边拿了一个产品从来不用的写法去比**。
        #     根因：`hotkey.py:48` 的 `DEFAULT_COMBO = "ctrl+e"` 是小写，
        #     照着它推「存的就是小写」；而 **`_combo` 存的其实是【显示写法】**
        #     （`hotkey.py:543` 那行注释写得清清楚楚），小写全拼只是
        #     `to_pynput()` 的**入参**格式，压根不是落盘格式。
        #     —— 「构建期断言必须照抄运行时读取函数」的又一例：
        #        这里该照抄的不是「读出来的值长什么样」，而是
        #        **产品自己认不认这个值**。
        #     所以改成问产品自己：两边都过一遍 `to_pynput()` 再比。
        #     这样即使将来落盘格式换成别的写法，只要产品能读回来，这条就还是绿的
        #     —— 而它真正要防的那件事（盘上没跟着变）照样会红。
        # ⚠ 读它可能抛（盘上那个串要是坏的）—— 抛了也只是一条红，
        #   不该把整份脚本带走，所以包一层。
        def _as_pynput(s):
            try:
                return hk.to_pynput(s) if s else None
            except Exception as e:            # noqa: BLE001
                return f"（读不出来：{type(e).__name__}）"

        got_key, want_key = _as_pynput(on_disk), _as_pynput("Ctrl+Shift+K")
        check(C19_SAVED,
              got_key is not None and got_key == want_key,
              f"盘上 hotkey={on_disk!r}（产品自己读它 → {got_key}，"
              f"期望 Ctrl+Shift+K → {want_key}）　"
              f"app 日志：{_last_log('改为') or '（没有「改为」这行）'}")
        case_end("C19")
    finally:
        kill(app)


def _last_log(needle):
    """app 日志里最后一条含 needle 的行（诊断用，别拿它当断言）。"""
    try:
        with open(APPLOG, encoding="utf-8", errors="replace") as f:
            hits = [ln.strip() for ln in f if needle in ln]
    except OSError:
        return ""
    return hits[-1] if hits else ""


def _find_scrolling(rect, needle, tag, rounds=4):
    """先在视口里找，找不到就往下滚一格再找。

    ⚠ 滚轮要落在**面板内部**：落在外面滚的是别的程序，
      而它不报错，只是这条断言静默地找不到东西。
    """
    # ⚠ rect 是 (左, 上, 右, 下)，高度是 rect[3] - rect[1]。
    #   写成 `rect[1] + rect[3] // 2` 在本机**看着也对** —— 面板铺满整屏、
    #   上边是 0，于是它凑巧落回屏幕中间。换一块带上边任务栏的屏就滚到面板外面，
    #   而滚到外面**不报错**，只是这条断言静默地什么都找不到。
    pos = (rect[0] + 24, rect[1] + (rect[3] - rect[1]) // 2)
    for i in range(rounds):
        hit = look(rect, needle, f"{tag}{i}" if i else tag)
        if hit:
            if i:
                print(f"       （往下滚了 {i} 格才进视口 —— 不是坏了）")
            return hit
        if i < rounds - 1:
            BOX.wheel(pos, -3)
            time.sleep(0.5)
    return None


def _open_console(rect, hwnd):
    """点标题栏齿轮进操作台。齿轮带的标签是「设置」两个字（见 index.html）。

    ⚠ **只在标题栏那一条里找**，不是「面板上部随便哪一块」——
      `ocr_find` 是**子串**匹配，而下面「取词区域」那张卡上挂着一枚
      `region-pill`，文案是「**未设置**」，它照样含「设置」两个字。
      全屏找的时候谁先命中，取决于 OCR 把哪块排在前面（通常按阅读顺序，
      所以标题栏那条通常赢）—— 但**「通常」不是判据**：
      输的那次会点空，红出来的名字是「操作台进不去」，
      而真因是 OCR 命中了一枚状态标签。
      按标题栏高度切，`未设置` 根本不在画面里，这件事就不存在了。
    """
    s = _scale(hwnd)
    strip = (rect[0], rect[1], rect[2],
             rect[1] + int(BAR_H * s) + 2)        # +2：别把边框切掉
    hit = look(strip, "设置", "gear", tries=4)
    if not hit:
        check("★ 标题栏上找得到齿轮「设置」", False,
              f"找的是那两个字，不是 ⚙ —— OCR 认不出符号。"
              f"只在标题栏那条 {strip} 里找（下面「未设置」那枚状态标签"
              f"也含「设置」，所以不能整块面板找）")
        return False
    BOX.click(hit)
    time.sleep(0.8)
    return True


def _press_combo():
    """按下 Ctrl+Shift+K。

    ★★ 2026-09-24：**必须把扫描码一起送**（`MapVirtualKey(vk, 0)`）。
       原来写的是 `keybd_event(vk, 0, 0, 0)` —— 第二三个参数一起说明
       「虚拟键码给了，扫描码给 0」。

       而 WebView2 里 Chromium 的 `KeyboardEvent.code` 是**从扫描码算出来的**，
       扫描码为 0 时 `e.code` 并不是 `'KeyK'`。前端
       `app.js` 的 `mainKeyFromCode()` 只认 `/^Key[A-Z]$/`，
       认不出来就回一句：
       > 这个键不支持。主键请用 A~Z、0~9 或 F1~F12。

       —— **那一句实测出现在了 `_50_hknew.png` 上**：
       按钮还停在「按下组合键…（Esc 取消）」，底下正是这行字。
       所以录制那一跳**根本没断**（`onRecordKey` 收到了 keydown、
       还按 `e.code` 判了），断的是**这个测试喂进去的输入**。

    ⚠ 判据不是「补上扫描码就绿了」——那是拿结果当理由。理由是：
      **真键盘敲下去一定带扫描码，硬件给的**。给 0 是一种真实键盘
      产生不出来的事件，所以它不是「更严格的测试」，是**输入不真**。
      补上它只是让合成输入回到用户真会产生的那一个，
      **断言一个字没动**（还是要 `Ctrl+Shift+K` 落盘）。

    ⚠ 也别退回 `KEYEVENTF_UNICODE`（`type_text` 用的那个）：
      那条路造出来的是**字符**，没有 keydown、没有修饰键，
      录制要的恰恰是 `ctrlKey/shiftKey` + `code`，收不到。
    """
    def press(vk, up=False):
        win32api.keybd_event(
            vk, win32api.MapVirtualKey(vk, 0),
            0 if not up else win32con.KEYEVENTF_KEYUP, 0)

    VK_CTRL, VK_SHIFT, VK_K = 0x11, 0x10, 0x4B
    for vk in (VK_CTRL, VK_SHIFT):
        press(vk)
    press(VK_K)
    time.sleep(0.05)
    press(VK_K, up=True)
    for vk in (VK_SHIFT, VK_CTRL):
        press(vk, up=True)


# ══════════════════════════════════════════════════════════
# 出口自检（尺子）—— 必须在任何 try 之外，且在任何断言之前
# ══════════════════════════════════════════════════════════

def _snapshot():
    return (len(PASS), len(FAIL))


def _restore(t):
    """把两个计数**原位**还回去。

    ⚠ 用切片赋值（`PASS[:] = ...`）而不是重新绑定名字 ——
      探针拿的是 `snapshot`/`restore` 两个函数，它看不到 `PASS` 这个变量；
      重新绑定的话改的是模块里这个名字，别处若还留着旧引用就分叉了。
      这也正是 `尺子.py` 里那句「是 `restore(快照)`，不是『减掉 2』那种心算」。
    """
    PASS[:] = PASS[:t[0]]
    FAIL[:] = FAIL[:t[1]]


def gate():
    return 尺子.gate(check, _snapshot, _restore, label="跑50条")


# ══════════════════════════════════════════════════════════
# --selfcheck：把每条判据拿一个**坏输入**喂一遍
# ══════════════════════════════════════════════════════════
#
# ★ 这是本项目最贵的一条规矩：
#
#     「新加一条断言跑绿不算数 —— 给它配的变体不红，它就是装饰品。」
#
#   上面那些断言跑绿只能证明「当时产品是对的」。产品**将来**改坏了、
#   这条断言还会不会红，是另一件事 —— 那件事只能靠喂坏输入来证明。
#
# ⚠️ 为什么是「把判据拎成函数、喂坏输入」，而不是「去改产品源码再跑一遍」：
#   两者都能证明判据会红，但改源码那种**有副作用**（改的时候产品是坏的，
#   万一中途退出，用户盘上就留着一份坏代码）。
#   拎出来喂坏输入是**纯内存**的，随时可跑、零风险，所以内置成 `--selfcheck`。
#   （实测两条路都走过：`_import_log_line` 那次是改源码验的，
#     因为「调用点到底调没调」只有改源码才问得出来 —— 那种情况另说。）
#
# ⚠️ **这里列的不是全部判据**：只覆盖「判据本身是一个函数」的那些。
#   几何那几条（窗口 rect 等于什么）是**直接比较**，喂坏输入等于自己跟自己
#   较劲，证明不了什么 —— 那几条的「会红」由 `全部回归` 里那种
#   「把产品改坏、变体当场红」的方式保证。本文件末尾照实说明这件事。

def _depended_probe(cond):
    """喂 `depended()` 一个前置条件，返回「它的行为对不对」。

    ★★ 这条探针**第一版是错的，而且错成了它自己要防的那个样子**。
      第一版只看 `depended()` 的**返回值**。写完之后按规矩配了个变体
      （把 `depended` 改成「返回 True 但一条都不登记」）——
      **它照样全绿。** 因为那个坏掉的实现返回的也是 True。

      也就是说：一条专门用来防「静默丢行」的探针，
      **自己就是靠「它说它会记」而不是「它真的记了」来判的**。
      和 C03 那条空转绿、和「检查覆盖面 < 结论覆盖面」是同一个形状。

      所以现在**两个都判**：返回值**和** `SKIP` 里真的多了几条。
      「说」和「做」是两件事，只判一件的断言，另一件坏了它不知道。

    ⚠ 探针自己会把 `SKIP` 弄脏（`depended` 一登记就往里塞）——
      跑完必须还原，不然 `--selfcheck` 会在正式报告里凭空多出几条没验。
      `SKIP[:] = keep` 改的是同一个列表对象（不是重新绑定名字），
      所以 `check`/`skip` 那几个闭包里看到的是同一份。
    """
    keep = list(SKIP)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            said = depended(cond, ["（探针甲）", "（探针乙）"], "（探针）")
        recorded = len(SKIP) - len(keep)
    finally:
        SKIP[:] = keep
    # ★ 两个都要对：它【说】该登记，**并且**它真的把那两条登记下去了。
    #   · 前置条件不成立 → 两句都为真 → True
    #   · 前置条件成立   → `said` 就是 False → 整句 False（合上 selfcheck
    #     那条「好输入→真、坏输入→假」的约定）
    #
    # ⚠ 第二半（`recorded == 2`）一个字都不能省 —— 见上面那段：
    #   只判 `said` 的第一版，被「说 True 却什么都不记」那条变体骗过去了。
    return said and recorded == 2


def _exit_code_probe(fails, skips, truncated):
    """喂 `exit_code()` 三样已知的东西，返回「这份算不算全绿」。"""
    return exit_code(fails, skips, truncated) == 0


SELF_PROBES = [
    # (判据名, 判据函数, 「好的输入」, 「坏的输入」)
    ("C09 断网判据",
     _offline_msg_ok,
     "连不上 DeepSeek，检查一下网络",
     "API Key 不对，或者已经失效了"),

    ("C49 泄漏判据",
     lambda msg, detail: not _leaks(msg, detail),
     ("", "HTTPConnectionPool 连接被拒绝"),
     ("Key 不对", f"发送的头是 Bearer {KEY_FOR_TEST}")),

    ("C05 十项都在",
     _all_kept,
     dict(C05_VALUES),
     {**C05_VALUES, "hotkey": "ctrl+e"}),

    # ★ 「没跑完」的报数。四扇门全跑到头 → 一条都不该报；
    #   C04 开了头没收尾 → 必须把它报出来。
    #   ⚠ 这条探的是**报数**，不是产品 —— 它坏掉的样子是
    #     「四条里有一条中途交卷了，报告却干净得像全跑完了」。
    ("「没跑完」的报数（truncation）",
     lambda started, done: not truncation(started, done),
     ({"C02", "C03", "C04", "C19"}, {"C02", "C03", "C04", "C19"}),
     ({"C02", "C03", "C04", "C19"}, {"C02", "C03", "C19"})),

    # ★ 2026-09-24 新加的第三个判决（没验）。两条，各钉一面：
    #   ① 前置条件不成立 → 必须登记（不许静默丢掉那几条）
    #   ② 前置条件成立   → 必须**不**登记（不许把验过的说成没验）
    #   少了②的话，「它登记了」可能只是因为它无条件登记。
    ("C03/C04 的「没验」登记（depended）",
     _depended_probe,
     False,          # 前置条件不成立 → 该登记 → True
     True),          # 前置条件成立   → 不该登记 → False

    # ★ 「没验」必须算进退出码。它坏掉的样子是**静默**的：
    #   一份有没验项的报告，和一份全绿的，退出码一样、也没有别的信号。
    ("「没验」也要算进退出码（exit_code）",
     _exit_code_probe,
     ([], [], []),                       # 三样都空 → 全绿，0
     ([], ["★ 某条没验"], [])),          # 有没验 → 必须非 0

    # ★★ 2026-09-24 新加的：C02/C03/C04/C19 现在**先问一句「窗口真是前台吗」**
    #    才点。这一问要是坏了，四条用例会退回原来那个形状 ——
    #    点击被吃掉、按钮像没反应、红的却顶着功能断言的名字。
    #    ⚠ 两个方向都要：
    #      · **能说不**（给 0，一个成不了前台的 hwnd）—— 少了这条，
    #        一个恒为 True 的版本会让四条**盲点**，正是这一批红了两轮的那个形状；
    #      · **能说是**（给这一刻的真前台）—— 少了这条，一个恒为 False 的版本
    #        会把四条全登记成「没验」，报告一片「没验」，看着很诚实，
    #        其实是**尺子坏了**，不是产品没测到。
    #    ⚠ 正向那半是弱一点的：`activate()` 认出「已经是前台」就直接返回，
    #      所以它验的是**接线**（读得到、比得对），不是「能把前台抢过来」——
    #      抢前台那件事非得有一个真窗口才验得了，这里没有。
    #      弱归弱，它挡得住「恒 False」那一半。
    ("窗口抢前台判据（_activate_until_foreground）",
     lambda use_real: _activate_until_foreground(
         win32gui.GetForegroundWindow() if use_real else 0, tries=1),
     True,                               # 真前台 → 必须认出来
     False),                             # 成不了前台的 hwnd → 必须说不
]


def selfcheck():
    section("--selfcheck · 每条判据喂一个坏输入，看它会不会红")
    bad = []
    for name, fn, good, badin in SELF_PROBES:
        try:
            got_good = fn(*good) if isinstance(good, tuple) else fn(good)
        except TypeError:
            got_good = fn(*good)
        try:
            got_bad = fn(*badin) if isinstance(badin, tuple) else fn(badin)
        except TypeError:
            got_bad = fn(*badin)
        ok = bool(got_good) and not bool(got_bad)
        print(f"  [{'通过' if ok else '不通过'}] {name}："
              f"好输入→{bool(got_good)}　坏输入→{bool(got_bad)}")
        if not ok:
            bad.append(name)
    print(f"  ── 探了 {len(SELF_PROBES)} 条判据，"
          f"{len(SELF_PROBES) - len(bad)} 条能被弄红")
    if bad:
        print(f"  ⚠ 这些判据喂坏输入照样为真，它们是【装饰品】：{bad}")
    return not bad


def exit_code(fails, skips, truncated):
    """这一份该以什么退出码收场。0 = 真的全绿。

    ★ 拎成纯函数，是为了 `--selfcheck` 能拿已知的坏输入喂它 ——
      「没验」这一类坏掉的样子是**静默**的：它不进退出码的话，
      一份有没验项的报告会和全绿的一模一样。

    ⚠ 三个输入一个都不能漏：「没跑完」和「没验」是两种不同的没验过，
      但它们**都不是通过**，所以三个都要算进非零。
    """
    return 1 if (fails or skips or truncated) else 0


def main_(argv):
    print("=" * 62)
    print("跑「测试用例50条.md」里现在能跑的那些")
    print("=" * 62)

    # ★ 先验尺子：`check` 是这里全部断言的唯一出口，它坏了每条都会变绿。
    if not gate():
        return 2

    if "--selfcheck" in argv:
        return 0 if selfcheck() else 3

    before_real_settings = sha(REAL_SETTINGS)
    before_real_kb = sha(REAL_KB)

    if "--logic-only" not in argv:
        # ★ 开窗之前的两道门，和那四个开窗脚本用同一份（`框选联调.py`）。
        #   锁屏时截屏拍到的是一张壁纸，后面每条 OCR 断言都会红成
        #   「面板上没有这几个字」，而**原因全是错的** —— 本项目明令禁止
        #   锁着屏跑开窗脚本。屏幕上要是有第二个「问了吗」，截图会截到压在
        #   上面的那个：读的是别人的界面，红的却是「功能」的名字。
        if not BOX.require_unlocked():
            return 1
        if not BOX.require_clean_screen():
            return 1
        # `--only C03` 单跑一条（改了一条要复现时才用；**别拿它代替全跑**）。
        only = [a.split("=", 1)[1] for a in argv if a.startswith("--only=")]
        only = only[0].split(",") if only else None
        table = {"C02": t_c02, "C03": t_c03, "C04": t_c04, "C19": t_c19}
        if only:
            print(f"\n（--only：这次只跑 {only}）")
        seed_50_settings()
        for tag, fn in table.items():
            if only and tag not in only:
                continue
            try:
                fn()
            except BOX.Bail as e:
                # ★ 光标摆不动 / 跑到一半锁屏 —— **这一条没跑完**（`Bail` 的
                #   两个子类，见 `框选联调.Bail`）。
                #
                # ⚠ 这里必须收：不收的话它会一路冒到 `main_()` 外面，
                #   把**后面几条一起带走** —— 2026-09-24 实测就是这么死的
                #   （一次两分钟的光标抽风，C03/C04/C19 三条一条都没跑到）。
                # ⚠ 也【不许】在这里补一句 `case_end(tag)`：那会把一条没跑完的
                #   记成跑完了，正好把这套账做废。不调用它，`truncation()`
                #   下面如实报出来 —— 它就是为这件事写的。
                BOX.why_bail(e)
                print(f"      （C 组的 {tag} 停在这儿了，后面的几条继续跑）")
    else:
        print("\n（--logic-only：开窗那四条 C02 / C03 / C04 / C19 这次不跑）")

    t_c05()
    t_c09()
    t_c49()

    # ── D 组自己核一遍：用户的真数据从头到尾没被打开过 ────────
    section("收尾 · 用户真数据有没有被碰过")
    check("★ app\\settings.json 哈希前后一致（那把唯一的 Key 没丢）",
          sha(REAL_SETTINGS) == before_real_settings,
          f"{before_real_settings} → {sha(REAL_SETTINGS)}")
    check("★ app\\kb.db 哈希前后一致（测试从没碰过用户真库）",
          sha(REAL_KB) == before_real_kb,
          f"{before_real_kb} → {sha(REAL_KB)}")

    # ── 开窗那四条：跑到头了没有 ──────────────────────────
    # ★ 「没跑完」和「跑完了」不能长得一样。中途 `return` 交卷的那几条
    #   少的项数这里数不出来（没跑到的断言根本没登记），所以只能说
    #   「哪几条没跑完」，不能说「少了几项」—— 后者是编的。
    truncated = truncation(_started, _done)
    if truncated:
        print(f"\n  ⚠ 这几条【没跑完】—— 中途 `return` 交卷了，"
              f"它们停在哪一条要看上面的输出：{'、'.join(truncated)}")

    print("\n" + "-" * 62)
    # ★ 三个判决分开打，一行一个。**不合成一句「通过 N 项」** ——
    #   没验和通过合成一句，就是把「没验过」和「验过了没问题」
    #   说成同一个样子，那正是本项目最忌讳的撒谎方向。
    print(f"通过 {len(PASS)} 项，不通过 {len(FAIL)} 项，没验 {len(SKIP)} 项")
    if FAIL:
        print("不通过的：")
        for n in FAIL:
            print(f"  · {n}")
    if SKIP:
        print("没验的（前置条件没成立，这几条等于没测）——")
        print("⚠ 它们【不算通过】。报告里没验的项数就是这几条：")
        for n in SKIP:
            print(f"  · {n}")
    code = exit_code(FAIL, SKIP, truncated)
    if code:
        print("⚠ 这份报告【不是全绿】：非 0 收场。")
    return code


if __name__ == "__main__":
    sys.exit(main_(sys.argv[1:]))
