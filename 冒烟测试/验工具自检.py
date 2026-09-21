# -*- coding: utf-8 -*-
"""验「验的工具」。

    python "Desktop\\问了吗\\冒烟测试\\验工具自检.py"

────────────────────────────────────────────────────────
为什么要有这个文件（2026-09-21 用户拍板的工作顺序）：

> 先把「验的工具」验一遍，再动产品代码 —— 不然修完也没法证明修好了

这是本项目那条老规矩（**「新加一条断言跑绿不算数 —— 给它配的变体不红，
它就是装饰品」**）**往外推了一层**：从「断言」推到**断言赖以成立的整套工具**。

推出去之后撞见的头一件事，就是 `check()` 自己：

- 它是**全部 400 项断言共用的唯一出口**
- 别的判据坏掉，红的是它自己那几条；`check()` 坏掉，是 **400 项一起变绿**
- 而且**「项数对账」也拦不住** —— `全部回归.py` 汇总行里那个
  「通过 N 项」的 N，就是从 `check()` 数的那个 `_ok` 里来的
  ★ 它同时污染**判据**和**对判据的对账**，是这份报告里唯一一处
  「没有任何别的机制能兜住」的地方

所以 `框选联调.py` 里加了 `_selftest_check()` + 闸门 `gate_check_selftest()`。
**而这个文件是给那道自检配的反向对照** —— 不配的话，
「自检通过了」和「自检根本不会报错」在屏幕上长得一模一样。

────────────────────────────────────────────────────────
两个方向都要验（少一个这个文件自己就成了装饰品）：

  · **负向**：把 `check()` 按已知的坏法改掉，自检**必须**报出来
  · **正向**：`check()` 没被改坏时，自检**必须**一声不吭
    ⚠️ 少了这一条，一个「永远报错」的自检会拿满分 ——
       那不是判据，是噪音

每一组还多验一步：**闸门本身要拦住**（`gate_check_selftest()` 返回 False）。
自检报出来了、闸门却没拦，等于报了个寂寞。

────────────────────────────────────────────────────────
⚠️ 这个文件【不并进 `全部回归.py` 的 SCRIPTS 表】——
   它验的是测试工具自己，跟产品行为无关。混进「通过 N 项」那个数里，
   会让那个数同时表示两件事（同 `扫描detail文案.py` 的处理）。
   它的调用方式是人手动跑，或者改完工具之后跑一次。
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── check() 的六种坏法 ──────────────────────────────────
# 每一条都是「我自己真会写出来」的那种，不是凑数的。
# ★ 判据：这条坏法要是放过去了，`check()` 就【安静地】不再具备那个能力。
def broken_ok_always(mod):
    """① 无条件记通过 —— 最要命的一种，400 项会一起变绿。"""
    def f(name, cond, detail="", hint=""):
        mod._ok += 1
        print(f"  [通过] {name}" + (f"　{detail}" if detail else ""))
        return cond
    return f


def broken_no_count(mod):
    """② 计数器压根不动 —— 不打汇总行？不，是打了也全是 0。"""
    def f(name, cond, detail="", hint=""):
        print(f"  [{'通过' if cond else '不通过'}] {name}")
        return cond
    return f


def broken_return_true(mod):
    """③ 返回值恒真 —— 调用方那些 `if not check(...): return` 全部失效。"""
    def f(name, cond, detail="", hint=""):
        if cond:
            mod._ok += 1
        else:
            mod._fail += 1
        print(f"  [{'通过' if cond else '不通过'}] {name}")
        return True
    return f


def broken_prefix(mod):
    """④ 失败行换了前缀 —— `全部回归.py` 拿 `[不通过]` 挑明细，一改就静默丢。"""
    def f(name, cond, detail="", hint=""):
        if cond:
            mod._ok += 1
            print(f"  [通过] {name}")
        else:
            mod._fail += 1
            print(f"  [FAIL] {name}　{detail}")
        return cond
    return f


def broken_hint_always(mod):
    """⑤ hint 在通过那一行也打 —— 就是「通过 + 一句说它没做到的话」。"""
    def f(name, cond, detail="", hint=""):
        if cond:
            mod._ok += 1
            print(f"  [通过] {name}" + (f"　— {hint}" if hint else ""))
        else:
            mod._fail += 1
            print(f"  [不通过] {name}　{detail}")
        return cond
    return f


def broken_detail_on_fail(mod):
    """⑥ detail 只在失败时打 —— 中性观察退化成「错了才说」。"""
    def f(name, cond, detail="", hint=""):
        if cond:
            mod._ok += 1
            print(f"  [通过] {name}")
        else:
            mod._fail += 1
            print(f"  [不通过] {name}　{detail}")
        return cond
    return f


BROKEN = [
    ("① 无条件记通过",        broken_ok_always),
    ("② 计数器不动",          broken_no_count),
    ("③ 返回值恒真",          broken_return_true),
    ("④ 失败行换了前缀",      broken_prefix),
    ("⑤ hint 通过时也打",     broken_hint_always),
    ("⑥ detail 只在失败时打", broken_detail_on_fail),
]


def probe_group(box, label, patch, want_caught):
    """把 check() 换成 patch，看自检和闸门各是什么反应。"""
    saved = box.check
    box.check = patch(box)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            bad = box._selftest_check()
            gate = box.gate_check_selftest()
    finally:
        box.check = saved
    # 闸门的返回值必须和自检的结论一致 —— 自检报了、闸门没拦，等于报了个寂寞
    caught = bool(bad) and (gate is False)
    ok = caught == want_caught
    print(f"  {'✓' if ok else '✗'} {label:<22} → "
          f"报出 {len(bad)} 条，闸门 pass={gate}"
          + ("" if ok else f"　⚠ 期望「{'报' if want_caught else '放行'}」"))
    if want_caught and not bad:
        print("      ⚠ 这条坏法【整套自检一个字都没说】—— 它是装饰品。")
    if not want_caught and bad:
        print(f"      ⚠ 没改坏却报错，第一条：{bad[0]}")
    return ok


# ══════════════════════════════════════════════════════════
# B 组 · `全部回归.py` 的反向对照
#
# 它是我在这个项目里【每一句「全绿」的唯一来源】，而它自己从没被测过。
# 2026-09-21 给它补了 `--selfcheck`（28 条）—— 那这 28 条自己能不能红？
# **配了自检却不给自检配变体，那 28 条就是 28 个装饰品。**
# ══════════════════════════════════════════════════════════

def _run_reg_selfcheck(mod):
    """跑一遍 `全部回归.selfcheck()`，返回 (红了几条, 一共几条)。

    ⚠️ 结尾那两句话的**分子不是一个意思**，别一起解析：
        `✅ n/n 条全对`   —— 分子是【总数】，红的当然没写出来
        `❌ k/n 条不符`   —— 分子是【红了几条】
    ★ 我第一版就是拿同一个正则抠两个数，于是「全绿」被读成
      「红了 22 条」，正向对照当场就红了 —— 而那不是自检坏了，
      是**我这个读它的人坏了**。
      「读测试结果的那段代码自己没被测过」正是这整个文件要治的病，
      所以这里单独把两种格式分开认。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.selfcheck()
    m = re.search(r"([❌✅]) (\d+)/(\d+)", buf.getvalue())
    if not m:
        return -1, -1                     # 压根没报数 —— 本身就是缺陷
    mark, a, b = m.group(1), int(m.group(2)), int(m.group(3))
    n_bad, n_all = (a if mark == "❌" else 0), b
    # 退出码和它自己报的数要一致 —— 报「❌ 3 条」却返回 0 也是一种撒谎
    if (n_bad > 0) != bool(rc):
        return None, n_all
    return n_bad, n_all


