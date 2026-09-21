# -*- coding: utf-8 -*-
"""扫一遍所有联调脚本：`check()` 的 `detail` 里，有没有写【只在失败时才成立】的话。

    python "Desktop\\问了吗\\冒烟测试\\扫描detail文案.py"              # 扫描
    python "Desktop\\问了吗\\冒烟测试\\扫描detail文案.py" --selfcheck  # 反向对照

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
★★ 2026-09-21 补的两个洞（都是第一版扫描器**真的漏掉了真话**才发现的）：

**洞一：只看 `detail=` 关键字，不看第三个【位置参数】。**
  第一版只遍历 `node.keywords`，而全仓有 **398 处** `check()` 是把 detail
  **按位置**传的（`check("名", cond, "文案", hint=...)`）—— 那些**一处都没被看过**。
  于是它报告「可疑处 0」，而屏幕上正打着：

      [通过] ★ 「查看详情」真的点进去了（上报来了）　点了 1 次，`[前端] 详情:` 一次都没来

**洞二：带插值的 f-string 整条放行。**
  原来的理由是「它带着运行时值，静态判不了」—— 这理由对**插值那部分**成立，
  但插值旁边的**静态片段**照样是能判的。上面那句就是
  `f"点了 {n} 次，…一次都没来"`：`{n}` 判不了，可「一次都没来」是白纸黑字。

⚠️ 两个洞合起来说明一件事：**扫描器报「0 处」时，那个 0 可能只是「没看」**。
   和本项目那条老规矩同源 —— 一个不生效的检查，和一个通过的检查，长得一模一样。

────────────────────────────────────────────────────────
⚠️ 不是所有脚本的 `check()` 都一样，**同一句话在不同文件里对错相反**：

  · `框选联调.check()` —— 通过时**也**打 detail（`框选联调.py:126`）→ 必须中性
  · 另外六份各自定义了一份 `check()` —— detail **只在失败时**打 → 写失败话**是对的**

所以判据是**逐文件**的：`detail_on_pass()` 自己去读那份脚本的 `check()`，
看 `if cond:` 那一支里的 `print` 有没有引用 `detail`。
**不能一刀切** —— 一刀切会把这六份是正确的写法（共 14 处）全报出来，
而「判据写宽了会把真缺陷淹掉」，那时候没人再看这个报告了。

────────────────────────────────────────────────────────
为什么走 AST 而不是正则：

`detail=` 的值常常是**跨行的隐式拼接**（相邻字符串字面量），正则要么漏掉跨行那种，
要么把后面相邻的别的参数一起吃进来。AST 直接给出「这个实参到底是哪几个
字符串常量拼的」，不存在这两种误判。

⚠️ 带 `if`/`else` 的表达式（`detail="…" if bad else ""`）**直接放行** ——
那是**另一种写法的同一个意思**（条件为假时打出来的是空串），不算违规。
存量里有不少这种，别去改它们。★ 它同时也是这类文案的**正解**：
`知识库端到端联调.py:920` 就是「不进库时才有话说」的标准写法。

────────────────────────────────────────────────────────
★ 2026-09-21 扫描结果：**3 处 → 收掉 1 处 → 余 2 处（都是已知误报）**

    收掉的那处：`check(名, False, "…")` —— 无条件失败的守卫，只有失败那一侧，
    现在按字面量识别并跳过（`_is_always_false()` 那条判据不是启发式）。

    余下 2 处**判成人眼误报，保持原样**，它们都是**判据的陈述**，
    在通过那一行上照样成立：

      · `AI端到端联调.py:395`「有 `[管线] 截屏` 就说明 clip 那条也去截屏了 —— 它不该截屏」
        —— 说的是「看到什么意味着什么」，不是在声称**看到了**。
      · `知识库端到端联调.py:979`「两个条件同时成立，才说明『排除 > 命中』这条顺序是对的」
        —— 同上，它解释的是这条断言凭什么成立。

    ⚠️ **别为了讨好这张表去改这两句** —— 表是筛子不是判据，
    把好好的判据说明改得读不懂，那是尾巴摇狗。**收掉的那一处才是真缺陷。**
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

# ⚠️ 名字【不能以 `_` 开头】—— `.gitignore` 里有一条 `冒烟测试/_*`，
#   那一条是拿来挡日志/临时 settings 的。这个文件是**工具**，得进库，
#   取 `_` 开头的话 `git check-ignore` 会命中它，**它压根提交不上去**，
#   而文档里已经引用了它 —— 链接指向一个不存在的东西。
#   先跑 `git check-ignore -v 冒烟测试/扫描detail文案.py`（exit 1 = 没被忽略）。
#
# ⚠️⚠️ 这里原来写的是「`.gitignore:33`」—— **写死行号的引用会漂**。
#   2026-09-21 v0.20.2 往 `.gitignore` 第一节加了 `app/settings.json.tmp`，
#   那条规则从第 33 行挪到了第 38 行，于是这行注释开始指向**别人的规则**
#   （幸好指到的还是一条真规则，只错不错在「说的是别的事」上）。
#   查 `git check-ignore -v` 别查行号 —— 它直接告诉你命中的是哪一条。
#   ⚠️ 2026-09-21 又添一例：同一天加的 `app/_region.png` 把行号再推后一次。
#   **这份文件自己：**
#     git check-ignore -v 冒烟测试/扫描detail文案.py   # 期望 exit 1、无输出


def literals(node):
    """把一串相邻字符串常量拼起来；拼不了（带条件）返回 None。

    ★ 与第一版不同：**带插值的 f-string 不再整条放行** ——
      只把它的**静态片段**拿出来（插值那几段本来就判不了），
      逐段查。见文件头「洞二」。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        l, r = literals(node.left), literals(node.right)
        return None if l is None or r is None else l + r
    return None                               # IfExp 等 → 放行


