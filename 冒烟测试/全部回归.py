# -*- coding: utf-8 -*-
"""一次跑完七个脚本，**每一轮都留全量日志**。

    python "Desktop\\问了吗\\冒烟测试\\全部回归.py"

────────────────────────────────────────────────────────
为什么要有这个文件（真实踩过，不是预防性设计）：

v0.8 第二轮回归是内联跑的，命令把每个脚本的输出 `tail -1` 了一下 ——
于是 `知识库端到端联调` 报「45 通过 / 3 不通过」时，**那 3 条是哪 3 条没留下来**。
重跑一次全绿，说明是偶发；可偶发也得知道偶发在哪一条上，
否则「48 项里偶尔红 3 项」就成了一条谁都不敢信的测试。

明细一旦丢过就找不回来了（截图也被下一轮覆盖），所以这里强制落盘：
每轮一个带时间戳的目录，七个脚本各一份完整 stdout。

────────────────────────────────────────────────────────
跑之前请务必知道两件事：

1. **跑的时候别动鼠标。** 四个端到端脚本是真开窗口、真点、真截图看 ——
   它们的点击是合成出来的（`SetCursorPos` + `mouse_event`），你同时动光标
   会跟它们抢位置；示例笔记窗口也摆在屏幕固定位置，你切个窗口盖住它，
   失败的是「功能」的名字，坏的是环境。

   ⚠ v0.13 起这句话【缩了一圈】，别说成「整个不能用电脑」：
     原来连「键盘鼠标有没有在动」都算数 —— 自动通道当时要等
     「系统空闲 ≥60s」，你碰一下它就归零，那个功能永远不触发
     （v0.11 为这条误判查了一整轮）。**那个条件已经删了**
     （触发只看画面稳定，见决策 45 的 v0.13 修订），
     所以**单纯敲键盘不再会害它失败**。
     上面那条「别动鼠标」照旧 —— 那是合成点击的问题，跟触发条件无关。
2. **四个开窗口的脚本必须串行**，不能并行 —— 它们抢同一个屏幕区域和前台焦点。

纯内存的四个（`知识库联调` / `AI接入联调` / `自动通道联调` / `快捷键联调`）
先跑，几秒钟，不开窗口。

⚠ `快捷键联调.py` 会【真动这台机器的剪贴板】（全局资源，见下面 NOCLIP 那段）。
   它排在开窗脚本前面 —— 那时候没人占着剪贴板，最容易拿到干净的一轮。
   但它和别的脚本**不并行**（本文件全程串行），所以不会互相踩。
"""
import contextlib
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")

# (脚本名, 期望项数, 是否开窗口)
SCRIPTS = [
    ("知识库联调.py",       80, False),
    ("AI接入联调.py",       78, False),
    ("自动通道联调.py",     49, False),
    ("快捷键联调.py",       50, False),
    # 2026-09-21 新增。纯内存 + 一个临时 settings 文件，不开窗口。
    # ⚠️ 它 `import main`（DPAPI 那对函数住在 main 里），但把
    #    `WENLEMA_SETTINGS` 指到 `_密钥联调_settings.json` ——
    #    **不碰用户真的那份**（脚本里 C.2 自己核一遍这件事）。
    ("密钥落盘联调.py",     33, False),
    ("知识库端到端联调.py", 69, True),
    ("AI端到端联调.py",     32, True),
    # 24 → 38：2026-09-21 补了三条「从没执行过的分支」
    # （`click_until` 的返回 0 那条路、`stray_panels` 真的拦到人那次、
    #   `screen_locked()` 判据那一半真的会说 True）。
    ("框选联调.py",         38, True),
    # 排最后：它自己会起一个「示例笔记」陪练窗口，并改写联调那份 settings
    # （`_e2e_settings.json`，不是用户真的那份）。放在其他开窗脚本前面的话，
    # 后面那些脚本会吃到它留下的区域设置。
    ("自动通道端到端联调.py", 18, True),
]

# 各脚本的汇总行措辞【三种】，一起认。都是真打出来过的原文：
#     `通过 78 项　失败 0 项`               （AI接入联调 / 知识库联调）
#     `通过 32 项，不通过 0 项`             （其余五个）
#     `通过 50 项、不通过 0 项、没测 0 项`   （快捷键联调 —— 三个数）
#
# ⚠️ 第三条那个「没测」是 2026-09-21 补上的。在那之前，
#   这一行只读前两个数，`没测 N` 里的 N **一个字节都没进过报告** ——
#   少验的那几项只能靠「项数对账」间接兜住（少一项 → 实到 ≠ 期望 → ❌）。
#   兜是兜住了，但**报告里没有任何一处写着「这项没测」** ——
#   而「没验过」和「验过了没问题」长得一模一样，正是本项目最忌讳的那件事。
SUMMARY = re.compile(
    r"通过\s*(\d+)\s*项.*?(?:不通过|失败)\s*(\d+)\s*项"
    r"(?:.*?没测\s*(\d+)\s*项)?")
