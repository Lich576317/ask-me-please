# -*- coding: utf-8 -*-
"""尺子 —— 「自己带计数器」的那几份脚本共用的【出口自检】。

（这个文件没有 main()，不用直接跑它。它是被别的脚本 import 的。）

────────────────────────────────────────────────────────
为什么要有它（2026-09-21，接在 `框选联调._selftest_check()` 后面）：

`check()` 是全部 400 项断言的唯一出口，但它在八份脚本里
**各写了一遍**，写法还不一样：

    · `知识库联调` / `AI接入联调` —— 两个列表 `PASS` / `FAIL`
    · `快捷键联调`                —— 三个列表（多一个 `SKIP`）
    · `自动通道联调`              —— 两个整数 `_ok` / `_fail`

写法不一样本身不致命。致命的是：**这四条出口，一条都没有被验过。**
（`框选联调.py` 那条当天补了自检，另外四条没有 —— 那 257 项就是这么落下的。）

────────────────────────────────────────────────────────
★ 危险的那种坏法，不是「不计数」，是【无条件记通过】：

    不计数  → 通过 0 项，一眼就看得见，
              而且 `全部回归.py` 的项数对账（实到 0 ≠ 期望 80）当场红。
    无脑记通过 → 通过 80 项　失败 0 项
              ← **期望正好是 80，项数对账照样通过。**

一屏全绿，一个字都不报错。跟 `check()` 那边是同一个病，只是换了个位置。

────────────────────────────────────────────────────────
判据只有一条，而且是【方向】不是【实现】：

    假断言必须记到失败那一侧；
    真断言必须记到通过那一侧。

不管计数器是列表还是整数、不管通过数从哪个表达式读出来 ——
两个方向各走一遍，走反了就是这把尺子坏了，而**坏了的尺子量出来的
任何数都没有意义**（它不是「可能错」，是「不可信，且不可信得很安静」）。

⚠️ 所以这个文件【故意写成通用的】：它不认识 `PASS`、也不认识 `_ok`，
  只认调用方递进来的两个动作 —— 快照和还原。
  ★ 这样它就不会变成「第五种写法」。
"""
import contextlib
import io
import sys

# ⚠ 顺手把 stdout 的编码摆平 —— 这件事该由「唯一实现处」来干。
#
#   本文件打的 `✓` 不在 GBK 里（U+2713），而这几份脚本的输出在跑回归时
#   **全被重定向**（`全部回归.py` 就是拿管道接的），Python 那时默认走本地
#   编码 GBK，`print` 当场抛 UnicodeEncodeError。
#
#   ★ 2026-09-21 实测：`AI接入联调.py` 原来一个非 GBK 的字都不打
#     （它的失败前缀是 `[OK]` / `[!!]`，全 ASCII），所以从来没写过那五行
#     —— 这道闸门一加上去，它立刻崩在 `✓` 上。**加一个字符就能压垮一份
#     跑了很久的脚本，而崩的地方和被测的东西毫无关系。**
#   放在这里而不是四份各写一遍：谁也别再漏。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# `全部回归.py` 拿这一串认「这道闸门真的跑过」。
# ⚠️ 六份脚本都打这一串，改它要两边一起改（同 `框选联调` 那句）。
MARK = "check() 自检通过"


def probe_check(check_fn, snapshot, restore):
    """两个方向探一份 `check()`。返回「哪里不对」的清单（空 = 好）。

    `check_fn`  —— 被测的那份 `check`，签名 `(name, cond, detail="")`
    `snapshot`  —— `() -> (通过数, 失败数)`
    `restore`   —— `(通过数, 失败数) -> None`，把计数还原回去

    ★ 这一段【故意一行都不调用被验的那个 `check()` 之外的断言工具】——
      被验的就是它。拿 `check()` 验 `check()`，等于让被告坐审判席：
      它要是坏成了「无条件记通过」，下面每条自检会跟着一起变绿。

    ⚠️ 探针自己【不许污染汇总】。它往里写了两条（一真一假），
      跑完必须原样还回去 —— 否则每份脚本的通过数都会多 1、失败数多 1，
      项数对账当场红，而红的原因跟产品毫无关系。
      所以这里是 `restore(快照)`，不是「减掉 2」那种心算。
    """
    bad = []
    o0, f0 = snapshot()

    def step(cond, tag, want):
        """走一步：跑一次 check，看两个计数各动了多少。

        ⚠️ 这一步打出来的字【必须吞掉】。探针里有【故意为假】的那一条，
           `知识库联调.check` 会老老实实打成 `[失败] [尺子自检]假` ——
           而 `全部回归.py` 的明细正则正是拿 `[失败]` 挑行的：
           一份**全绿**的报告底下会多出一行「失败明细：[尺子自检]假」。
           ★ 探针的字混进被测的输出里，就是把噪音喂给了读报告的人。
           （`框选联调._selftest_check()` 当初也是这么处理的，这里照办。）
        """
        nonlocal bad
        before = snapshot()
        with contextlib.redirect_stdout(io.StringIO()):
            check_fn(f"[尺子自检]{tag}", cond)
        after = snapshot()
        got = (after[0] - before[0], after[1] - before[1])
        if got != want:
            bad.append(
                f"{tag}断言该让「通过{'+1' if want[0] else '不动'}、"
                f"失败{'+1' if want[1] else '不动'}」，"
                f"实际是「通过{got[0]:+d}、失败{got[1]:+d}」")

    step(True, "真", (1, 0))       # 真条件 → 通过那一侧 +1
    step(False, "假", (0, 1))      # 假条件 → 失败那一侧 +1

    restore((o0, f0))
    if snapshot() != (o0, f0):
        bad.append(f"探针没把计数还回去：跑完是 {snapshot()}，跑之前是 {(o0, f0)}")
    return bad


