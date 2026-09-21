# -*- coding: utf-8 -*-
"""API Key 落盘联调 —— 函数级，纯内存 + 一个临时文件，不开窗口、不联网、不花钱。

跑起来：
    python "Desktop\\问了吗\\冒烟测试\\密钥落盘联调.py"

本文件盯的是 `app/secret.py`（DPAPI 落盘保护，2026-09-21）和
`app/main.py` 里 `load_settings()` / `save_settings()` 这两处的**接线**。

────────────────────────────────────────────────────────
★ 为什么这件事非验不可（跟别的模块的理由不一样）：

**这把 Key 是这个项目里唯一一份「丢了就没了」的数据。**
`kb.db` 脏了能重建，Key 不能 —— 2026-09-15 就被联调脚本覆盖过一次（附录 J）。
而这次改动是**唯一一次「主动去重写它」**的改动：加解密写在盘上，
一旦写坏，没有任何东西能把它救回来。

⚠️ 所以本文件里最重要的一条不是「能加密」，是下面 A.9 那条**往返自检**和
   B.0 那条**正向对照**：

     · A.9 —— `seal()` 加完密**当场读一遍**，读不回来就宁可存明文。
     · B.0 —— 先证明「夹具真的看得见明文」。**没有它，B.1 那条
       「盘上找不到明文」在一个「`save_settings` 啥都没写」的坏世界里
       【恒绿】** —— 那是本项目的老账（全绿的断言必须能被弄红）。

────────────────────────────────────────────────────────
⚠️ 测不了的一条，如实写在这里，别当成测过了：

  **DPAPI 在别的机器/用户上解不开** —— 这条恰恰是这套方案的**代价**，
  也正是它「绑死这台机器 + 这个用户」的原因。要真验它，得换一台机器或
  换一个 Windows 用户跑，本脚本做不到。
  能验的是它的**下游形状**（B.6：解不开时不许连累其余设置、不许静默）。
  上游那一半（DPAPI 真的会因为换机器而解不开）是 Windows 的契约，不归这里验。

────────────────────────────────────────────────────────
⚠️ 本脚本**全程不碰**用户的 `app\\settings.json`（用 `WENLEMA_SETTINGS`
   指到临时文件上，同另外几份脚本）。而且它**自己核一遍**这件事（C.2）——
   「不碰用户数据」是承诺，核过才算机制。
"""

import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

# ⚠ `▸` / `✓` 这类字符 GBK 编不出来，输出被重定向时 print 当场抛 UnicodeEncodeError。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ★ 必须在 import main 之前把 settings 指向临时文件。
#   （`settings_path()` 是**每次现读**的，所以理论上晚一点也行 ——
#    但摆在这里，是为了让「这份脚本碰不到真文件」这件事在源码上就看得见。）
TMP_SETTINGS = os.path.join(HERE, "_密钥联调_settings.json")
REAL_SETTINGS = os.path.join(APP, "settings.json")
_OLD_ENV = os.environ.get("WENLEMA_SETTINGS")
os.environ["WENLEMA_SETTINGS"] = TMP_SETTINGS

import secret                                                      # noqa: E402

# 「出口自检」那把共用的尺子（见 `尺子.py`）。
sys.path.insert(0, HERE)
import 尺子                                                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [通过] {name}")
    else:
        FAIL.append((name, detail or "断言为假"))
        print(f"  [失败] {name}　→ {detail}")


# ══════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════

KEY = "sk-wenlema-test-0123456789"

# 一份「老格式」的 settings.json：Key 是明文，别的键齐全。
# ★ 用它而不是用 DEFAULT_SETTINGS，是因为迁移那条要能看出
#   「别的键有没有被带走」。
OLD_STYLE = {
    "api_key": KEY,
    "kb_policy": "whitelist",
    "hotkey": "ctrl+shift+k",
    "system_prompt": "老提示词",
    "seen_notice": True,
    "region": [10, 20, 300, 400],
    "auto_settle_sec": 30,
}


