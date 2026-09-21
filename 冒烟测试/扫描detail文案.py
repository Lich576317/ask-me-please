# -*- coding: utf-8 -*-
"""扫一遍所有联调脚本：`check()` 的 `detail` 里，有没有写【只在失败时才成立】的话。

    python "Desktop\\问了吗\\冒烟测试\\_扫描detail.py"              # 扫描
    python "Desktop\\问了吗\\冒烟测试\\_扫描detail.py" --selfcheck   # 反向对照

它不跑测试，只看源码。**不并进 `全部回归.py`** —— 它查的是测试自己写得好不好，
跟产品行为无关，混进项数会让「通过 N 项」那个数同时表示两件事。

────────────────────────────────────────────────────────
为什么要查这个（2026-09-21 立的规矩，真踩过两次）：

`check(name, cond, detail, hint)` 里，**`detail` 是【中性观察】，通过和不通过都会打出来**。
所以一句只有在失败那一侧才为真的话，一旦写进 `detail`，报告里就会出现：

    [通过] ★ 拖框守卫：光标摆不到起点时它会吭声　守卫那段一个字都没打

读者看到的是「**通过** ＋ 一句说它**没做到**的话」。
这类话最容易被当成噪音略过 —— 于是**测试自己把自己的可信度磨掉了**，
而这正是它当初被写出来要消灭的东西（同「标签撒谎比没有标签更坏」）。

判据：写完问一句「**这句话在【通过】那一行上还成立吗**」——
不成立，它就该是 `hint`。

────────────────────────────────────────────────────────
为什么走 AST 而不是正则：

`detail=` 的值常常是**跨行的隐式拼接**（相邻字符串字面量），正则要么漏掉跨行那种，
要么把后面相邻的别的参数一起吃进来。AST 直接给出「这个关键字参数到底是哪几个
字符串常量拼的」，不存在这两种误判。

⚠️ 带 `if`/`else` 的表达式（`detail="…" if bad else ""`）**直接放行** ——
那是**另一种写法的同一个意思**（条件为假时打出来的是空串），不算违规。
存量里有不少这种，别去改它们。

⚠️ 带 f-string 插值的也放行 —— 它带着运行时值，静态判不了。
"""
import ast
import glob
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

# 只有在【失败】那一侧才为真的措辞。
# ⚠️ 这张表**不可能穷尽** —— 它是个筛子不是判据（「上不了屏说明…」当初就漏了，
#   因为它一个「没」字都不带）。所以下面每条命中都还要人看一眼，别当自动判决。
FAILWORDS = [
    "没", "还在", "一个字", "不会", "没有", "上不了", "说明",
    "会红", "就等于", "坏了", "对不上", "丢了", "看不到",
    "进不去", "不出现", "不该", "不许", "退化成", "不见了",
]

# 扫描时跳过的文件（自己扫自己没意义，而且它引用了这些词）
SKIP = {"扫描detail文案.py"}

# ⚠️ 名字【不能以 `_` 开头】—— `.gitignore:33` 有一条 `冒烟测试/_*`，
#   那一条是拿来挡日志/临时 settings 的。这个文件是**工具**，得进库，
#   取 `_` 开头的话 `git check-ignore` 会命中它，**它压根提交不上去**，
#   而文档里已经引用了它 —— 链接指向一个不存在的东西。
#   先跑 `git check-ignore -v 冒烟测试/扫描detail文案.py`（exit 1 = 没被忽略）。


def literals(node):
    """把一串相邻字符串常量拼起来；拼不了（带插值/带条件）返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        if any(isinstance(v, ast.FormattedValue) for v in node.values):
            return None                       # f-string 带运行时值 → 放行
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        l, r = literals(node.left), literals(node.right)
        return None if l is None or r is None else l + r
    return None                               # IfExp 等 → 放行


def scan_source(src):
    """返回 [(行号, 命中的词, 那段话)]。"""
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != "check":
            continue
        for kw in node.keywords:
            if kw.arg != "detail":
                continue
            text = literals(kw.value)
            if text is None:
                break
            hit = [w for w in FAILWORDS if w in text]
            if hit:
                out.append((node.lineno, hit, text))
            break
    return out


def selfcheck():
    """反向对照：把已知的坏写法喂进去必须报，好写法必须放行。

    ★ 没有这一段，「扫出 0 处」和「扫描器坏了」在屏幕上长得一模一样 ——
      本项目的老账：**全绿的检查必须能被弄红**。
    """
    cases = [
        ("坏：detail 里写失败话",
         'check("x", c, detail="守卫那段一个字都没打")', True),
        ("坏：跨行拼接",
         'check("x", c, detail="上不了屏说明 "\n  "extras 没接住")', True),
        ("好：detail 带 if/else",
         'check("x", c, detail="一个字都没打" if bad else "")', False),
        ("好：失败话放进了 hint",
         'check("x", c, hint="一个字都没打")', False),
        ("好：detail 是中性观察",
         'check("x", c, detail="等了 3.0s")', False),
        ("好：detail 带 f-string 插值",
         'check("x", c, detail=f"查了 {n} 次")', False),
    ]
    bad = 0
    for label, src, want in cases:
        got = bool(scan_source(src))
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {label:<22} → "
              f"{'报' if got else '放行'}（期望 {'报' if want else '放行'}）")
    print(f"\n反向对照：{'通过 —— 它抓得住已知的坏写法' if not bad else f'{bad} 条不符 = 扫描器本身有问题'}")
    return 1 if bad else 0


def main():
    if "--selfcheck" in sys.argv:
        return selfcheck()

    total = 0
    files = 0
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        name = os.path.basename(path)
        if name in SKIP:
            continue
        files += 1
        src = open(path, encoding="utf-8").read()
        for line, hit, text in scan_source(src):
            total += 1
            print(f"{name}:{line}  命中 {hit}")
            print(f"    {text[:160]}")

    print(f"\n扫了 {files} 份脚本，可疑处：{total}")
    if total:
        print("⚠️ 逐条看一眼：这句话在【通过】那一行上还成立吗？"
              "不成立就该挪进 hint=。")
        print("   （表里的词是筛子不是判据，会有误报。）")
    else:
        print("✓ 没有把「只在失败时才成立」的话写进 detail。"
              "⚠️ 想确认这个 0 是真的，跑 --selfcheck。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
