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
import hashlib
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
    ("知识库端到端联调.py", 69, True),
    ("AI端到端联调.py",     32, True),
    ("框选联调.py",         24, True),
    # 排最后：它自己会起一个「示例笔记」陪练窗口，并改写联调那份 settings
    # （`_e2e_settings.json`，不是用户真的那份）。放在其他开窗脚本前面的话，
    # 后面那些脚本会吃到它留下的区域设置。
    ("自动通道端到端联调.py", 18, True),
]

# 各脚本的汇总行措辞不统一（「失败」/「不通过」两种），一起认
SUMMARY = re.compile(r"通过\s*(\d+)\s*项.*?(?:不通过|失败)\s*(\d+)\s*项")
# 明细行：框选联调.py 的 check() 打的格式
DETAIL = re.compile(r"^\s*\[不通过\]\s*(.*)$")

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


def skip_note(name, ok, expect, secs, skip):
    """一个「没跑完」的脚本该怎么说。跑的一开始和汇总各用一次。

    ⚠ 三种情况【都】不算通过，但跑过多少不一样，措辞必须分开：
      · "locked" —— 开局就锁着，一项都没验
      · "cut"    —— 跑到一半才锁，前面那些项是【真跑过的】，只是整份不算数
      · "noclip" —— 只有取词那一组没跑，别的组是【真跑过的】
    写成同一句话就是在撒谎，而撒谎的方向正好相反（见 TRUNCATED 那段）。
    """
    if skip == "cut":
        return (f"⏸ {name:<22} 跑到一半锁了：前 {ok}/{expect} 项跑过，"
                f"后面的没跑　（{secs:.0f}s）")
    if skip == "noclip":
        return (f"⏸ {name:<22} 取词那组没跑（剪贴板里不是纯文字）："
                f"别的组跑过 {ok} 项　（{secs:.0f}s）")
    return f"⏸ {name:<22} 锁屏，整段没跑　（{secs:.0f}s）"