# 明细行：各脚本 `check()` 打的失败行。
#
# ⚠️ 八份脚本用了【四种】失败前缀，2026-09-21 审计时才发现的：
#     `[不通过]` —— 框选联调 + 三个端到端 + 自动通道联调（5 份）
#     `[失败]`   —— 知识库联调、快捷键联调
#     `[!!]`     —— AI接入联调
#   而这里原来只认第一种，于是**另外三份的失败明细一条都进不了报告**：
#   报告上只有 `❌ 知识库联调 通过 78 不通过 2` 和一个日志路径，
#   不会告诉你那 2 条是什么 —— 而「那 3 条是哪 3 条，永远查不回来了」
#   **正是这个文件当初被写出来的全部理由**（见文件头那段 v0.8 事故）。
#   现在三种一起认（和上面 SUMMARY「措辞不统一，一起认」同一个办法）。
#
# ⚠️ 只影响【明细行显示】，不参与任何计数 —— `problems` / `skipped` /
#   项数对账都由别的判据算，看不看明细都不改变 ✅ 还是 ❌。
#   `全部回归.selfcheck()` 的 ⑤ 钉的就是这三种前缀都认得出来。
DETAIL = re.compile(r"^\s*\[(?:不通过|失败|!!)\]\s*(.*)$")

# 出口自检那道闸门打的标记（`框选联调.gate_check_selftest()` / `尺子.gate()`）。
#
# ★ 为什么「有闸门」不够、还要专门认这一行：
#   八份脚本每份都有一个 `check()`，而**它自己也可能是坏的**
#   （最凶的一种坏法是「无条件记通过」—— 那会让通过数正好等于期望，
#    项数对账照样通过，一屏全绿一个字都不报错）。
#   2026-09-21 给八份都配了自检闸门。但**闸门写了却没被调用**，
#   和「闸门跑了而且通过」在日志上长得一模一样 ——
#   于是「检查的覆盖面」又小于「结论的覆盖面」。
#   所以这里要求：**跑过的脚本，日志里必须有这一行。**
#   ⚠️ 只对【真跑过】的脚本要求；锁屏那种整段没跑的，不要求。
GATE_MARK = "check() 自检通过"

KEEP_RUNS = 5      # 只留最近 N 轮，别把磁盘堆满

# 开窗口那几个脚本开局会查锁屏（`框选联调.screen_locked()`），锁着就拒绝跑。
# ⚠ 这里必须认得出那句话，否则「屏幕锁了」会被记成「脚本挂了」——
#   而两者的修法完全不同：一个是去解锁，一个是去改代码。
#   真踩过：人走开了、机器锁了，四个开窗脚本报了一屏「面板上没有这几个字」，
#   每一条的原因都是错的。
LOCKED = "屏幕现在是【锁着的】"

# 跑到一半才锁的：脚本不打汇总行，改打
#     `截断了：通过 N 项，不通过 M 项，后面的没跑`
# （`框选联调.lock_bail()` 之后，各脚本 main() 里那个 `if interrupted:` 分支）
#
# ⚠ 那句话里带着上面的 LOCKED，所以这一种【分类】本来就是对的（⏸ 不是 ❌）——
#   错的只是措辞：对一份跑到一半的报告说「整段没跑」是句假话。
#   而这两种错法一样糟：说成「整段没跑」让真跑过的那些项白跑一遍，
#   说成「通过 N 项」把没验的说成验过了。
#   实测 2026-09-16 00:24 那次：`confirm_lock()` 还没修好，脚本以 raw mss
#   traceback 崩掉，连这句话都没打出来 —— 于是被记成「❌ 期望 69 项，实到 0 项」，
#   读起来像产品炸了，实际是屏幕锁了。
TRUNCATED = "截断了"

# 第三种「没跑完」，和前两种【不是一个形态】：
#     `快捷键联调.py` 的取词那一组要**真动这台机器的剪贴板**（全局资源）。
#     剪贴板里是图片/文件时它整组不跑 —— 理由和产品拒绝取词是同一条：
#     赔不起的东西不碰。它打的是 SKIP_MARK 那句话。
#
# ⚠ 措辞不能借用锁屏那两种：那条说的是「没解锁」，这条说的是
#   「剪贴板里有东西」。用户要做的事完全不同 ——
#   一个是解锁重跑，一个是随便复制一段文字再重跑。
NOCLIP = "剪贴板里不是纯文字，取词那组没跑"


def box_module():
    """拿 `框选联调.py` 里的锁屏判据。

    ⚠ 判据只留一个实现处（和 `kb.canon` / `shell.PANEL_W` 一个道理）——
      在这里抄一份的话，哪天判据改了，这份会【静静地】按老规矩判，
      不报错，只是把「锁着」当成「没锁」。
    """
    import importlib.util
    path = os.path.join(HERE, "框选联调.py")
    spec = importlib.util.spec_from_file_location("boxlink_reg", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["boxlink_reg"] = mod
    spec.loader.exec_module(mod)
    return mod


def prune(logroot):
    """只留最近几轮 —— 日志是拿来查案的，不是拿来归档的。"""
    try:
        runs = sorted(d for d in os.listdir(logroot)
                      if os.path.isdir(os.path.join(logroot, d)))
    except FileNotFoundError:
        return
    for old in runs[:-KEEP_RUNS]:
        shutil.rmtree(os.path.join(logroot, old), ignore_errors=True)


def skip_note(name, ok, expect, secs, skip, skip_n=0):
    """一个「没跑完」的脚本该怎么说。跑的一开始和汇总各用一次。

    ⚠ 四种情况【都】不算通过，但跑过多少不一样，措辞必须分开：
      · "locked"  —— 开局就锁着，一项都没验
      · "cut"     —— 跑到一半才锁，前面那些项是【真跑过的】，只是整份不算数
      · "noclip"  —— 只有取词那一组没跑，别的组是【真跑过的】
      · "partial" —— 脚本自己报的「没测 N 项」，别的项是真跑过的
    写成同一句话就是在撒谎，而撒谎的方向正好相反（见 TRUNCATED 那段）。
    """
    if skip == "cut":
        return (f"⏸ {name:<22} 跑到一半锁了：前 {ok}/{expect} 项跑过，"
                f"后面的没跑　（{secs:.0f}s）")
    if skip == "noclip":
        return (f"⏸ {name:<22} 取词那组没跑（剪贴板里不是纯文字）："
                f"别的组跑过 {ok} 项　（{secs:.0f}s）")
    if skip == "partial":
        return (f"⏸ {name:<22} 有 {skip_n} 项没测（脚本自己报的）："
                f"其余 {ok} 项跑过　（{secs:.0f}s）")
    return f"⏸ {name:<22} 锁屏，整段没跑　（{secs:.0f}s）"


# ══════════════════════════════════════════════════════════
# 判断逻辑 —— 全是纯函数。
#
# ★ 为什么从 `main()` 里提出来：**这一段提到的每一样东西，都是
#   「今晚绿没绿」这个结论的唯一依据**，而它自己从没被测过 ——
#   其中一条（settings 哈希那条网）在 2026-09-15 一边打 ✓
#   一边把用户的明文 API Key 覆盖成了 `sk-test-fake`。
#   提成纯函数之后，`--selfcheck` 才能拿【已知的坏输入】喂它们。
#
# ⚠️ 提出来的时候【判据一个字都不许改】—— 换个写法顺手「理顺」一下，
#   就等于把这份报告换成了一份没人验过的新报告。
# ══════════════════════════════════════════════════════════


def parse_summary(text):
    """→ (通过数, 不通过数, 没测数, 找着汇总行了没)。

    ⚠️ 汇总行可能有好几行（脚本会分节打），取【最后】那一条 ——
      那是脚本自己的结论，前面的可能是中途的分节小计。
    """
    m = None
    for line in text.splitlines():
        m = SUMMARY.search(line) or m
    if not m:
        return 0, -1, 0, False
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), True


