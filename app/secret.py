# -*- coding: utf-8 -*-
"""API Key 的落盘保护 —— 「明文 JSON」那一半的收口。

（这个文件没有 main()，不用直接跑它。它是被 `main.py` import 的。）

────────────────────────────────────────────────────────
来历（2026-09-21）：

`app/settings.json` 里 `api_key` 一直是**明文**。这条是 v0.7 遗留的最要紧一条
隐私待办，`CLAUDE.md` 的「尚未决定」和 `进展文档.md` 六-4 都记着它。

    ✅ 入库那一半已经封住了：`.gitignore:22` 命中 `app/settings.json`，
       `git ls-files app/` 里没有它 —— **真 Key 不会被提交**。
    ❌ 落盘那一半没有：文件里仍是明文，任何能读它的人 / 以用户身份跑起来的
       程序都拿得到。

方案是 Windows DPAPI（`win32crypt`，pywin32 已在依赖里），
**2026-09-21 由用户拍板**。代价他自己认了：

> 密钥绑死「这台机器 + 这个用户」—— 换机器、换用户、把 settings.json 拷走，
> 都得重新填一次 Key。

★ 为什么这个取舍非他拍不可：**这把 Key 是这个项目里唯一一份「丢了就没了」的
  数据**（`kb.db` 脏了能重建，Key 不能）。2026-09-15 还被联调脚本覆盖过一次
  （附录 J）。所以这里每一个「可能把它弄坏」的动作，都按那条优先级处理。

────────────────────────────────────────────────────────
落盘格式（**唯一真相来源就在这个文件**）：

    "api_key": "dpapi:v1:<base64>"

★ 为什么要有那个前缀，而不是「一律当密文解」：

老 `settings.json` 里躺着一把**明文** Key，而且**已经填进去了**。
加载时若无脑去解密，会解不出来 → 静默返回空 → 用户看到的是「Key 没填」。

那正是本项目最忌讳的形状：**把「读不出来」和「本来就没有」说成同一件事。**
（同 `全部回归.py` 的 ⏸ 跳过 ≠ 通过、同「截断了」不许报成「通过 N 项」。）

有前缀才分得开三态：**密文 / 明文（待迁移）/ 空**。
第四态是坏的密文（换了机器/用户、或者文件被改坏了），也要单独分出来，
因为**它要给用户一句能照做的话**（「重新填一次 Key」），而「空」不用给。

────────────────────────────────────────────────────────
⚠️ 这个文件【不许】把 Key 拿去打日志。`unseal()` 只把明文交给调用方，
   自己一个字都不打 —— 包括出错的时候。
"""

import base64

PREFIX = "dpapi:v1:"

# DPAPI 的 `CRYPTPROTECT_UI_FORBIDDEN`。
# ⚠️ 自己写死这个 1，是因为 **`win32crypt` 里没有这个常量**
#    （2026-09-21 实测：`[n for n in dir(win32crypt) if n.startswith("CRYPT")]`
#     返回**空列表**，而 `CryptProtectData` 本身是有的，只是不导出常量）。
#    别的项目里常写成 `win32crypt.CRYPTPROTECT_UI_FORBIDDEN` —— 在这个 pywin32
#    版本上会 AttributeError。**别照抄那个写法。**
#    它的意思是「不许弹 UI」：解密失败时直接报错返回，不要弹一个对话框 ——
#    这里是开机加载，弹窗会把启动卡住，而且用户看不懂。
_UI_FORBIDDEN = 0x1

try:
    import win32crypt
    _HAS = True
except Exception:               # pragma: no cover - 只在没装 pywin32 时才走
    win32crypt = None
    _HAS = False


def available():
    """DPAPI 这台机器上能不能用。

    ⚠️ 它【不参与】`seal()` 的判据 —— `seal()` 是「试了才知道」。
       `import win32crypt` 成功不等于 `CryptProtectData` 一定能跑
       （没见过，但把它当成前提没有好处）。这个函数是给**日志和测试**用的：
       要报告「为什么这把 Key 是明文」，得先能说出是哪一种。
    """
    return _HAS


def is_encrypted(v):
    """这个值看起来像不像我们写的密文。**不看内容能不能解开。**"""
    return isinstance(v, str) and v.startswith(PREFIX)