def main():
    logroot = os.path.join(HERE, "_回归日志")
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
    try:
        if box_module().screen_locked():
            print("\n⏸ 屏幕现在是【锁着的】—— 四个开窗脚本会整段跳过（不算通过）。")
            print("   纯内存那三个照跑。要验开窗的那部分，解锁后重跑。")
    except Exception as e:
        print(f"（锁屏检测没跑起来：{e!r} —— 不影响后面）")

    # ⚠ 用户的真实知识库。回归全程不该碰它，跑完再确认一次。
    #   开窗口那几个脚本都会把 WENLEMA_KB 指到自己的临时库，
    #   但这是【约定】不是【强制】—— 万一哪个脚本漏了，这条能兜住。
    real_kb = os.path.join(APP, "kb.db")
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
    real_settings = os.path.join(APP, "settings.json")

    def digest(p):
        try:
            return hashlib.sha256(open(p, "rb").read()).hexdigest()
        except FileNotFoundError:
            return None

    settings_before = digest(real_settings)

    rows = []
    for name, expect, windowed in SCRIPTS:
        path = os.path.join(HERE, name)
        log = os.path.join(rundir, os.path.splitext(name)[0] + ".log")
        print(f"\n▶ {name}（期望 {expect} 项{'，开窗口' if windowed else ''}）")

        t0 = time.time()
        # ⚠ -u：不加的话 stdout 重定向到文件时是块缓冲，
        #   跑到一半去看日志会一个字都没有，看着像卡死。
        with open(log, "wb") as fh:
            rc = subprocess.call([sys.executable, "-u", path],
                                 cwd=HERE, stdout=fh, stderr=subprocess.STDOUT)
        secs = time.time() - t0

        text = open(log, encoding="utf-8", errors="replace").read()
        m = None
        for line in text.splitlines():
            m = SUMMARY.search(line) or m
        ok, bad = (int(m.group(1)), int(m.group(2))) if m else (0, -1)

        fails = [DETAIL.match(l).group(1).strip()
                 for l in text.splitlines() if DETAIL.match(l)]

        # 没跑完的两种形态，判据都在脚本自己打的那句话上（本地不另立一份）。
        # ⚠ "cut" 只可能在 LOCKED 成立时出现 —— 四个开窗脚本里 `interrupted`
        #   唯一的置位处就是 `except ScreenLocked`，而它紧跟着调 `lock_bail()`。
        skip = ""
        if windowed and LOCKED in text:
            skip = "cut" if TRUNCATED in text else "locked"
        elif NOCLIP in text:
            skip = "noclip"
        rows.append((name, ok, bad, expect, secs, log, fails, skip))

        if skip == "noclip":
            # ⚠ 和锁屏那两种【不是一个形态】，所以【不能】跟着 continue ——
            #   那会连带跳过下面那行汇总。它只少验了一组，
            #   前面几组（组合键互转、闸一判据、唯一实现处）是真跑过的，
            #   按「整段没跑」报等于让那几十项白跑一遍。
            #   真失败（bad>0）也照常报 —— 取词那组没跑，不代表别的组没红。
            print("  " + skip_note(name, ok, expect, secs, skip))
        elif skip:
            print("  " + skip_note(name, ok, expect, secs, skip))
            if skip == "cut":
                # 跑过的那半截有真明细（包括真失败），值得让人翻一眼
                print(f"    完整输出：{log}")
            continue

        print(f"  通过 {ok} / 不通过 {bad}　（{secs:.0f}s）"
              + ("" if m else "　⚠ 没找到汇总行，脚本可能没跑起来"))

        for f in fails:
            print(f"    [不通过] {f[:120]}")
        if not m or bad:
            print(f"    完整输出：{log}")

    # ── 汇总 ────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("汇总")
    print("=" * 62)
    total_ok = total_bad = 0
    problems = []
    skipped = []
    for name, ok, bad, expect, secs, log, fails, skip in rows:
        if skip:
            skipped.append(name)
            print("  " + skip_note(name, ok, expect, secs, skip))
            continue
        total_ok += ok
        total_bad += max(bad, 0)
        mark = "✅"
        if bad != 0 or ok != expect:
            mark = "❌"
            problems.append(name)
        # 项数对不上期望，说明脚本被改过却没同步这里的数字（或没跑完）
        note = "" if ok == expect else f"　⚠ 期望 {expect} 项，实到 {ok} 项"
        print(f"  {mark} {name:<22} 通过 {ok:>3}　不通过 {max(bad,0)}{note}")
    print(f"\n  合计：通过 {total_ok} 项，不通过 {total_bad} 项")

    # ── 数据安全 ────────────────────────────────────────
    # ⚠ 这一段必须在下面那段「没跑完就返回 1」【之前】。
    #   原来它在后面，于是「锁屏跳过」的那一轮**整个安全网一次都没跑**：
    #   返回 1、一行都没提 Key，而看报告的人会以为该核的都核了。
    #   实测 2026-09-16 00:30 那轮：数据安全这一节一个字都没打出来。
    #   这是 v0.12.1 那条教训的同一形状，只是换了个触发方式 ——
    #   **只在「一切顺利」时才跑的检查，等于在最需要它的那几轮缺席。**
    print("\n" + "=" * 62)
    kb_after = os.path.exists(real_kb)
    if kb_after != kb_before:
        print("⚠⚠ 真实知识库 app\\kb.db 的存在状态被改变了 —— 有脚本没走临时库！")
        print(f"    跑之前：{'在' if kb_before else '不在'}　"
              f"跑之后：{'在' if kb_after else '不在'}")
        problems.append("app\\kb.db 被动了")
    else:
        print(f"✓ 真实知识库 app\\kb.db 未被动过（{'存在' if kb_before else '全程不存在'}）")

    settings_after = digest(real_settings)
    if settings_after != settings_before:
        print("⚠⚠ 真实 settings.json 被改动了 —— 里面是明文 API Key，这条最不能红！")
        print(f"    跑之前：{'（无此文件）' if settings_before is None else settings_before[:12]}")
        print(f"    跑之后：{'（文件没了）' if settings_after is None else settings_after[:12]}")
        print("    多半是哪个脚本没走 WENLEMA_SETTINGS，退回去直接写 app\\settings.json 了。")
        print("    若 Key 已被覆盖：从 `_e2e_settings.json` 认得出假 Key 的样子（sk-test-fake），")
        print("    真要补救得从历史会话记录里找 09-14 那份 settings.json 全文。")
        problems.append("app\\settings.json 被动了")
    else:
        print("✓ 真实 settings.json 未被动过（哈希一致，Key 没丢）")

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


if __name__ == "__main__":
    sys.exit(main())