def parse_fails(text):
    """失败明细行。前缀是 `check()` 定的，见 DETAIL 那段。"""
    return [DETAIL.match(l).group(1).strip()
            for l in text.splitlines() if DETAIL.match(l)]


def finished_normally(text):
    """这份日志是不是【跑到了自己的结论】。→ True/False

    ★★ 为什么需要这个、而不是只看 `LOCKED in text`：
       `LOCKED` 是【一句给人看的话】，而**一句话可以被断言引用**。
       2026-09-21 真踩到：`框选联调.py` 为「三条从没红过的判据」补了一条
       `t_lock_signal_unit()` —— 它故意把 `fg_exe` 换成桩，好让
       `require_unlocked()` 真的返回一次 False。于是那句警告**被真的打了出来**，
       回归读到它，把一份 **38 项全绿、跑完整整五节**的报告记成
       「锁屏，整段没跑」并排除在合计之外。日志一个字没错，判定错了。

    ★ 正确的判据是【结构】不是【措辞】：跑完的脚本一定会打一行正常汇总；
      开局锁屏的不会打任何汇总；跑到一半的只打那句「截断了：…」。
       而「这份日志里到底有没有一个正常汇总行」是伪造不出来的 ——
       断言可以引用一句话，但它不能凭空让脚本宣布自己跑完了。

    ⚠️ `TRUNCATED not in text` 这一半不能省：那句「截断了：通过 2 项，
       不通过 0 项，后面的没跑」**是能被 `SUMMARY` 正则匹配上的**，
       所以「找到了汇总行」在截断的那一种里也是 True。少了这一半，
       跑到一半锁屏会被记成「跑完了」—— 方向正好是更糟的那一边。
    """
    _ok, _bad, _skip_n, found = parse_summary(text)
    return found and TRUNCATED not in text


def classify(windowed, text, skip_n=0):
    """这个脚本是【怎么没跑完】的。空串 = 它跑完了。

    ⚠️ `windowed` 那个条件不能省：「锁屏」那句话只有四个开窗脚本会打，
      纯内存的脚本里出现同一句话只可能是别的原因（比如它自己引用了
      这段文案）。不加限定就会把「跑完了」错判成「锁屏」，方向是
      **把绿的报成没跑** —— 而反向的错（把没跑的报成绿）更糟，
      所以两种都不敢赌，这里只认确凿的那一种。

    ⚠️ `finished_normally()` 那个条件同样不能省，而且它是**两个条件里更硬的那个**：
       `windowed` 挡的是「别的类别的脚本引用了这句话」，
       它挡的是「**同一份脚本里、一条断言故意把锁屏这条路走了一遍**」。
       后者才是 2026-09-21 那次的实际形态（见 `finished_normally` 的说明）。
    """
    if windowed and LOCKED in text and not finished_normally(text):
        return "cut" if TRUNCATED in text else "locked"
    if NOCLIP in text:
        return "noclip"
    if skip_n:
        # 脚本自己说了「有 N 项没测」。它可能是任何原因（造不出环境、
        # 如实记没测……），本文件不去猜 —— 只把它从「通过」里摘出来。
        return "partial"
    return ""


def judge(ok, bad, expect):
    """→ (标记, 附注, 算不算问题)。

    ⚠️ 项数对账（`ok != expect`）是「脚本被改过却没同步这里的数字」
      和「脚本悄悄少跑了几项」共用的【唯一一道网】。
      它要是退化成恒不报，`全部回归.py` 就会把一份跑了一半的报告
      报成 ✅ —— 这一条没有别的机制能兜住。
    """
    problem = bad != 0 or ok != expect
    note = "" if ok == expect else f"　⚠ 期望 {expect} 项，实到 {ok} 项"
    return ("❌" if problem else "✅"), note, problem