def gate(check_fn, snapshot, restore, label=""):
    """闸门：自检不过就【不开跑】。返回 True = 可以跑。

    ⚠ 为什么不过就整个不跑，而不是「记一条不通过再接着跑」：
      尺子坏了的时候，量出来的数不只是「可能错」，而是**没有意义** ——
      接着跑只会产出一屏看着正常的假数据，而那份假数据正是
      「跑过了、没问题」这句话的唯一依据。同 `框选联调.gate_check_selftest()`。

    ⚠️ 调用点【必须在任何 try 之外】，而且要在真正干活之前。
      这个项目栽过一次：守门断言和被守的代码套在同一个 try 里，
      前者抛异常时后者跟着缺席，而汇总照样打绿的。
    """
    bad = probe_check(check_fn, snapshot, restore)
    if not bad:
        print(f"  ✓ {MARK}"
              + (f"（{label}）" if label else "")
              + "：假断言记失败、真断言记通过，两个方向都验了")
        return True
    print(f"  ⚠⚠ {MARK}没过 —— 它是这份脚本全部断言的唯一出口，")
    print("     它坏了的话下面每一条都会变绿，所以【不跑了】：")
    for b in bad:
        print(f"      · {b}")
    return False


# ── 给「列表型」计数器用的现成快照/还原 ────────────────────
# （`PASS` / `FAIL` 那种写法，四份里有三份是它）
def list_snapshot(pass_list, fail_list):
    """返回一对 `(snapshot, restore)`，直接递给 `gate()`。"""
    def snap():
        return len(pass_list), len(fail_list)

    def restore(s):
        del pass_list[s[0]:]
        del fail_list[s[1]:]
    return snap, restore


# ── 给「整数型」计数器用的 ────────────────────────────────
def int_snapshot(getter, setter):
    """`getter() -> (ok, fail)`、`setter(ok, fail)` 由调用方给。

    ⚠️ 整数型没法「截断回原样」，只能整个写回去 —— 所以调用方那边
      往往得写成 `def _set(v): global _ok, _fail; _ok, _fail = v`。
    """
    def snap():
        return getter()

    def restore(s):
        setter(s)
    return snap, restore


def _selfcheck():
    """这个文件自己的反向对照 —— 免得它变成第五种没验过的写法。

    跑法：`python 冒烟测试\\尺子.py`
    """
    print("尺子自检（尺子自己的反向对照）")

    def make(kind):
        """造一份指定写法的计数器，返回 (check, snap, restore, 读两个数)。"""
        if kind == "列表":
            P, F = [], []

            def ck(name, cond, detail=""):
                # ★ 必须【真打一行】。第一版这里只 append 不 print，
                #   于是「探针有没有漏字」那条恒绿 —— 一个不打字的假 check
                #   物理上就漏不出东西来。**夹具够不着，变体就是装饰品。**
                if cond:
                    P.append(name)
                    print(f"  [通过] {name}")
                else:
                    F.append((name, detail or "断言为假"))
                    print(f"  [失败] {name}　→ {detail}")
                return cond
            s, r = list_snapshot(P, F)
            return ck, s, r, (lambda: (len(P), len(F)))
        P, F = [], []

        def ck2(name, cond, detail=""):
            P.append(name)            # ★ 坏的：无条件记通过
            print(f"  [通过] {name}")  # 而且照打一行 —— 这才像个真 check
            return cond
        s, r = list_snapshot(P, F)
        return ck2, s, r, (lambda: (len(P), len(F)))

    bad = 0
    # 正向：好的那把，必须一声不吭
    ck, s, r, _ = make("列表")
    if probe_check(ck, s, r):
        print("  ✗ 好的 check 也被报了错 —— 那不是判据，是噪音")
        bad += 1
    else:
        print("  ✓ 好的 check：两个方向都对，一声不吭")
    # 反向：无脑记通过那把，必须报出来
    ck, s, r, _ = make("坏")
    if probe_check(ck, s, r):
        print("  ✓ 坏的 check（无条件记通过）：报出来了")
    else:
        print("  ✗ 坏的 check 没被报出来 —— 这个文件是装饰品")
        bad += 1
    # ★ 探针的字不许漏进日志。
    #   —— 这一条没验的话，上面 step() 里那句「必须吞掉」就只是句注释：
    #     探针里有【故意为假】的一条，漏出去就是全绿报告底下多一行失败明细。
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        probe_check(*make("列表")[:3])
    if buf.getvalue():
        print(f"  ✗ 探针漏了字出去：{buf.getvalue()!r}")
        bad += 1
    else:
        print("  ✓ 探针一个字都没漏进日志")
    print(("❌ %d 条不符" % bad) if bad else "✅ 全对")
    return 1 if bad else 0


if __name__ == "__main__":
    # ⚠️ 只是为了让这个文件能被 `py_compile` 之外的场合直接跑一下。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(_selfcheck())