def write_raw(obj):
    """**裸写**一份 settings.json（绕开 save_settings）。"""
    with open(TMP_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_raw():
    """**裸读**一份 settings.json（绕开 load_settings）。

    ★ 这两个「裸」函数是本文件的立身之本：被验的是 load/save 那一对，
      拿它们去读自己写的东西 = **让被告坐审判席**（`CLAUDE.md` 第 15 条）。
      判盘上到底有什么，必须用不认识这套逻辑的写法去看。
    """
    with open(TMP_SETTINGS, encoding="utf-8") as f:
        return json.load(f)


def read_bytes():
    with open(TMP_SETTINGS, "rb") as f:
        return f.read()


def cleanup():
    for p in (TMP_SETTINGS, TMP_SETTINGS + ".tmp"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
# A. `secret.seal()` / `secret.unseal()` 本身
# ══════════════════════════════════════════════════════════

def t_seal():
    print("\n▸ A. seal / unseal（纯函数，不碰盘）")

    blob = secret.seal(KEY)
    check("A.1 加密可用时 seal() 带 dpapi 前缀",
          secret.is_encrypted(blob), f"拿到的是 {blob[:40]!r}")
    check("A.2 ★ 密文里【不含】明文 Key",
          KEY not in blob,
          "密文里直接出现了明文 —— 那这个「加密」什么也没做")
    check("A.3 unseal(seal(x)) 拿回原值，状态是 enc",
          secret.unseal(blob) == (KEY, "enc"), f"实到 {secret.unseal(blob)!r}")

    # ★ 同明文两次加密**不该**相等（DPAPI 每次带随机盐）。
    #   写这条不是为了好看：它同时钉住「不许拿密文相等当判据」这件事 ——
    #   谁要是在别处写了 `assert seal(a) == seal(b)`，那条断言会**恒假**。
    check("A.4 同一个明文两次 seal 结果不同（DPAPI 带随机盐）",
          secret.seal(KEY) != blob)

    check("A.5 空 Key：seal('') 就是 ''，不去加密一个空值",
          secret.seal("") == "")
    check("A.6 空 Key：unseal 返回 ('', 'empty')",
          secret.unseal("") == ("", "empty"))
    check("A.7 不是字符串的也算「空」，不算「坏」",
          secret.unseal(None) == ("", "empty")
          and secret.unseal(123) == ("", "empty"),
          f"{secret.unseal(None)!r} / {secret.unseal(123)!r}")

    # ★ 老格式（明文）必须**原样可用** —— 用户现在盘上就是这一份。
    check("A.8 ★ 老格式的明文 Key 原样认得出、原样可用（状态 plain）",
          secret.unseal(KEY) == (KEY, "plain"), f"实到 {secret.unseal(KEY)!r}")

    # ★★★ 这条是整个改动的中心：加密完当场读一遍。
    #   没有它的话，「加密成功、但读回来是别的」会让用户唯一一把
    #   找不回来的 Key 在下次启动时变成一个解不开的串。
    back, status = secret.unseal(blob)
    check("A.9 ★★★ seal() 的往返自检真的过了（加完密读得回来）",
          back == KEY and status == "enc",
          f"读回来的是 {back!r}（状态 {status}）—— 那 seal 就该退回去存明文")

    check("A.10 坏密文（合法 base64 但不是 DPAPI 的）→ 状态 bad、明文空",
          secret.unseal("dpapi:v1:YWJjZGVmZ2hpamtsbW5vcA==") == ("", "bad"),
          f"实到 {secret.unseal('dpapi:v1:YWJjZGVmZ2hpamtsbW5vcA==')!r}")
    check("A.11 坏密文（base64 都不合法的）→ 状态 bad、不抛异常",
          secret.unseal("dpapi:v1:!!!不是base64!!!") == ("", "bad"))

    # ★★ 这条钉的是本项目最忌讳的那个形状：
    #   「读不出来」和「本来就没有」**不许长成同一个样子**。
    check("A.12 ★★「解的坏」和「本来空的」状态分得开（bad ≠ empty）",
          secret.unseal("").__class__ is tuple
          and secret.unseal("")[1] != secret.unseal("dpapi:v1:zzz")[1],
          "两个都变成同一个状态的话，用户只会觉得「怎么又要填」，"
          "而真正的原因（换过机器/用户）永远不会被说出来")

    # 不带前缀的 DPAPI 密文会被当成明文 —— 这是「前缀就是判据」的**有意**取舍。
    # 钉住它，免得将来有人以为这里有个 bug 而去「顺手修好」。
    import base64 as _b64
    import win32crypt as _wc
    raw_blob = _b64.b64encode(
        _wc.CryptProtectData(KEY.encode("utf-8"), None, None, None, None, 1)).decode()
    check("A.13 ★ 没有前缀的密文按【明文】对待（前缀就是判据，这是有意的）",
          secret.unseal(raw_blob)[1] == "plain",
          f"实到 {secret.unseal(raw_blob)[1]!r}")


# ══════════════════════════════════════════════════════════
# B. load_settings() / save_settings() 的接线
# ══════════════════════════════════════════════════════════

def t_wiring():
    print("\n▸ B. load_settings / save_settings 的接线")
    import main

    # ── B.0 正向对照 ────────────────────────────────────────
    # ★★★ 先证明这个夹具**真的看得见明文**。
    #   没有这一条，下面 B.1「盘上找不到明文」在一个
    #   「save_settings 压根没写文件」的坏世界里**恒绿** ——
    #   文件不存在 / 是空的，当然也找不到明文。
    #   这就是 m23 那条教训：**夹具够不着，变体就是装饰品。**
    write_raw(OLD_STYLE)
    raw_txt = read_bytes().decode("utf-8")
    check("B.0 ★★★ 正向对照：裸写一份老格式后，明文 Key 真的在文件里",
          KEY in raw_txt,
          "夹具连明文都放不进去 —— 下面那条「盘上没有明文」是恒绿的，不算数")

    # ── B.1 存盘：盘上不许有明文 ────────────────────────────
    main.save_settings({"api_key": KEY, "kb_policy": "dedupe"})
    raw_txt = read_bytes().decode("utf-8")
    check("B.1 ★★★ save_settings 之后，盘上【找不到】明文 Key",
          KEY not in raw_txt,
          "明文还躺在盘上 —— 这次改动没接上（save_settings 没走 secret.seal？）")
    on_disk = read_raw().get("api_key", "")
    check("B.2 盘上那一栏是密文（带前缀）",
          secret.is_encrypted(on_disk), f"盘上是 {on_disk[:40]!r}")
    check("B.3 ★ 盘上找不到明文，不是因为文件是空的（别的键写进去了）",
          read_raw().get("kb_policy") == "dedupe",
          f"读到 {read_raw()!r}")

    # ── B.4 读回来 ─────────────────────────────────────────
    got, first_run = main.load_settings()
    check("B.4 load_settings 把 Key 解回明文",
          got.get("api_key") == KEY, f"实到 {got.get('api_key')!r}")
    check("B.5 不是首次运行（文件在）",
          first_run is False, f"first_run={first_run!r}")

    # ── B.6 老格式迁移 ─────────────────────────────────────
    write_raw(OLD_STYLE)
    got, _ = main.load_settings()
    check("B.6 ★ 老格式（明文）加载后，内存里拿到的是明文",
          got.get("api_key") == KEY, f"实到 {got.get('api_key')!r}")
    on_disk = read_raw()
    check("B.7 ★★★ 加载即迁移：盘上那份**已经变成密文**了",
          secret.is_encrypted(on_disk.get("api_key", "")),
          f"盘上还是 {on_disk.get('api_key')!r} —— 明文会永远躺着不动")
    check("B.8 ★ 迁移【只动了 Key】，其余设置一个都没丢",
          {k: on_disk.get(k) for k in
           ("kb_policy", "hotkey", "system_prompt", "seen_notice",
            "region", "auto_settle_sec")}
          == {k: OLD_STYLE[k] for k in
              ("kb_policy", "hotkey", "system_prompt", "seen_notice",
               "region", "auto_settle_sec")},
          f"迁移后是 {on_disk!r}")

    # ── B.9 往返幂等 ───────────────────────────────────────
    #   存 → 读 → 存 → 读，两轮之后还得是同一把。
    #   （钉的是「迁移完再存一次会不会把密文又当明文加一层」。）
    a, _ = main.load_settings()
    main.save_settings(a)
    b, _ = main.load_settings()
    main.save_settings(b)
    c, _ = main.load_settings()
    check("B.9 ★ 存→读→存→读 两轮后 Key 还是同一把（迁移幂等）",
          c.get("api_key") == KEY, f"实到 {c.get('api_key')!r}")

    # ── B.10 不留临时文件 ──────────────────────────────────
    #   原子写（`.tmp` + os.replace）是**为了 Key 不半途而废**：
    #   直接开原文件覆盖写，写到一半断电 = 那把唯一的 Key 没了。
    check("B.10 ★ 存盘没留下 .tmp（原子写收干净了）",
          not os.path.exists(TMP_SETTINGS + ".tmp"))

    # ── B.11 空 Key ────────────────────────────────────────
    main.save_settings({"api_key": "", "kb_policy": "repeat"})
    check("B.11 空 Key 就存空串，不写成一个空密文",
          read_raw().get("api_key") == "",
          f"盘上是 {read_raw().get('api_key')!r}")

    # ── B.12 解不开的密文：不许连累其余设置 ────────────────
    #   ★★ 这条钉的是「别让一把 Key 把整份设置带走」。
    #      原来那个大 `try: ... except: return DEFAULT_SETTINGS`
    #      会把解密失败吞成「整份设置读不出来 → 全用默认值」——
    #      用户会连框选区域、知识库策略、快捷键一起丢，还不知道发生过什么。
    write_raw({"api_key": "dpapi:v1:YWJjZGVmZ2hpamtsbW5vcA==",
               "kb_policy": "whitelist", "region": [1, 2, 3, 4]})
    got, first_run = main.load_settings()
    check("B.12 ★★ 密文解不开时：api_key 变空，但【其余设置原样还在】",
          got.get("api_key") == "" and got.get("kb_policy") == "whitelist"
          and got.get("region") == [1, 2, 3, 4],
          f"实到 api_key={got.get('api_key')!r} kb_policy={got.get('kb_policy')!r} "
          f"region={got.get('region')!r}")
    check("B.13 解不开也不算「首次运行」（文件是在的）",
          first_run is False, f"first_run={first_run!r}")

    # ── B.14 坏 JSON：原行为照旧 ───────────────────────────
    #   这条是**钉住没被这次改动带跑**的既有行为（不是新功能）。
    with open(TMP_SETTINGS, "w", encoding="utf-8") as f:
        f.write("{这不是 JSON")
    got, first_run = main.load_settings()
    check("B.14 文件不是合法 JSON 时，照旧回落到默认值 + 首次运行",
          got.get("kb_policy") == "dedupe" and first_run is True
          and got.get("api_key") == "",
          f"first_run={first_run!r} kb_policy={got.get('kb_policy')!r}")

    # ── B.15 ★ 反向对照：把 seal 打瘸，盘上就应该真的出现明文 ──
    #   ★★★ 这条是给 B.1 配的**变体**（`CLAUDE.md`：「新加一条断言跑绿不算数 ——
    #       给它配的变体不红，它就是装饰品」）。
    #       桩掉 `secret._HAS` 是**打折 DPAPI 的腿**，模拟「这台机器上加不了密」。
    #       它证明了两件事：降级路径真的会降级，而且 B.1 那条判据**够得着**。
    _saved = secret._HAS
    try:
        secret._HAS = False
        main.save_settings({"api_key": KEY, "kb_policy": "dedupe"})
        txt = read_bytes().decode("utf-8")
        check("B.15 ★★ 桩掉 DPAPI 后，Key 真的以【明文】落盘（降级是真的降级）",
              KEY in txt,
              "桩了还是密文 —— 那说明 save_settings 没走 secret.seal，"
              "B.1 那条判据可能是装饰品")
    finally:
        secret._HAS = _saved

    # ── B.16 打回原样后，又该是密文了 ──────────────────────
    main.save_settings({"api_key": KEY, "kb_policy": "dedupe"})
    check("B.16 把 DPAPI 还原回去之后，落盘又变回密文",
          secret.is_encrypted(read_raw().get("api_key", "")),
          f"盘上是 {read_raw().get('api_key')!r}")


# ══════════════════════════════════════════════════════════
# C. 环境与路径
# ══════════════════════════════════════════════════════════

def t_env():
    print("\n▸ C. 路径与本脚本自己守的规矩")
    import main

    check("C.1 settings_path() 仍认 WENLEMA_SETTINGS",
          main.settings_path() == TMP_SETTINGS,
          f"实到 {main.settings_path()!r}")

    # ★ 「不碰用户数据」是承诺，核过才算机制（附录 J 的教训）。
    #   这里只看一件事：**本脚本用到的路径不是那个真文件。**
    check("C.2 ★ 本脚本读写的是临时文件，不是用户的 app\\settings.json",
          os.path.normcase(main.settings_path())
          != os.path.normcase(REAL_SETTINGS),
          f"{main.settings_path()!r}")

    check("C.3 源码层：settings.json 仍在 .gitignore 里（真 Key 不会入库）",
          _gitignored(), "git check-ignore 没命中 —— 真 Key 会被提交")


def _gitignored():
    """`git check-ignore` 判 `app/settings.json` 有没有被忽略。

    ⚠ 这一条是**读仓库状态**，不是读产品 —— 它红了不代表产品坏了，
      代表**仓库的危险面变了**，照样得当场停下来看。
    """
    import subprocess
    try:
        p = subprocess.run(["git", "check-ignore", "-v", "app/settings.json"],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
        return p.returncode == 0
    except Exception:
        return False


# ══════════════════════════════════════════════════════════

def main_():
    print("═" * 62)
    print("问了吗 · 密钥落盘联调（DPAPI）")
    print("═" * 62)
    print(f"临时 settings：{TMP_SETTINGS}")
    if not secret.available():
        print("⚠ 这台机器上 DPAPI 不可用 —— 下面的加密用例会走降级路径。")

    # ★ 先验尺子：`check()` 是这 30 项断言的唯一出口。
    # ⚠️ 摆在任何 try 之外（老守门员那条规矩）。
    if not 尺子.gate(check, *尺子.list_snapshot(PASS, FAIL), label="密钥落盘联调"):
        return 2

    try:
        t_seal()
        t_wiring()
        t_env()
    finally:
        cleanup()
        if _OLD_ENV is None:
            os.environ.pop("WENLEMA_SETTINGS", None)
        else:
            os.environ["WENLEMA_SETTINGS"] = _OLD_ENV

    print("\n" + "═" * 62)
    print(f"通过 {len(PASS)} 项　失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败明细：")
        for name, detail in FAIL:
            print(f"  · {name}　→ {detail}")

    print("""
⚠ 本文件【一条测不了的】，别当成测过了：
  · 「DPAPI 在别的机器/用户上解不开」—— 那正是这套方案的代价
    （绑死这台机器 + 这个用户）。要真验得换机器或换 Windows 用户。
    能验的是它的下游形状（B.12：解不开不许连累其余设置、不许静默）。
""")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