def digest(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except FileNotFoundError:
        return None


def data_safety(kb_path, kb_before, settings_path, settings_before):
    """末尾两条数据安全网。→ (要打的行, problems)。

    ★ 路径是【参数】不是常量 —— `--selfcheck` 要拿临时文件试它。
      **绝不能**拿用户真的 `app\\settings.json` 去试「这张网响不响」：
      那张网存在的全部意义就是那个文件不能被动，为了验它去动它，
      正好是它防的那件事。

    ⚠️ 调用点必须在「没跑完就返回 1」【之前】。原来它在后面，
      于是锁屏跳过的那一轮**整个安全网一次都没跑**：
      返回 1、一行都没提 Key，而看报告的人会以为该核的都核了。
      **只在「一切顺利」时才跑的检查，等于在最需要它的那几轮缺席。**
    """
    lines, problems = [], []

    kb_after = os.path.exists(kb_path)
    if kb_after != kb_before:
        lines.append("⚠⚠ 真实知识库 app\\kb.db 的存在状态被改变了 —— "
                     "有脚本没走临时库！")
        lines.append(f"    跑之前：{'在' if kb_before else '不在'}　"
                     f"跑之后：{'在' if kb_after else '不在'}")
        problems.append("app\\kb.db 被动了")
    else:
        lines.append(f"✓ 真实知识库 app\\kb.db 未被动过"
                     f"（{'存在' if kb_before else '全程不存在'}）")

    settings_after = digest(settings_path)
    if settings_after != settings_before:
        lines.append("⚠⚠ 真实 settings.json 被改动了 —— "
                     "里面是明文 API Key，这条最不能红！")
        lines.append("    跑之前：" + ("（无此文件）" if settings_before is None
                                      else settings_before[:12]))
        lines.append("    跑之后：" + ("（文件没了）" if settings_after is None
                                      else settings_after[:12]))
        lines.append("    多半是哪个脚本没走 WENLEMA_SETTINGS，"
                     "退回去直接写 app\\settings.json 了。")
        lines.append("    若 Key 已被覆盖：从 `_e2e_settings.json` 认得出"
                     "假 Key 的样子（sk-test-fake），")
        lines.append("    真要补救得从历史会话记录里找 09-14 那份 "
                     "settings.json 全文。")
        problems.append("app\\settings.json 被动了")
    else:
        lines.append("✓ 真实 settings.json 未被动过（哈希一致，Key 没丢）")

    return lines, problems


def main(scripts=None, script_dir=None, logroot=None, app_dir=None,
         lock_probe=True):
    """跑一轮回归。

    ★ 这几个参数是给 `--selfcheck` 用的：它拿【仿造的脚本】和一个临时目录,
      把整个 `main()` 端到端驱动一遍，再断言打出来的那份报告。

      **为什么非要端到端**：上面那些纯函数验的是【零件】，而用户读到的
      是【那句话】。「✅ 全绿」是怎么从零件拼出来的，只有把整条链路
      跑一遍才验得到 —— 只验零件的话，`main()` 里少调用一次 `judge()`、
      或者把 `data_safety` 挪到 `return 1` 后面，零件再对也没人知道，
      而后者**真的发生过**（见下面数据安全那段的注释）。
    """
    scripts = SCRIPTS if scripts is None else scripts
    script_dir = HERE if script_dir is None else script_dir
    logroot = os.path.join(HERE, "_回归日志") if logroot is None else logroot
    app_dir = APP if app_dir is None else app_dir

    rundir = os.path.join(logroot, time.strftime("%m%d-%H%M%S"))
    os.makedirs(rundir, exist_ok=True)
    prune(logroot)

    print("=" * 62)
    print("问了吗 · 全部回归")
    print(f"日志：{rundir}")
    print("=" * 62)
    print("⚠ 跑的过程中别动鼠标 —— 四个端到端脚本要抢前台焦点和光标位置。")
    print("  （敲键盘没关系：v0.13 起自动通道不再看「系统空闲」，"
          "你动不动键鼠都不影响触发。）")

    # 开局先说一声：锁着的话后面四个开窗脚本会整段跳过。
    # 不说的话，用户要等纯内存那三个跑完（约 1 分钟）才发现，
    # 而那时候他多半已经走开了 —— 明早回来看见一屏 ⏸ 还得重新判断发生了什么。
    if lock_probe:
        try:
            if box_module().screen_locked():
                print("\n⏸ 屏幕现在是【锁着的】—— 四个开窗脚本会整段跳过（不算通过）。")
                print("   纯内存那三个照跑。要验开窗的那部分，解锁后重跑。")
        except Exception as e:
            print(f"（锁屏检测没跑起来：{e!r} —— 不影响后面）")

    # ⚠ 用户的真实知识库。回归全程不该碰它，跑完再确认一次。
    #   开窗口那几个脚本都会把 WENLEMA_KB 指到自己的临时库，
    #   但这是【约定】不是【强制】—— 万一哪个脚本漏了，这条能兜住。
    real_kb = os.path.join(app_dir, "kb.db")
    kb_before = os.path.exists(real_kb)

    # ⚠ 用户的真实 settings.json。里面是【明文 API Key】，比库还不能出事。
    #
    #   2026-09-15 就是在这里漏的：那天 287 项跑了个全绿，
    #   而同一次运行把用户的真 Key 覆盖成了 `sk-test-fake` ——
    #   这份报告当时还在下面打印「✓ 真实知识库未被被动过」，
    #   **一边报平安一边把东西弄丢了**。只核库不核 settings，
    #   等于把最该核的那份漏在外面。
    #
    #   现在四个开窗脚本一律走 WENLEMA_SETTINGS 指到临时文件，
    #   真文件理论上根本不会被打开；这条是【兜底】，
    #   不是它该起作用的时候起作用 —— 它红了就说明有人退回去了。
    real_settings = os.path.join(app_dir, "settings.json")
    settings_before = digest(real_settings)

    rows = []
    for name, expect, windowed in scripts:
        path = os.path.join(script_dir, name)
        log = os.path.join(rundir, os.path.splitext(name)[0] + ".log")
        print(f"\n▶ {name}（期望 {expect} 项{'，开窗口' if windowed else ''}）")

        t0 = time.time()
        # ⚠ -u：不加的话 stdout 重定向到文件时是块缓冲，
        #   跑到一半去看日志会一个字都没有，看着像卡死。
        with open(log, "wb") as fh:
            subprocess.call([sys.executable, "-u", path],
                            cwd=script_dir, stdout=fh,
                            stderr=subprocess.STDOUT)
        secs = time.time() - t0

        text = open(log, encoding="utf-8", errors="replace").read()
        ok, bad, skip_n, found = parse_summary(text)
        fails = parse_fails(text)
        skip = classify(windowed, text, skip_n)
        gated = GATE_MARK in text
        rows.append((name, ok, bad, expect, secs, log, fails, skip, skip_n, gated))

        if skip == "noclip":
            # ⚠ 和锁屏那两种【不是一个形态】，所以【不能】跟着 continue ——
            #   那会连带跳过下面那行汇总。它只少验了一组，
            #   前面几组（组合键互转、闸一判据、唯一实现处）是真跑过的，
            #   按「整段没跑」报等于让那几十项白跑一遍。
            #   真失败（bad>0）也照常报 —— 取词那组没跑，不代表别的组没红。
            print("  " + skip_note(name, ok, expect, secs, skip, skip_n))
        elif skip:
            print("  " + skip_note(name, ok, expect, secs, skip, skip_n))
            if skip == "cut":
                # 跑过的那半截有真明细（包括真失败），值得让人翻一眼
                print(f"    完整输出：{log}")
            continue

        print(f"  通过 {ok} / 不通过 {bad}　（{secs:.0f}s）"
              + ("" if found else "　⚠ 没找到汇总行，脚本可能没跑起来"))

        for f in fails:
            print(f"    [不通过] {f[:120]}")
        if not found or bad:
            print(f"    完整输出：{log}")

    # ── 汇总 ────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("汇总")
    print("=" * 62)
    total_ok = total_bad = 0
    problems = []
    skipped = []
    for name, ok, bad, expect, secs, log, fails, skip, skip_n, gated in rows:
        if skip:
            skipped.append(name)
            print("  " + skip_note(name, ok, expect, secs, skip, skip_n))
            continue
        total_ok += ok
        total_bad += max(bad, 0)
        mark, note, is_problem = judge(ok, bad, expect)
        if is_problem:
            problems.append(name)
        # ★ 闸门【跑过没有】，和闸门【说没说通过】是两件事。
        #   ⚠️ 只对真跑过的脚本要求（锁屏那种整段没跑的在上面 continue 掉了）。
        if not gated:
            is_problem = True
            problems.append(f"{name}（出口自检没跑）")
            note += "　⚠ 日志里没有出口自检那行 —— 这份的 check() 没被验过"
        print(f"  {mark} {name:<22} 通过 {ok:>3}　不通过 {max(bad,0)}"
              f"　出口自检{'✓' if gated else '✗'}{note}")
    print(f"\n  合计：通过 {total_ok} 项，不通过 {total_bad} 项")

    # ── 数据安全 ────────────────────────────────────────
    # ⚠ 这一段必须在下面那段「没跑完就返回 1」【之前】。
    #   原来它在后面，于是「锁屏跳过」的那一轮**整个安全网一次都没跑**：
    #   返回 1、一行都没提 Key，而看报告的人会以为该核的都核了。
    #   实测 2026-09-16 00:30 那轮：数据安全这一节一个字都没打出来。
    #   这是 v0.12.1 那条教训的同一形状，只是换了个触发方式 ——
    #   **只在「一切顺利」时才跑的检查，等于在最需要它的那几轮缺席。**
    #   ★ 判据提到 `data_safety()` 里了，这里只负责【调用它】——
    #     调用点摆在哪，是这一段唯一要说清的事。
    print("\n" + "=" * 62)
    lines, safety_problems = data_safety(
        real_kb, kb_before, real_settings, settings_before)
    for line in lines:
        print(line)
    problems.extend(safety_problems)

    if problems:
        print(f"\n❌ 有问题：{'、'.join(problems)}")
        print(f"   日志在：{rundir}")

    # ⚠ 锁屏【不能算通过】。跳过多少项就等于少验多少项 ——
    #   这里要是照样打「全绿」，那这份报告就是在撒谎，
    #   而比撒谎更糟的是：它把「没验过」和「验过了没问题」变成同一个样子。
    if skipped:
        print(f"\n⏸ 有 {len(skipped)} 个脚本没跑完：{'、'.join(skipped)}")
        print("   这不是通过 —— 没跑到的那部分今晚等于没验。")
        print("   锁屏那几种：解锁后重跑一次。")
        print("   取词那一组：随便复制一段文字，再重跑一次。")
        print(f"   日志在：{rundir}")

    # 两种都返回非零：❌ 是「坏了」，⏸ 是「没验」—— 都不是「通过」。
    if problems or skipped:
        return 1
    print("\n✅ 全绿。")
    return 0


# ══════════════════════════════════════════════════════════
# 反向对照
#
# ★ 2026-09-21 立的：用户拍板「先把『验的工具』验一遍，再动产品代码 ——
#   不然修完也没法证明修好了」。
#   本文件是**我在这个项目里每一句「全绿」的唯一来源**，而它自己
#   从没被测过。上面那些纯函数就是为这一段提出来的。
#
# ★ 特意【端到端】而不是只测零件：用户读到的是那句「✅ 全绿」，
#   零件对不代表拼出来的那句话对。真发生过的事就是拼错了 ——
#   2026-09-16 那轮，`data_safety` 被摆在 `return 1` 后面，
#   于是锁屏跳过的那一轮**整个安全网一次都没跑**，
#   而报告长得和「该核的都核了」一模一样。
#   那个 bug 只有把整条链路跑一遍才抓得到，零件全是好的。
# ══════════════════════════════════════════════════════════

# 每个仿造脚本都要带的一段。**真实脚本每一份开头都是这几行**，
# 假脚本不带的话就露馅了 —— 而且露得很安静：
#   子进程的 stdout 走本地编码（GBK），中文能编码出去；
#   但本文件回读时用的是 `encoding="utf-8"` → 一片替换字符 →
#   汇总行**一个字都认不出来** → 每个假脚本都被判成「没找到汇总行」。
#   ★ 我第一遍就是这么写的，22 条里错了 14 条，连「一路顺风」那组都红 ——
#     而根因不是判断逻辑，是夹具。**夹具错了会伪装成产品错了。**
# 仿造脚本的「前置段」拆成两半，是为了 ⑨ 那条用例：
# 它要试的是「闸门那行没有」—— 但编码那五行**不能一起去掉**，
# 去掉的话仿造脚本按 GBK 写出、汇总行一个字都认不出，
# 于是红的会是「没找到汇总行」而不是「闸门没跑」——
# **试错东西了，而屏幕上两条都写着「红了」。**
PRE_ENC = ("import sys\n"
           "for _s in (sys.stdout, sys.stderr):\n"
           "    try:\n"
           "        _s.reconfigure(encoding='utf-8', errors='replace')\n"
           "    except Exception:\n"
           "        pass\n")

# ★ 仿造脚本也要打那句闸门标记 —— 真的八份脚本每份都有。
#   ⚠️ 少这一行的话，「闸门没跑要报错」那条规则会把下面
#      **每一条**仿造用例都判红，包括「一路顺风」那组，
#      而红的原因跟被测的判断逻辑毫无关系 —— 又是夹具的错。
PRE = PRE_ENC + "print('  ✓ check() 自检通过（仿造脚本）')\n"


def _drive(tmp, tag, scripts, files, pre=None, prelude=None):
    """把整个 `main()` 拿【仿造脚本】跑一遍，返回 (打出来的字, 退出码)。

    files: {脚本名: 源码}；源码里的 `@APP@` 会换成这次用的临时 app 目录 ——
           假脚本靠它去动「真实 settings.json」的替身（见下面第 ⑦⑧ 组）。
    pre:   {文件名: 内容}，在 `main()` **开跑之前**就摆进 app 目录。
           ⚠️ 数据安全网比的是【开跑前】和【跑完之后】——
              要试「这东西被动了没有」，它就得先存在；
              夹具在跑的过程中才把它建出来，网看到的是「本来不在、后来在了」，
              那是另一回事（而且会被正确地报成「被动了」）。
    ⚠️ 全程只在临时目录里，**碰不到任何真东西**。
    """
    root = os.path.join(tmp, tag)
    sdir = os.path.join(root, "s")
    logroot = os.path.join(root, "logs")
    app = os.path.join(root, "app")
    for d in (sdir, logroot, app):
        os.makedirs(d, exist_ok=True)
    for name, src in files.items():
        with open(os.path.join(sdir, name), "w", encoding="utf-8") as f:
            f.write((PRE if prelude is None else prelude)
                    + src.replace("@APP@", app))
    for fname, body in (pre or {}).items():
        with open(os.path.join(app, fname), "w", encoding="utf-8") as f:
            f.write(body)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(scripts=scripts, script_dir=sdir, logroot=logroot,
                  app_dir=app, lock_probe=False)
    return buf.getvalue(), rc


def selfcheck():
    """把「已知的坏输入」喂进整个回归流程，看它报不报。"""
    import tempfile

    _n = [0, 0]

    def want(label, cond, extra=""):
        _n[0] += 1
        _n[1] += 0 if cond else 1
        print(f"  {'✓' if cond else '✗'} {label}" + (f"　{extra}" if extra else ""))
        return cond

    def green(n, wording="，不通过 0 项"):
        return f"print('通过 {n} 项{wording}')\n"

    tmp = tempfile.mkdtemp(prefix="wlm_regchk_")
    try:
        # ① 一路顺风 —— 必须真的说「全绿」，否则后面每一条都没意义
        out, rc = _drive(tmp, "ok", [("甲.py", 3, False)],
                         {"甲.py": green(3)})
        want("① 全绿：说「✅ 全绿。」", "✅ 全绿。" in out)
        want("① 全绿：退出码 0", rc == 0, f"rc={rc}")

        # ② 项数对账 —— 「脚本悄悄少跑了几项」的唯一一道网
        out, rc = _drive(tmp, "count", [("乙.py", 3, False)],
                         {"乙.py": green(2)})
        want("② 项数对账：少一项要 ❌", "❌" in out and "期望 3 项，实到 2 项" in out)
        want("② 项数对账：不许说「全绿」", "✅ 全绿。" not in out)
        want("② 项数对账：退出码非 0", rc != 0, f"rc={rc}")

        # ③ 三种汇总行措辞 —— 都是真跑出来过的原文（见 SUMMARY 那段）
        out, rc = _drive(tmp, "words", [("甲.py", 1, False),
                                        ("乙.py", 2, False),
                                        ("丙.py", 3, False)],
                         {"甲.py": green(1, "　失败 0 项"),
                          "乙.py": green(2),
                          "丙.py": green(3, "、不通过 0 项、没测 0 项")})
        want("③ 措辞：三种都认得出（合计 1+2+3=6）",
             "合计：通过 6 项，不通过 0 项" in out and out.count("✅") >= 3)
        want("③ 措辞：退出码 0", rc == 0, f"rc={rc}")

        # ④ ★ 脚本自己报的「没测 N」必须进报告 —— 2026-09-21 之前
        #    这个数字一个字节都没进过报告，只靠项数对账间接兜住
        out, rc = _drive(tmp, "untested", [("丁.py", 3, False)],
                         {"丁.py": green(2, "、不通过 0 项、没测 1 项")})
        want("④ 没测：报告里要出现「有 1 项没测」", "有 1 项没测" in out)
        want("④ 没测：不许说「全绿」", "✅ 全绿。" not in out)
        want("④ 没测：退出码非 0（⏸ 不是通过）", rc != 0, f"rc={rc}")

        # ⑤ 失败明细 —— 八份脚本用了【四种】前缀，三种失败写法都要认得出来
        #    （2026-09-21 审计发现：原来只认 `[不通过]`，于是知识库联调 /
        #      快捷键联调 / AI接入联调 这三份的明细一条都进不了报告）
        for tag, line in [("不通过", "  [不通过] 真的红了　说明文字"),
                          ("失败", "  [失败] 也真的红了　→ 说明文字"),
                          ("!!", "  [!!] 还是真的红了　→ 说明文字")]:
            out, rc = _drive(tmp, "detail_" + tag,
                             [("戊.py", 3, False)],
                             {"戊.py": "print('  [通过] 这条不该被当成明细')\n"
                                       "print('  [OK] 这条也不该')\n"
                                       f"print({line!r})\n" + green(2)})
            want(f"⑤ 明细：`[{tag}]` 前缀要认得出来", "真的红了" in out)
            want(f"⑤ 明细：`[{tag}]` 时通过那行不许混进来",
                 "这条不该被当成明细" not in out
                 and "这条也不该" not in out)
            want(f"⑤ 明细：`[{tag}]` 时退出码非 0", rc != 0, f"rc={rc}")

        # ⑥ 没跑完的三种形态 —— 措辞必须分开，混成一句就是撒谎
        #
        # ⚠️ 这个夹具【故意不打汇总行】，而且这是它跟真实行为对齐的关键：
        #    四个开窗脚本开局锁屏时都是 `require_clean_screen()` 返回 False
        #    之后【立刻 return，一行汇总都不打】——
        #    逐个核过：框选联调.py:1176 / 知识库端到端联调.py:749 /
        #    AI端到端联调.py:154 / 自动通道端到端联调.py:501，全是 `return 1`。
        #    原来这里写的是 `LOCK + green(0)`，等于给一份「锁屏退出」的运行
        #    配了一行「通过 0 项」——**它模仿的系统不那样运行**。
        #    2026-09-21 修 `finished_normally()` 时这两条当场红了，
        #    红得对：该改的是夹具，不是判据（断言一个字没动）。
        LOCK = "print('屏幕现在是【锁着的】')\n"
        out, _ = _drive(tmp, "lock", [("己.py", 5, True)],
                        {"己.py": LOCK})
        want("⑥ 开局锁屏（无汇总行）→「锁屏，整段没跑」", "锁屏，整段没跑" in out)
        want("⑥ 开局锁屏 → 不许说「全绿」", "✅ 全绿。" not in out)

        out, _ = _drive(tmp, "cut", [("庚.py", 5, True)],
                        {"庚.py": LOCK + "print('截断了：通过 2 项，不通过 0 项，"
                                         "后面的没跑')\n"})
        want("⑥ 跑到一半锁 →「跑到一半锁了：前 2/5 项跑过」",
             "跑到一半锁了：前 2/5 项跑过" in out)

        # ★ 不加 `windowed` 限定的话，这句话会把「跑完了」错判成「锁屏」
        out, rc = _drive(tmp, "fakelock", [("辛.py", 1, False)],
                         {"辛.py": "print('（这行只是在引用那句文案："
                                   "屏幕现在是【锁着的】）')\n" + green(1)})
        want("⑥ 纯内存脚本里出现同一句话 → 不许判成锁屏",
             "锁屏，整段没跑" not in out and rc == 0, f"rc={rc}")

        # ★★ 开窗脚本【跑完了】，但日志里出现了那句话 —— 2026-09-21 真踩到的一次。
        #    背景：`框选联调.py` 为「三条从没红过的判据」补了一条
        #    `t_lock_signal_unit()`，它故意把 `fg_exe` 换成桩，
        #    好让 `require_unlocked()` 真的返回一次 False ——
        #    于是那句警告**被真真切切打进了日志**。
        #    当时回归把一份 **38 项全绿、五节都跑完**的报告记成
        #    「锁屏，整段没跑」，还把它排除在合计之外：报出来的总数
        #    比真跑过的少 38。**这不是错报，是漏报** —— 而漏报特别难发现，
        #    因为它长得就像「这一份今天没轮上跑」。
        #    ⚠️ 上面那条 ⑥ 挡不住它：那条试的是【别的类别的脚本】引用这句话，
        #       这条试的是【同一份开窗脚本里】一条断言走了锁屏那条路。
        out, rc = _drive(tmp, "fakelock_done", [("壬.py", 5, True)],
                         {"壬.py": LOCK + green(5)})
        want("⑥ 开窗脚本【跑完了】但日志里有那句话 → 不许判成锁屏",
             "锁屏，整段没跑" not in out and "跑到一半锁了" not in out, f"out={out[-120:]!r}")
        want("⑥ …而且照常算通过（退出码 0）", rc == 0, f"rc={rc}")

        # ⑦ 两条数据安全网 —— 2026-09-15 就是在这一处一边报 ✓ 一边弄丢 Key
        #    两个替身【开跑前就摆好】，假脚本再去动它们。
        PRE_APP = {"kb.db": "假的库", "settings.json": '{"api_key":"REAL"}'}
        MUT = ("import os\n"
               "p = os.path.join(r'@APP@', 'settings.json')\n"
               "k = os.path.join(r'@APP@', 'kb.db')\n")

        out, rc = _drive(tmp, "safe_ok", [("壬.py", 1, False)],
                         {"壬.py": green(1)}, pre=PRE_APP)
        want("⑦ 都没动 → 两条 ✓", "✓ 真实知识库" in out
             and "✓ 真实 settings.json 未被动过" in out and rc == 0, f"rc={rc}")

        out, rc = _drive(tmp, "safe_kb", [("癸.py", 1, False)],
                         {"癸.py": MUT + green(1) + "os.remove(k)\n"},
                         pre=PRE_APP)
        want("⑦ 知识库被删 → ⚠⚠ + 算问题", "⚠⚠ 真实知识库" in out
             and "app\\kb.db 被动了" in out, f"rc={rc}")

        out, rc = _drive(tmp, "safe_set", [("子.py", 1, False)],
                         {"子.py": MUT + green(1)
                          + "open(p,'w').write('sk-test-fake')\n"},
                         pre=PRE_APP)
        want("⑦ settings 被改 → ⚠⚠ + 算问题",
             "⚠⚠ 真实 settings.json 被改动了" in out
             and "app\\settings.json 被动了" in out, f"rc={rc}")

        out, rc = _drive(tmp, "safe_gone", [("丑.py", 1, False)],
                         {"丑.py": MUT + green(1) + "os.remove(p)\n"},
                         pre=PRE_APP)
        want("⑦ settings 没了 → ⚠⚠ 且认出「文件没了」",
             "⚠⚠ 真实 settings.json 被改动了" in out
             and "（文件没了）" in out, f"rc={rc}")

        # ⑧ ★★ 2026-09-16 那个真 bug：锁屏跳过的那一轮，
        #    安全网【整段一次都没跑】—— 因为它被摆在 `return 1` 后面。
        #    报告长得和「该核的都核了」一模一样。
        #    这一条就是钉调用点的：谁再把 `data_safety` 往下挪，它就红。
        #
        # ⚠️ 这里的夹具同样【不打汇总行】—— 理由和第 ⑥ 组那两条一样：
        #    原来的 `+ green(0)` 让这份假脚本「先跑完、再宣布自己锁屏了」，
        #    而那正是 2026-09-21 那个缺陷的形状，不是真实脚本的行为。
        #    去掉之后它才真是一份「开局锁屏、整段没跑」的运行 ——
        #    也就是这条断言本来要试的那个东西。
        out, rc = _drive(tmp, "safe_when_skipped", [("寅.py", 5, True)],
                         {"寅.py": MUT + LOCK
                          + "open(p,'w').write('sk-test-fake')\n"},
                         pre=PRE_APP)
        want("⑧ ★ 没跑完的那一轮，安全网【照样要跑】",
             "锁屏，整段没跑" in out
             and "⚠⚠ 真实 settings.json 被改动了" in out, f"rc={rc}")

        # ⑨ ★ 出口自检那道闸门【跑过没有】。
        #    2026-09-21 给八份脚本都配了闸门 —— 但「闸门写了却没被调用」
        #    和「闸门跑了而且通过」在日志上长得一模一样。
        #    ⚠️ 这条必须配一个「没有那行就红」的反向用例，
        #      否则它只是个装饰：一份**没打那行**的日志照样能全绿。
        out, rc = _drive(tmp, "gate_on", [("卯.py", 3, False)],
                         {"卯.py": green(3)})       # PRE 里有那行
        want("⑨ 闸门跑了 → 该全绿", "✅ 全绿。" in out and rc == 0, f"rc={rc}")

        out, rc = _drive(tmp, "gate_off", [("卯.py", 3, False)],
                         {"卯.py": green(3)}, prelude=PRE_ENC)
        want("⑨ ★ 闸门【没跑】→ 必须报出来，不许全绿",
             "✅ 全绿。" not in out and "出口自检没跑" in out
             and "出口自检✗" in out, f"rc={rc}")
        want("⑨ ★ 闸门没跑 → 退出码非 0", rc != 0, f"rc={rc}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    if _n[1]:
        print(f"❌ {_n[1]}/{_n[0]} 条不符 —— **回归本身有问题**，先修它。")
        print("   在那之前，它报的「全绿」不作数。")
    else:
        print(f"✅ {_n[0]}/{_n[0]} 条全对：三种措辞、项数对账、跳过三分类、"
              "明细行、两条安全网、")
        print("   「没跑完那一轮安全网照样跑」、「闸门没跑要报出来」"
              "—— 都验过了。")
    return 1 if _n[1] else 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        sys.exit(selfcheck())
    sys.exit(main())