# ── 五种坏法（都是这份文件真会犯的错）────────────────────
def _b_judge_never_reports(mod):
    """项数对账退化成恒不报 —— 「悄悄少跑几项」就再也没人管了。"""
    mod.judge = lambda ok, bad, expect: ("✅", "", False)


def _b_safety_silent(mod):
    """安全网对 settings 的变化不吭声 —— 正是 2026-09-15 那一幕。"""
    mod.data_safety = lambda *a, **k: (["✓ 什么都没动"], [])


def _b_classify_no_guard(mod):
    """拿掉 `windowed` 限定 —— 纯内存脚本里出现那句话会被判成「锁屏」。"""
    def f(windowed, text, skip_n=0):
        if mod.LOCKED in text:
            return "cut" if mod.TRUNCATED in text else "locked"
        if mod.NOCLIP in text:
            return "noclip"
        if skip_n:
            return "partial"
        return ""
    mod.classify = f


def _b_summary_no_untested(mod):
    """不认「没测 N」—— 回到 09-21 之前：那个数字一个字节都进不了报告。"""
    def f(text):
        m = None
        for line in text.splitlines():
            m = mod.SUMMARY.search(line) or m
        if not m:
            return 0, -1, 0, False
        return int(m.group(1)), int(m.group(2)), 0, True
    mod.parse_summary = f


def _b_detail_one_prefix(mod):
    """明细行只认 `[不通过]` —— 回到 09-21 之前：另外三份的明细一条都进不了报告。"""
    mod.DETAIL = re.compile(r"^\s*\[不通过\]\s*(.*)$")