def seal(plain):
    """明文 Key → 要写进 settings.json 的那个字符串。

    · 空        → `""`（不去加密一个空值 —— 那只会让配置文件更难读）
    · 能加密    → `"dpapi:v1:<base64>"`
    · 加不了密  → **原样返回明文**（调用方必须自己看出来并报警，
                  判据是 `is_encrypted(返回值)`）

    ★ 这里【不抛异常】。存盘存不下去的时候，用户会连 `kb_policy`、`hotkey`
      一起存不了 —— **一把 Key 的存储方式不该把整个操作台锁死**。
      「降级了」这件事由返回值暴露给调用方去说，不由异常去说。

    ★★ 最后那道 `unseal(blob) != plain` 是这个文件的中心：
      **加密完当场读一遍，读不回来就宁可存明文。**
      没有它的话，「加密成功、但读回来是别的（或者读不回来）」会让
      用户**唯一一把找不回来的 Key** 在下次启动时变成一个解不开的串 ——
      而那时没有任何人能把它救回来。
      ⚠️ 同族教训见 `CLAUDE.md`：「自证不算验证」——
      但**这里**的对照不是我自己（DPAPI 是外部裁判），所以它算数。
    """
    if not plain:
        return ""
    if not _HAS:
        return plain
    try:
        blob = win32crypt.CryptProtectData(
            plain.encode("utf-8"), None, None, None, None, _UI_FORBIDDEN)
    except Exception:
        return plain
    stored = PREFIX + base64.b64encode(blob).decode("ascii")
    back, _status = unseal(stored)
    if back != plain:
        return plain
    return stored


def unseal(stored):
    """落盘字符串 → `(明文, 状态)`。

    状态四个值，**调用方必须四个都想到**：

        "empty"  —— 本来就没有（`""` / `None` / 不是字符串）
        "plain"  —— 老格式的明文。★ **原样可用**，而且**待迁移**
        "enc"    —— 密文，解开了
        "bad"    —— 看起来是密文却解不开（换了机器/用户，或者文件坏了）

    ★ `"bad"` 和 `"empty"` **必须分开**。合成一个的话，
      「Key 解不开了，请重填一次」会变成一片安静 —— 用户只会觉得
      「怎么又要填」，而**真正的原因（换过机器/用户）永远不会被说出来**。
      同 `CLAUDE.md` 那条：**检查项的措辞要小于等于它的实际范围**。

    ★ 解不开时**不抛异常**：抛出去会被 `main.load_settings()` 那个大 `try`
      吞成「整份设置读不出来 → 全用默认值」，那比丢一把 Key 更糟 ——
      用户会连框选区域、知识库策略、快捷键一起丢，而且不知道发生过什么。

    ⚠️ 出错路径上**一个字都不打**（尤其不打异常内容）。
      `pywintypes.error` 的文本在这台机器上出来是乱码
      （2026-09-21 实测，GBK 解码问题），打出去只会让日志更难读。
    """
    if not isinstance(stored, str) or not stored:
        return "", "empty"
    if not is_encrypted(stored):
        return stored, "plain"
    body = stored[len(PREFIX):]
    try:
        raw = base64.b64decode(body, validate=True)
        out = win32crypt.CryptUnprotectData(raw, None, None, None, _UI_FORBIDDEN)
        # ★★★ 这里【不能】写 `...[0]`。2026-09-21 实测：本机 pywin32 的返回值是
        #     `(描述, 数据)` —— **下标 0 是 Description（空串），下标 1 才是数据**。
        #     网上到处流传的 `CryptUnprotectData(blob, None, None, None, 0)[0]`
        #     在这个版本上**恒返回 `''`**，一个字节都不报错。
        #
        #     ⚠️ 这个坑的后果正是「静默降级」那一种：`unseal` 永远说读不回来
        #        → `seal` 的往返自检永远不过 → **Key 永远以明文落盘，
        #        而功能看起来一切正常**（存得住、读得出、界面照常显示已配置）。
        #        抓到它的是 `seal()` 里那句往返断言 —— 不是任何一条「能存能读」的
        #        断言（那几条在坏掉的版本下**照样是绿的**）。
        #
        #     所以这里按**类型**取，不按下标取：两个元素的顺序将来变了也不怕。
        #     **谁要把这行「顺手理顺」成 [0]，先看上面这段。**
        data = next((x for x in out if isinstance(x, bytes)), None)
        if data is None:
            return "", "bad"
        return data.decode("utf-8"), "enc"
    except Exception:
        return "", "bad"