def detail_arg(call):
    """取这一次 `check()` 调用里 detail 那个实参；没有就返回 None。

    ★ 这是「洞一」的修法：`detail=` 关键字**优先**，没有就认**第三个位置参数**。
      全仓 398 处是位置传的，第一版一处都没看。
    """
    for kw in call.keywords:
        if kw.arg == "detail":
            return kw.value
    if len(call.args) >= 3:
        return call.args[2]
    return None


def _is_always_false(call):
    """`check(名, False, …)` —— 一个【永远打不到通过那一侧】的守卫。

    判据是字面量，不是猜：第二个实参写死 `False`，通过那条 `print` 就不存在，
    它 detail 里的话只会在失败时出现，怎么写都是对的。
    """
    return (len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and call.args[1].value is False)


def detail_on_pass(tree):
    """这份源码里 `check()` 在【通过】那一侧会不会打 detail。

    · 自己定义了 `check` → 看 `if cond:` 那一支的 print 有没有引用 detail
    · 没自己定义 → 用的是 `框选联调.check`（两路都打）→ True

    ⚠️ 判不出来的（有 `check` 却找不到 `if cond`）**按 False 算**，
       「宁漏勿误报」：这个报告的用处全在「命中都是真的」上，
      一旦常驻一批噪音，它就会被当成背景略过 —— 那比不扫还坏。
    """
    for node in tree.body:                    # 只看顶层，别把别的函数里的同名 def 算进来
        if isinstance(node, ast.FunctionDef) and node.name == "check":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.If)
                        and isinstance(sub.test, ast.Name)
                        and sub.test.id == "cond"):
                    body = ast.dump(ast.Module(body=sub.body, type_ignores=[]))
                    return "'detail'" in body
            return False
    return True