def _b_safety_after_verdict(tmp):
    """★ 源码手术：把 `data_safety` 的调用点挪到 `return 1` 【之后】。

    这就是 2026-09-16 那个真 bug 的原样 —— 锁屏跳过的那一轮，
    整个安全网一次都没跑，而报告长得和「该核的都核了」一模一样。
    ⚠️ 这一条【monkeypatch 做不到】—— 坏的不是某个函数，是**调用点的位置**。
      所以只能改源码，改的是一份临时副本，仓库里那份一个字不动。
    """
    src = open(os.path.join(HERE, "全部回归.py"), encoding="utf-8").read()
    CALL = ("    lines, safety_problems = data_safety(\n"
            "        real_kb, kb_before, real_settings, settings_before)\n"
            "    for line in lines:\n"
            "        print(line)\n"
            "    problems.extend(safety_problems)\n")
    VERDICT = ("    if problems or skipped:\n"
               "        return 1\n"
               "    print(\"\\n✅ 全绿。\")\n")
    if CALL not in src or VERDICT not in src:
        return None                       # 源码结构变了 —— 让它报错，别静默放行
    old = src.replace(CALL, "", 1)
    new = old.replace(VERDICT,
                      "    if problems or skipped:\n"
                      "        return 1\n"
                      + CALL +
                      "    if problems:\n"
                      "        return 1\n", 1)
    path = os.path.join(tmp, "全部回归_旧排法.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return load("regression_oldsite", path)


def probe_reg(load_fn, label, want_red=True):
    """load_fn 返回一个模块（或 None = 造不出来）。跑它的 selfcheck，看红不红。"""
    tmp = tempfile.mkdtemp(prefix="wlm_variant_")
    try:
        mod = load_fn(tmp)
        if mod is None:
            print(f"  ✗ {label:<28} → 造不出这个变体（源码结构变了？）")
            return False
        n_bad, n_all = _run_reg_selfcheck(mod)
        if n_bad is None:
            print(f"  ✗ {label:<28} → 报的数和退出码对不上，这本身是个缺陷")
            return False
        red = n_bad > 0
        ok = red == want_red
        print(f"  {'✓' if ok else '✗'} {label:<28} → "
              f"红了 {n_bad}/{n_all} 条"
              + ("" if ok else f"　⚠ 期望「{'红' if want_red else '全绿'}」"))
        if want_red and not red:
            print("      ⚠ 这条坏法【自检一个字都没说】—— 那 28 条里没人防它。")
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("验工具自检")
    print("=" * 62)
    bad = 0

    # ── A 组 · `check()` ──────────────────────────────────
    print("\nA 组 · `check()`（全部断言的唯一出口）")
    box = load("boxlink_verify", "框选联调.py")

    print("\n  正向对照（`check()` 没被改坏 —— 自检必须【一声不吭】）")
    # ★ 少了这一条，一个「永远报错」的自检会拿满分。那不是判据，是噪音。
    if not probe_group(box, "原样的 check()", lambda m: m.check, False):
        bad += 1

    print("\n  反向对照（每条坏法都必须被自检抓到，且被闸门拦住）")
    for label, patch in BROKEN:
        if not probe_group(box, label, patch, True):
            bad += 1
    n_a = len(BROKEN) + 1

    # ── B 组 · `全部回归.py` ──────────────────────────────
    print("\nB 组 · `全部回归.py`（每一句「全绿」的唯一来源）")

    def fresh(name="regression_verify"):
        return lambda tmp: load(name + "_" + os.urandom(3).hex(),
                                "全部回归.py")

    print("\n  正向对照（没被改坏 —— 那 28 条必须【全绿】）")
    # ★ 同上：一个永远报错的自检同样是装饰品，只是方向相反。
    if not probe_reg(fresh(), "原样的判断逻辑", want_red=False):
        bad += 1

    print("\n  反向对照（每种坏法都必须让那 28 条红起来）")
    for label, patch in [
        ("`judge()` 恒不报问题", _b_judge_never_reports),
        ("`data_safety()` 不吭声", _b_safety_silent),
        ("`classify()` 少了 windowed", _b_classify_no_guard),
        ("`parse_summary()` 不认没测", _b_summary_no_untested),
        ("`DETAIL` 只认一种前缀", _b_detail_one_prefix),
    ]:
        def fn(tmp, patch=patch):
            m = load("regression_" + os.urandom(3).hex(), "全部回归.py")
            patch(m)
            return m
        if not probe_reg(fn, label, True):
            bad += 1

    # ★ 最后这条是【调用点的位置】错了，不是某个函数错了 —— monkeypatch 够不着
    if not probe_reg(_b_safety_after_verdict,
                     "★ 安全网挪到 return 1 之后", True):
        bad += 1
    n_b = 1 + 5 + 1

    print("\n" + "=" * 62)
    n = n_a + n_b
    if bad:
        print(f"❌ {bad}/{n} 组不符 —— **自检或闸门本身有问题**，先修它。")
        print("   它现在【不能】用来证明任何别的东西。")
    else:
        print(f"✅ {n}/{n} 组全对：")
        print(f"   A 组 {n_a} 条 —— `check()` 的 {len(BROKEN)} 种坏法都抓得住，"
              "原样的也放行；")
        print(f"   B 组 {n_b} 条 —— `全部回归` 的 5 种坏法都让它红，")
        print("   连「安全网挪到 return 1 之后」（2026-09-16 那个真 bug）也重现得住。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