def scan_source(src, on_pass=True):
    """返回 [(行号, 命中的词, 那段话)]。`on_pass` = 这份脚本的 check 通过时打不打 detail。"""
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
        # ★ `check(名, False, "…")` 是【无条件失败的守卫】——
        #   它那一行永远打在失败那侧，detail 里写失败话**天经地义**。
        #   `知识库端到端联调.py:939` 就是这种（写在 `if 词不在界面上: return` 里面）。
        #   这一条不是启发式：`cond` 是字面量 `False` 时，通过那条路**不存在**。
        if _is_always_false(node):
            continue
        arg = detail_arg(node)
        if arg is None:
            continue
        # ★ 只在「通过时也会打」的脚本里才要求中性；另外六份是失败才打，写失败话是对的
        if not on_pass:
            continue
        frags = literals(arg)
        if frags is None:                     # 带 if/else → 放行
            continue
        for text in frags:
            hit = [w for w in FAILWORDS if w in text]
            if hit:
                out.append((node.lineno, hit, text))
    return out


def selfcheck():
    """反向对照：把已知的坏写法喂进去必须报，好写法必须放行。

    ★ 没有这一段，「扫出 0 处」和「扫描器坏了」在屏幕上长得一模一样 ——
      本项目的老账：**全绿的检查必须能被弄红**。

    ★★ 下面第 3、4 条是 2026-09-21 补的，**它们就是扫描器当时漏掉的那个形状**：
      位置参数 + 带插值的 f-string。补上之前，同样的坏写法喂进去是「放行」的。
    """
    cases = [
        # (标签, 源码, detail 在两路都打吗, 期望报吗)
        ("坏：detail= 里写失败话",
         'check("x", c, detail="守卫那段一个字都没打")', True, True),
        ("坏：跨行拼接",
         'check("x", c, detail="上不了屏说明 "\n  "extras 没接住")', True, True),
        ("坏：位置参数 + f-string（洞一+洞二）",
         'check("x", c, f"点了 {n} 次，上报一次都没来")', True, True),
        ("坏：纯位置参数",
         'check("x", c, "上一步没找到这个词")', True, True),
        ("好：这份脚本失败时才打 detail",
         'check("x", c, "上一步没找到这个词")', False, False),
        ("好：detail 带 if/else",
         'check("x", c, detail="一个字都没打" if bad else "")', True, False),
        ("好：失败话放进了 hint",
         'check("x", c, hint="一个字都没打")', True, False),
        ("好：detail 是中性观察",
         'check("x", c, detail="等了 3.0s")', True, False),
        ("好：f-string 插值旁边是中性话（洞二的另一半）",
         'check("x", c, f"click_until 返回 {n}，等了 {t} 秒")', True, False),
        ("好：cond 是字面量 False（守卫只有失败那一侧）",
         'check("拿到坐标才能右键", False, "上一步没找到这个词")', True, False),
    ]
    bad = 0
    for label, src, on_pass, want in cases:
        got = bool(scan_source(src, on_pass))
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {label:<34} → "
              f"{'报' if got else '放行'}（期望 {'报' if want else '放行'}）")
    print(f"\n反向对照：{'通过 —— 它抓得住已知的坏写法' if not bad else f'{bad} 条不符 = 扫描器本身有问题'}")
    return 1 if bad else 0


def main():
    if "--selfcheck" in sys.argv:
        return selfcheck()

    total = 0
    files = 0
    skipped = 0
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        name = os.path.basename(path)
        if name in SKIP:
            continue
        files += 1
        src = open(path, encoding="utf-8").read()
        try:
            on_pass = detail_on_pass(ast.parse(src))
        except SyntaxError:
            continue
        if not on_pass:
            skipped += 1
        for line, hit, text in scan_source(src, on_pass):
            total += 1
            print(f"{name}:{line}  命中 {hit}")
            print(f"    {text[:160]}")

    print(f"\n扫了 {files} 份脚本，可疑处：{total}")
    # ★ 把「这份没扫」如实说出来 —— 报告里必须能看出覆盖面。
    #   不说的话，「扫了 19 份、可疑处 0」会被读成「19 份都干干净净」，
    #   而其中 {skipped} 份的 detail 压根不该按这条判（它们本来就只在失败时打）。
    print(f"⚠️ 其中 {skipped} 份的 `check()` 是「只在失败时打 detail」的写法，"
          f"它们写失败话**是对的**，这份扫描器按设计不看它们。")
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
