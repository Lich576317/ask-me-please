# -*- coding: utf-8 -*-
"""「问了吗」启动入口。

当前进度：三段式管线【已合龙】——
          截屏 → OCR → ① 规则初筛 → ③ 知识库过滤 → ② AI 筛选并写解释 → 贴侧栏。
          自动/手动通道尚未接入。

⚠ 注意上面那个顺序：③ 排在 ② 前面，不是 ①②③。
  「库里已经解释过的词不再问模型」就是靠这个顺序省钱的。
  CLAUDE.md 硬约束 3 写的是三段的分工，不是调用次序。

跑起来：
    python "Desktop\\问了吗\\app\\main.py"
"""
import os
import sys
import json
import time
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# stdout 被重定向到文件/管道时，Python 默认用【本地编码 GBK】，
# 而这里打印的东西可能来自 OCR 结果或前端回传 —— 只要有一个 GBK 编不出的字符，
# print 就抛 UnicodeEncodeError，还是在 js_api 线程里。统一成 UTF-8，编不出的也不许炸。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import ai                                                        # noqa: E402
import auto as auto_mod                                          # noqa: E402
import capture                                                   # noqa: E402
import hotkey as hotkey_mod                                      # noqa: E402
import kb                                                        # noqa: E402
import ocr                                                       # noqa: E402
import pipeline                                                  # noqa: E402
import prompt as prompt_mod                                      # noqa: E402
import secret                                                    # noqa: E402
import shell as shell_mod                                        # noqa: E402
from shell import Shell, TITLE, PANEL_W                          # noqa: E402

def settings_path():
    """settings.json 的路径。

    ⚠ WENLEMA_SETTINGS 是【留给联调脚本的接口】，和 `kb.kb_path()` 的 WENLEMA_KB、
    ai.py 的 WENLEMA_BASE_URL 一个性质 —— 正常启动时它不存在，
    行为与写死路径完全一致，不是给用户的功能开关。

    为什么非要有它（2026-09-15 真出过事）：
    四个开窗脚本原来走的是「备份 → 跑 → 还原」。那是个【承诺】，不是机制。
    那天 287 项跑了个全绿，用户的真 Key 却没了 —— settings.json 停在脚本
    播下去的假 Key（`sk-test-fake`）上。之后单个跑、按顺序连着跑、
    再跑一遍 `全部回归.py` 都没能复现，所以到底是哪个进程在「还原」之后
    又把内存里那份假设置写了回去，至今没查出来。
    查不出来也没关系：只要脚本把 app 指到一个临时文件上，
    真的 settings.json 从头到尾【没被打开过】，就不存在「还原失败」这回事。
    """
    return os.environ.get("WENLEMA_SETTINGS") or os.path.join(HERE, "settings.json")


WEB_DIR = os.path.join(HERE, "web")
WEB = os.path.join(WEB_DIR, "index.html")

SEL_TITLE = "问了吗 · 框选"
SHOT_FULL = os.path.join(WEB_DIR, "_shot.png")     # 框选底图（用完即删）
SHOT_REGION = os.path.join(HERE, "_region.png")    # 待识别的区域图

# ⚠ 覆盖层窗口的引用放在【模块级】，不挂在 Api 实例上。
#   原因见 Api.__init__ 的说明 —— 挂在 Api 上会触发 pywebview 的无限递归。
_OVERLAY = {}


def _clear_shots():
    """把落盘的截图删掉（决策 33：截图用完即删）。

    ⚠ 这两张都不是「程序自己的临时文件」：
      `_shot.png` 是框选底图，里面是【用户整个桌面】；
      `_region.png` 是待识别那块，里面是用户的笔记。
      留下任何一张，都是用户的东西留在了盘上，而且界面上不会有任何提示。

    谁调用：
      · **启动时** —— 上一次要是没收拾干净（被强杀、或者覆盖层没关掉就退了），
        底图会留在盘上，而用户正常情况下不会再点一次「框选区域」，
        它就永远躺在那儿了。真发生过：接了第二块屏之后覆盖层开在副屏上，
        拖框却落在主屏 —— 覆盖层收不到鼠标抬起，一直没关，
        一张整个桌面的截图留了一整天，直到下次跑回归才被发现。
      · **关窗时** —— `os._exit(0)` 会跳过【所有】finally，
        所以这些删除必须在退出前【显式】做完，不能指望 finally。

    ⚠ 开机就删有个边角情况：同时开着两个实例时，后起的那个会把前一个
      正在用的底图删掉。这个风险【原来就有】—— `pick_region()` 一开始
      也是无条件删 `_shot.png`；同时开两个实例本身就不被支持
      （面板会叠在同一块矩形上）。所以这里没有引入新的失败模式。
    """
    for p in (SHOT_FULL, SHOT_REGION):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

DEFAULT_SETTINGS = {
    "api_key": "",
    "kb_policy": "dedupe",        # dedupe | repeat | whitelist
    "kb_dir": "",                 # 导出目录。空 = 用 kb.default_dir()
    "system_prompt": "",          # 空 = 用默认（框选/自动通道，只认英文）
    "system_prompt_any": "",      # 空 = 用默认（快捷键通道，中英不限）。v0.20
    "hotkey": hotkey_mod.DEFAULT_COMBO,   # 显示写法，如 "ctrl+e"。v0.20
    "seen_notice": False,
    "region": None,               # [left, top, width, height] 物理坐标
    "auto_on": False,             # 自动通道总开关（决策 2 / 16，语义见决策 45）
    "auto_settle_sec": 15,        # 画面停住多少秒算「在读这一屏」（5/15/30/60）
}
# ⚠ 往这里加键【不需要】写迁移：load_settings() 是先 merged = dict(DEFAULT_SETTINGS)
#   再 merged.update(文件内容)，老 settings.json 缺的键自动补上默认值。
#   但 save_settings() 是【整份覆盖】，所以新键必须真的在 self.settings 里
#   （load 已经保证了），否则第一次存盘就把它丢了。


def load_settings():
    first_run = not os.path.exists(settings_path())
    if first_run:
        return dict(DEFAULT_SETTINGS), True
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
    except Exception:
        # ⚠️ 整个文件读不出来（不存在 / 不是 JSON）才算「从头来过」。
        #    ★ 一把 Key 解不开**不归这里管** —— 见下面。这两件事原来混在
        #      同一个 try 里，于是「Key 解不开」会被吞成「整份设置读不出来
        #      → 全用默认值」：用户会连框选区域、知识库策略、快捷键一起丢，
        #      而且不知道为什么。**「读不出来」不该长得像「本来就没有」。**
        return dict(DEFAULT_SETTINGS), True

    # ══════════ API Key：单独处理，四个状态都要说清 ══════════
    # （落盘格式与状态语义的唯一真相来源是 `app/secret.py` 的文件头。）
    raw_key = merged.get("api_key") or ""
    key, status = secret.unseal(raw_key)
    merged["api_key"] = key

    if status == "bad":
        # ⚠️ **这里必须出声。** 悄悄给一个空 Key 的话，用户看到的是
        #    「Key 没填」，而真正的原因是「换了机器/用户，解不开了」——
        #    两件事的下一步动作完全不同（一个是去填，一个是去重填）。
        print("[设置] ⚠ 存着的那把 API Key 【解不开】了 —— 它只在这台机器、"
              "这个 Windows 用户下能用。请在操作台重新填一次。", flush=True)
    elif status == "plain" and key:
        # ★ 老格式（明文）→ 加载时就地迁移成密文。
        #   不做这一步的话，用户盘上那份明文会**永远**躺着不动 ——
        #   他没有理由再去动一次 Key，也就永远等不到第一次 save。
        # ⚠️ 迁移的安全性靠两件事，都在 `secret.seal()` / `save_settings()` 里：
        #    (1) seal() 加完密**当场读回来**，读不回来就宁可存明文；
        #    (2) 存盘走 `.tmp` + `os.replace()`，**原子**替换 ——
        #        直接开原文件覆盖写，写到一半断电 = 那把唯一的 Key 没了。
        try:
            save_settings(merged)
            print("[设置] API Key 已从【明文】就地改成 DPAPI 密文"
                  "（原来的明文已被覆盖）", flush=True)
        except Exception as e:
            print(f"[设置] ⚠ 想把明文改成密文但没成功，文件里还是明文："
                  f"{type(e).__name__}", flush=True)

    return merged, False


def save_settings(data):
    """存盘。**API Key 在这里变成密文**（DPAPI，见 `app/secret.py`）。

    ⚠️ 两条不许动的：
      1. **Key 必须先过 `secret.seal()`**。`seal()` 自己会验一遍能不能读回来，
         验不过就返回明文 —— 宁可明文也不要写一个解不开的串。
         判据是 `secret.is_encrypted(返回值)`，所以这里能看出降级了没有。
      2. **走 `.tmp` + `os.replace()`，不直接覆盖**。这个文件里有这个项目
         唯一一份「丢了就没了」的数据（用户的 API Key），
         直接覆盖写 = 写到一半出任何事都是永久损失。
    """
    path = settings_path()
    payload = dict(data)
    plain = (data.get("api_key") or "").strip()
    payload["api_key"] = secret.seal(plain)
    if plain and not secret.is_encrypted(payload["api_key"]):
        # 只在这把 Key **真的没被加密**时才出声 —— 不打字的话，
        # 「落盘是明文」这件事没有任何人会发现（同 `secret.seal` 的说明）。
        print("[设置] ⚠ 这把 API Key 只能以【明文】落盘（DPAPI 不可用）——"
              "换机器或换 Windows 用户时它反而能跟着走，但这台机器上"
              "任何能读这个文件的程序都读得到它。", flush=True)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)          # 原子：要么旧的、要么新的，没有中间态


class Api:
    """暴露给前端的接口。JS 侧通过 window.pywebview.api.xxx() 调用。"""

    def __init__(self, sh):
        self.sh = sh
        # ⚠ 这里【不要】存 pywebview 的 Window 对象。
        #   它内部通向 .NET 的 WinForms Form，pywebview 反射时会顺着
        #   window.native.AccessibilityObject.Bounds.Empty.Empty.Empty… 无限递归，
        #   后果是 JS 侧整个方法列表都建不出来（报 xxx is not a function），
        #   日志还会涨到上百 MB 把进程拖死。
        #   要用窗口时现取：import webview; webview.windows[0].destroy()
        self.settings, self.first_run = load_settings()

        # 启动就把库开起来。schema 有问题要【现在】炸，
        # 而不是等用户读到一半点「导出」才炸 —— 那种失败最难往数据库上想。
        kb._connect()

        # 最近一轮 ③ 判定「要交给 ②」的候选（带 key），**按通道分槽**。
        # explain() 靠它把模型的回答认领回候选。见 Api.explain 的说明。
        #
        # ⚠ v0.20 从单槽改成按通道分槽（decision 50）。理由:
        #   单槽时两条通道会互相污染，而且【两个方向都错】——
        #     · clip 污染 region：框选那轮 recognize 之后、explain 之前
        #       按一下 Ctrl+E，_last_todo 就被中文那批覆盖了，
        #       框选的 explain 于是认领到错的 key。
        #     · region 污染 clip：反过来 clip 会捡到上一轮框选的候选。
        #   眼下的保护是前端那把 running 锁（同一时刻只跑一条管线），
        #   但那是一道【跨语言的】不变量 —— 用 JS 里一个变量来保证
        #   Python 这边一个字段的正确性。分槽之后它变成局部的、
        #   在本文件里就看得明白的事，不再依赖前端是否守规矩。
        #   见 进展文档 里「单槽竞争」那条 —— 那次记的是「本轮不动它」，
        #   v0.20 多了一条通道，所以动了。
        self._last_todo = {"region": None, "clip": None}

        # ⚠ 自动通道来了之后 recognize() 就【真的会】并发了，这里有两处单槽状态：
        #     · _last_todo 是单槽 —— 两轮撞上，后到者覆盖，先到者的解释会认领错的 key
        #     · SHOT_REGION 是【固定单一路径】—— ocr._lock 只护引擎不护文件，
        #       两张图互相覆盖，后到者会把先到者正在 OCR 的那张删掉
        #   所以 recognize() 全程持锁，把它变成原子的。
        #
        #   注意这把锁的边界：它管不到「A 的 recognize → B 的 recognize → A 的 explain」
        #   这种交错（recognize 和 explain 是前端两次独立调用）。
        #   真正保证「同一时刻只有一条管线」的是前端那把 running 锁，
        #   这里挡的是前面那两类【静默】损坏 —— 它们不报错，只是结果不对。
        self._run_lock = threading.RLock()

        # 自动通道：判决器 + 一条 daemon 判决线程。
        # 线程每 2 秒醒一次，开关关着时第一个判断就返回了，不截屏也不比画面。
        # 所以不做「开关打开时才起线程」那套生命周期管理 —— 少一处能出错的地方。
        #
        # ⚠ 三个 getter 都是【每 tick 现读 self.settings】的拉取式。
        #   别把阈值算好了传进来 —— 那会冻结在构造那一刻，操作台改了不生效。
        self.auto = auto_mod.Watcher(
            rect_getter=lambda: self.settings.get("region"),
            notice_getter=lambda: bool(self.settings.get("seen_notice")),
            settle_getter=lambda: auto_mod.resolve_settle(
                self.settings.get("auto_settle_sec")),
        )
        self.auto.set_on(self.settings.get("auto_on", False))
        auto_mod.start(self.auto)

        # 快捷键手动通道（v0.20 / 决策 50）—— 与自动通道并列的第二条触发源。
        #
        # ⚠ 它【只取词、不跑管线】：热键线程把用户选中的文字弄到手就置个标记，
        #   前端每 500ms 来 poll 一次，拿到后去跑它自己那条 runPipeline()。
        #   硬约束 4 要求两条通道共用同一条管线，这里自己再跑一遍就是第二份实现。
        #   见 app/hotkey.py 开头的说明。
        self.hotkey = hotkey_mod.start(
            self.settings.get("hotkey") or hotkey_mod.DEFAULT_COMBO)

        # 联调后门：启动时假装「用户刚选中了这段字」。
        # ⚠ 同 WENLEMA_BASE_URL / WENLEMA_KB / WENLEMA_AUTO_SETTLE —— 是留给
        #   联调脚本的接口，**不是给用户的功能开关**：正常启动时它不存在。
        #   存在的理由见 hotkey.Hotkey.seed()：pynput 刻意忽略注入按键，
        #   「按快捷键真的响」那一跳没法自动化验，但后面全条都能验。
        _seed = (os.environ.get("WENLEMA_CLIP_TEXT") or "").strip()
        if _seed:
            self.hotkey.seed(_seed)

    # ══════════ 窗口 ══════════
    def dock(self, edge):
        ok = self.sh.dock(edge)
        return {"ok": ok, "edge": self.sh.edge, **self.diag()}

    def collapse(self):
        self.sh.collapse()
        return self.diag()

    def close_app(self):
        """关掉程序。

        ⚠ 最后那句 os._exit(0) 不能删，它是修 v0.7 就带着的一个 bug：

          pywebview 每次 js_api 调用都新开一条【非守护线程】
          （webview/util.py:335 `thread = Thread(target=_call)`，整个文件里
          一个 daemon 都没有）。而 explain() 最长会阻塞 60 秒。
          w.destroy() 只是把窗口销毁掉，main() 返回之后解释器还要 join
          这些线程 —— 现象就是【面板已经消失了，python 进程还挂在任务管理器里
          几十秒】。以后接了文件夹对话框，卡住的线程会让它永远退不掉。

          os._exit 跳过清理直接退出。清理该做的事在这里显式做完：
          kb.close() 让 WAL 落盘（虽然 SQLite 自己也能恢复，但没必要赌）。
        """
        import webview
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass
        try:
            kb.close()
        except Exception:
            pass

        # 摘掉全局键盘钩子。这里不摘也能退出（pynput 的 Listener 是 daemon，
        # 且 os._exit 根本不走清理），但**钩子是系统级的** ——
        # 让它挂到一个已经死掉的进程上不是「反正要退了」那么轻的事。
        # 这是 os._exit(0) 之前【唯一】还有机会做事的地方，加在这里。
        try:
            self.hotkey.suspend()
        except Exception:
            pass

        # ⚠ os._exit 跳过【所有】finally —— 包括 recognize() 里那个删图的 finally。
        #   它要是恰好落在「区域图还在盘上」那一小段，用户的笔记就留在磁盘上了
        #   （决策 33：截图用完即删）。所以临走前显式再删一次。
        #   自动通道每 2 秒截一帧走的是内存，不留文件，这里管的是手动那条路的那张。
        #
        # ⚠ 底图 `_shot.png` 也要一起删 —— 以前这里只管区域图，是因为
        #   「覆盖层总会被关掉、关了就会删」。可关窗的时候覆盖层很可能
        #   正开着（用户点开框选，然后从任务栏把程序关了），
        #   那就等于把一张【整个桌面】的截图留在盘上了。
        _clear_shots()

        os._exit(0)

    def report(self, tag, detail=""):
        """前端把自身状态回传给后端打印。

        这个窗口的 devtools 不是随手能开的，出问题时全靠这条。
        方向是「前端说」而不是「后端猜」，也不碰 GUI 线程，比 evaluate_js 稳。

        ⚠ 这是【诊断通道】，它自己绝不能把程序带崩 ——
          detail 是从前端原样传过来的，什么字符都可能有。
        """
        try:
            print(f"[前端] {tag}: {detail}", flush=True)
        except Exception:
            pass
        return True

    def diag(self):
        r = self.sh.current_rect()
        ml, mt, mr, mb = self.sh.monitor_rect()
        return {
            "edge": self.sh.edge,
            "collapsed": self.sh.collapsed,
            "rect": [r[0], r[1], r[2], r[3]],
            "size": [r[2] - r[0], r[3] - r[1]],
            "monitor": [mr - ml, mb - mt],
            "topmost": self.sh.is_topmost(),
            "frameless": self.sh.is_frameless(),
        }

    # ══════════ 设置 ══════════
    def get_settings(self):
        s = dict(self.settings)
        s["api_key_set"] = bool(s.pop("api_key", ""))
        s["first_run"] = self.first_run
        # 默认提示词住在后端（prompt.py），前端只负责显示。
        # 两份各写一遍的话，改了一边忘了另一边，界面上显示的和实际用的就对不上了。
        s["default_prompt"] = prompt_mod.DEFAULT_PROMPT
        # 第二份默认提示词（快捷键通道用，中英不限）。两份都要给 ——
        # 前端那个「切换编辑哪一份」的控件要靠它俩把自己填满。
        s["default_prompt_any"] = prompt_mod.DEFAULT_PROMPT_ANY
        # 快捷键。★ 给的是【后端生效值】而不是 settings 里存的那个 ——
        #   注册失败时 Hotkey 会保留旧组合，前端照 settings 画就会显示一个
        #   实际没生效的键（同 markSettle 用后端回读值）。
        s["hotkey"] = self.hotkey.state()
        s["kb"] = kb.stats()
        s["kb_dir_effective"] = s.get("kb_dir") or kb.default_dir()
        # ★ 真正生效的等待阈值也由后端说，前端【不】自己写一遍「空/非法就用 15」——
        #   「唯一实现处」是这个项目一贯的做法（同 kb.canon()）。
        #   联调时 WENLEMA_AUTO_SETTLE 会盖过它，那时这个值可能是 3，
        #   操作台四个档位一个都不亮 —— 那是【诚实的显示】，不是 bug。
        s["auto_settle_effective"] = auto_mod.resolve_settle(s.get("auto_settle_sec"))
        # 屏蔽词名单也一起给。启动时前端要把它渲染进操作台 ——
        # 不给的话，开机第一眼看到的是一份空名单（其实库里有），
        # 得先点一次「添加/恢复」才会冒出来。
        s["excluded"] = kb.list_excluded()
        return s

    def save_prompt(self, text, which="region"):
        """存一份系统提示词。which: "region"（框选/自动）| "clip"（快捷键）。

        两份是【各自独立】的，各存各的键、各跟各的默认值比。
        合并成一份的话，中文那套规则会被自动通道用上 ——
        而自动通道维持只认英文是这次改动的硬边界（决策 50）。
        """
        text = (text or "").strip()
        if which == "clip":
            dflt, key = prompt_mod.DEFAULT_PROMPT_ANY, "system_prompt_any"
        else:
            which, dflt, key = "region", prompt_mod.DEFAULT_PROMPT, "system_prompt"
        # 和默认一模一样就存空 —— 这样以后默认提示词改进了，用户能自动跟上，
        # 而不是被一份「看起来一样的旧副本」钉死在旧版本上（决策 37）。
        self.settings[key] = "" if text == dflt else text
        save_settings(self.settings)
        return {"ok": True, "which": which,
                "using_default": not self.settings[key]}

    def save_api_key(self, key):
        self.settings["api_key"] = (key or "").strip()
        save_settings(self.settings)
        return {"ok": bool(self.settings["api_key"])}

    def set_kb_policy(self, policy):
        if policy in ("dedupe", "repeat", "whitelist"):
            self.settings["kb_policy"] = policy
            save_settings(self.settings)
        return {"ok": True, "policy": self.settings["kb_policy"], "kb": kb.stats()}

    # ══════════ ③ 知识库 ══════════
    def kb_stats(self):
        return {"ok": True, "kb": kb.stats(),
                "excluded": kb.list_excluded(),
                "dir": self.settings.get("kb_dir") or kb.default_dir()}

    def kb_harvest(self):
        r = kb.harvest()
        print(f"[知识库] 收纳历史：新增 {r['added']}　更新 {r['updated']}　"
              f"跳过 {r['skipped']}", flush=True)
        return {"ok": True, **r, "kb": kb.stats()}

    def kb_export(self, scope="all"):
        if scope not in ("all", "today"):
            scope = "all"
        out_dir = self.settings.get("kb_dir") or kb.default_dir()
        r = kb.export_md(scope, out_dir)
        if r.get("ok"):
            print(f"[知识库] 导出 {r['count']} 条 -> {r['path']}", flush=True)
        else:
            print(f"[知识库] 导出失败：{r.get('msg')} {r.get('detail','')}", flush=True)
        return r

    def kb_exclude(self, term):
        """右键「不再解释该词」（决策 21）。

        ⚠ 这是个【单向门】，前端的提示里必须带「撤销」。
          用户手一滑右键一个常用词，它就从所有地方消失了 ——
          没有撤销和「已排除」列表的话，那叫数据丢失，不叫功能。
        """
        r = kb.exclude(term)
        if r.get("ok"):
            print(f"[知识库] 排除：{term} ({r['key']})", flush=True)
        r["kb"] = kb.stats()
        return r

    def kb_unexclude(self, key):
        r = kb.unexclude(key)
        r["kb"] = kb.stats()
        r["excluded"] = kb.list_excluded()
        return r

    def kb_set_dir(self, path):
        """手粘的导出目录。选择框只是一个便利，不是唯一入口 ——
        万一哪天对话框挂了，「导出 md」照样要能用。"""
        self.settings["kb_dir"] = (path or "").strip()
        save_settings(self.settings)
        return {"ok": True, "dir": self.settings["kb_dir"] or kb.default_dir()}

    def kb_choose_dir(self):
        """选导出目录。

        ⚠ **故意不用 pywebview 的 create_file_dialog(FOLDER_DIALOG)**，读源码后三个理由：
          1. platforms/winforms.py:866 —— 同文件所有其它 UI 操作
             （Hide / Minimize / Maximize / load_url）都包了 `self.Invoke(...)`，
             【只有 create_file_dialog 没包】。而 js_api 的调用来自 HTTP 工作线程，
             不是 GUI 线程
          2. 它最终落到 COM 的 IFileDialog::Show，要求调用线程能泵消息（通常还得是 STA），
             工作线程不满足。社区有「非主线程调用导致挂起且进程无法退出」的报告
          3. winforms.py:873 用 os.environ['HOMEPATH'] 当默认目录，
             本机上那是【没有盘符】的 `\\Users\\<用户名>`

        改成另起一个短命子进程跑 tkinter：它有自己主线程和自己的消息循环，
        和 WebView2 完全不共享 GUI 线程，卡死了也能 kill 掉。
        """
        import subprocess

        src = (
            "import tkinter as tk\n"
            "from tkinter import filedialog\n"
            "root = tk.Tk()\n"
            "root.withdraw()\n"
            # 面板是置顶无边框的，Tk 窗口不置顶的话会被自己的面板压住 ——
            # 症状是「点了选择，什么都没发生」
            "root.attributes('-topmost', True)\n"
            "p = filedialog.askdirectory(title='选择导出目录', mustexist=False)\n"
            "root.destroy()\n"
            "print(p or '')\n"
        )
        try:
            r = subprocess.run(
                [sys.executable, "-c", src],
                capture_output=True, text=True,
                # ⚠ 两个都要：encoding 管父进程怎么解码，
                #   PYTHONUTF8 管子进程自己怎么写 stdout。
                #   少一个，中文路径回来就是乱码（和 main.py 开头 reconfigure 同一个坑）。
                encoding="utf-8", errors="replace", timeout=180,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "对话框等了 3 分钟没动静，已放弃"}
        except Exception as e:
            return {"ok": False, "msg": "打不开选择框", "detail": str(e)}

        if r.returncode != 0:
            return {"ok": False, "msg": "选择框自己出错了",
                    "detail": (r.stderr or "")[-300:]}

        path = (r.stdout or "").strip()
        if not path:
            return {"ok": True, "cancelled": True,
                    "dir": self.settings.get("kb_dir") or kb.default_dir()}

        self.settings["kb_dir"] = path
        save_settings(self.settings)
        return {"ok": True, "dir": path}

    def dismiss_notice(self):
        self.settings["seen_notice"] = True
        save_settings(self.settings)
        return {"ok": True}

    def test_connection(self):
        return ai.check_key(self.settings.get("api_key"))

    # ══════════ ② AI 调用 ══════════
    def _system_prompt(self, mode):
        """这条通道该用哪份系统提示词。**唯一实现处**，别在别处再判一次。

        region（框选/自动）→ 只认英文那份；clip（快捷键）→ 中英不限那份。
        两份都可以被用户在操作台改，改过的存在 settings 里；
        **为空 = 用默认**（同决策 37 的语义）。
        """
        if mode == "clip":
            return (self.settings.get("system_prompt_any")
                    or prompt_mod.DEFAULT_PROMPT_ANY)
        return (self.settings.get("system_prompt")
                or prompt_mod.DEFAULT_PROMPT)

    def explain(self, payload, mode="region"):
        """把 ① 的产物交给 DeepSeek，拿回 [{术语, 解释}]。

        ⚠ 这里会阻塞几秒到几十秒。它是 js_api 线程，不占 GUI 线程，
          但**前端必须自己给出「正在问模型」的进度**，否则用户不知道还要等多久。

        mode: "region"（框选 / 自动通道）| "clip"（快捷键取词通道）。
        clip 有两处【故意】和 region 不一样，下面都点了名。
        """
        payload = payload or {}
        mode = mode if mode in ("region", "clip") else "region"

        # 要问的那批候选。优先用最近一轮 ③ 判定的结果（里面带 key）；
        # 拿不到就退回按候选词现算 —— 前端可能重发了一份载荷。
        #
        # ⚠ 只读【自己这条通道】那一槽（决策 50）。读错槽不会报错，
        #   只会拿另一条通道的候选去认领，key 挂到不相干的词上。
        cands = self._last_todo.get(mode)
        if cands is None:
            cands = [{"term": t, "key": kb.canon(t)} for t in (payload.get("候选词") or [])]

        if not cands and mode == "clip" and self.settings.get("kb_policy") != "whitelist":
            # ★ clip 通道【故意绕过】下面那道空候选闸门（决策 50，破例二）。
            #
            #   那道闸门在 region 上是对的，理由一字未改地写在 elif 里。
            #   但 clip 的用户选中一段纯中文时，① （只认英文字母的规则初筛）
            #   必然一个词都给不出来 —— 那不是「全被知识库回答了」，
            #   是「这一轮本来就没有候选」。中文术语只能由 ② 从原文里挑，
            #   这正是这条通道存在的意义（用户拍板：解释对象仍是术语，中英都认）。
            #   在这里拦住它 = 用户按了快捷键，面板上什么都不出现。
            print("[管线·取词] 无规则候选，交给模型直接从原文里挑术语", flush=True)
        elif not cands and mode == "clip":
            # ⚠⚠ 白名单模式下 clip【收回】这个破例（2026-09-21，决策 52）。
            #
            #   上面那段放行的理由是「中文只能靠 ② 从原文里挑」，而白名单的
            #   契约恰恰是「不在库里的词一概不显示」（设置页那行说明原文）——
            #   ② 挑出来的词【必然不在库里】，所以放行 = 当着用户的面破掉
            #   产品自己写的那条规矩。前端 runPipeline 里同一道闸门同时收。
            #
            #   代价说清楚：白名单 + 中文取词【结构上】永远不会命中 ——
            #   ① 的规则初筛只认英文字母，中文给不出候选，而它没有本地词表。
            #   所以这里不是「这次没捞到」，是「这条路走不通」，
            #   返回的话要和「全被知识库回答了」区分开，别让用户以为换个词就行。
            print("[管线·取词] 白名单模式下不绕空候选闸门（库外词一概不显示）",
                  flush=True)
            return {"ok": True, "entries": [], "seconds": 0, "usage": {},
                    "skipped": "白名单模式下没有库内命中"}
        elif not cands:
            # 全被知识库回答了。这时候【必须】不调模型 ——
            # 给模型发一个空候选列表不光白烧 token，
            # 它还可能自己从原文里编出几条不属于候选的词来。
            return {"ok": True, "entries": [], "seconds": 0,
                    "usage": {}, "skipped": "没有需要问模型的词"}

        r = ai.explain(
            self.settings.get("api_key"),
            self._system_prompt(mode),
            payload,
            mode,
        )
        if not r.get("ok"):
            print(f"[AI] 失败：{r.get('msg')}　{r.get('detail', '')[:200]}", flush=True)
            return r

        # ★ 把模型的回答认领回候选，key 从【候选】派生而不是从模型返回值派生。
        #   否则模型这轮写 Chunk、下轮写 Chunking，知识库就永远命不中。
        policy = self.settings.get("kb_policy", "dedupe")
        r["entries"] = kb.attach_keys(r["entries"], cands)

        # ⚠ 排除判定要【再过一遍 ② 的产物】。③（filter_candidates）只看得到 ① 的候选表，
        #   而词组/整句是 ② 从原文里拎出来的整条 —— 不在候选表里，③ 永远拦不住。
        #   不跑这一遍的话，右键「不再解释该词」对这些整条就是静默失效：
        #   toast 照样说「已不再解释」，下一轮它照常回来。
        before = len(r["entries"])
        r["entries"] = kb.drop_excluded(r["entries"])
        if len(r["entries"]) < before:
            print(f"[AI] 排除名单挡掉 {before - len(r['entries'])} 条", flush=True)

        n = kb.record_entries(r["entries"], policy)

        u = r.get("usage") or {}
        print(f"[AI] {len(r['entries'])} 条　{r['seconds']}s　"
              f"tokens {u.get('in')}+{u.get('out')}={u.get('total')}　"
              f"入库 {n} 条（{policy}）", flush=True)
        return r

    # ══════════ 自动通道 ══════════
    # 后端只负责「到点了」；真正跑管线的是【前端】，调的是它自己那个
    # runPipeline()。硬约束 4 要求自动与手动共用整条管线 ——
    # 后端再实现一遍 recognize→explain 就是第二份，两段式渲染、三桶、
    # bucketize 的顺序全要跟着抄，迟早分叉。见 app/auto.py 开头。
    def auto_set(self, on):
        """自动通道总开关。"""
        on = bool(on)
        self.settings["auto_on"] = on
        save_settings(self.settings)
        self.auto.set_on(on)
        # 立刻 tick 一次，别让界面停在上一个状态等最多 2 秒。
        # 顺带这也是「打开开关时重新计时」的落点：set_on(True) 之前
        # 一定是关着的，关闭时 _reset_tracking() 已经把指纹清成 None，
        # 于是这一 tick 只会记下画面、把计时归零，不会当场触发。
        self.auto.tick_once()
        print(f"[自动] {'开' if on else '关'}", flush=True)
        return {"ok": True, "on": on}

    def set_auto_settle(self, sec):
        """改「画面停住多少秒算在读这一屏」。

        ⚠ 白名单，不收任意数字。理由不是洁癖：这个值直接乘进「跑多少次管线」，
          用户手滑填个 0 会让它每一 tick 都触发 —— 一次误填就是一条烧钱的循环。
          能选的档位就是操作台上那四个。
        """
        try:
            sec = int(sec)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad_value"}
        if sec not in (5, 15, 30, 60):
            return {"ok": False, "reason": "not_in_whitelist"}
        self.settings["auto_settle_sec"] = sec
        save_settings(self.settings)
        # 不用通知判决线程：它的 settle 是每 tick 现读的（见 app/auto.py）。
        # 这里 tick 一次只是让界面别停在上一个状态等最多 2 秒。
        self.auto.tick_once()
        print(f"[自动] 等待阈值改为 {sec}s（实际生效 {self.auto.settle:g}s）", flush=True)
        return {"ok": True, "sec": sec,
                "effective": auto_mod.resolve_settle(sec)}

    def auto_poll(self):
        """前端每 2 秒问一次「到点了吗」。

        ⚠ 这里【不持锁、不做重活】。pywebview 每次 js_api 调用都新开一条
          【非守护线程】—— 这个方法要是阻塞在上面那把 _run_lock 上，
          就会攒出一堆卡住的非守护线程，正好把 os._exit(0) 要绕开的问题
          原样搬回来。它只读几个标量字段。
        """
        return self.auto.poll()

    def auto_report(self, state):
        """前端汇报一次运行的生命周期：start / done / fail。

        done / fail 会顺手钉上「这一屏已经跑过了」——
        手动跑完也要报，否则用户刚点完「立即识别」，
        自动通道立刻对同一屏再跑一遍，白花一次钱。
        """
        self.auto.report(state)
        return {"ok": True, "state": state}

    # ══════════ 快捷键手动通道（v0.20 / 决策 50）══════════
    def hotkey_poll(self):
        """前端每 500ms 来一次「有没有人按了快捷键」。

        ⚠ 同 auto_poll：**不持锁、不做重活**。pywebview 每次 js_api 调用都新开
          一条非守护线程，这里阻塞就会攒出一堆挂着的线程，
          正好把 os._exit(0) 要绕开的那个 bug 原样搬回来。
        ⚠ 它是【只读不消费】的 —— 取走得调 hotkey_ack(seq)，理由见 hotkey.poll()。
        """
        return self.hotkey.poll()

    def hotkey_ack(self, seq):
        """前端说「第 seq 条我用上了」。"""
        return self.hotkey.ack(seq)

    def hotkey_suspend(self):
        """录制改键前挂起热键。

        ★ 不挂的话，用户在录制界面按下的那一下会真的触发一次取词：
          半秒后冒出「没抓到文字」，同时那串键还被前台程序照常收走。
        ★ 它【只是运行时状态，绝不落盘】—— 落了盘的话，「录制中崩了」
          会把「没有快捷键」这个状态持久化，用户重启后发现功能凭空消失。
        """
        return self.hotkey.suspend()

    def hotkey_resume(self):
        return self.hotkey.resume()

    def set_hotkey(self, combo):
        """改快捷键。校验不过就【保持旧的不动】。"""
        r = self.hotkey.set_combo(combo)
        if r.get("ok"):
            self.settings["hotkey"] = r["combo"]
            save_settings(self.settings)
            print(f"[快捷键] 改为 {r['combo']}", flush=True)     # ★ click_until 的判据
            return r
        # ⚠ 失败时也要把【当前生效值】带回去，前端用它重画。
        #   不带的话前端只能拿自己输的那个画，界面上会显示一个没生效的键。
        print(f"[快捷键] 没改成：{r.get('reason')}", flush=True)
        return {**r, "combo": self.hotkey.state()["combo"]}

    # ══════════ 框选区域 ══════════
    def get_region(self):
        """主窗口轮询用：框选是【覆盖层】那个窗口干的活，
        干完把结果写进 settings，主窗口这边并不知道。
        它靠这个方法回读，回读到就更新卡片。
        """
        return {"region": self.settings.get("region")}

    def region_info(self):
        """覆盖层问：我铺在哪块屏上、多大。

        ⚠ 必须是【覆盖层实际所在的那块】，不能是主屏。覆盖层拿这两个数
          把像素坐标折算成 0~1（`select.html` 的 paint），报错了尺寸，
          框选结果就会整体缩放错位 —— 而存下来的数字看着仍然很正常。
        """
        ml, mt, mw, mh = _OVERLAY.get("mon") or capture.primary_rect()
        return {"monitor": [mw, mh], "at": [ml, mt]}

    def pick_region(self):
        """截全屏当底图 → 开覆盖层让用户框选。

        ★ **哪一块屏：鼠标在的那块**（决策 10）。

          以前固定用 `mss.monitors[1]`，接了第二块屏之后那正好是【副屏】——
          面板停在主屏上，覆盖层却开到副屏去，用户点了「框选区域」，
          自己这块屏幕上什么变化都没有，看着像按钮坏了。
          （本机实测 2026-09-15：monitors[1]=DISPLAY1 副屏，真正的主屏是 monitors[2]。）

          这块屏的 rect 从这里定下，然后**一路传到底**：底图截的是它、
          覆盖层铺在它上面、set_region 拿同一个 rect 换算回来。
          中途换一块，框就落到别的屏上去了。
        """
        import webview

        self._close_overlay()               # 上一张还开着的话，先收掉

        mon = capture.cursor_monitor_rect()

        # 底图每张都是新的，旧的一律先删掉（里面是用户整个桌面）
        try:
            if os.path.exists(SHOT_FULL):
                os.remove(SHOT_FULL)
        except Exception:
            pass

        size = capture.grab_full(SHOT_FULL, monitor=mon)

        w = webview.create_window(
            SEL_TITLE,
            url=os.path.join(WEB_DIR, "select.html"),
            js_api=self,
            frameless=True, on_top=True, easy_drag=False, resizable=False,
            width=size[0], height=size[1], x=mon[0], y=mon[1],
        )
        # ★ 建完立刻登记，别等到要用时再按标题去找 ——
        #   按标题找会把【新开的这张】当成旧的关掉（见 _close_overlay）
        #
        # mon 和窗口挂在同一个字典里，因为它们是【同生共死】的：
        #   _close_overlay 会一起摘掉。分开存的话，窗口关了 mon 还留着，
        #   下一次换算就会悄悄用上一块屏的数字。
        _OVERLAY["win"] = w
        _OVERLAY["mon"] = mon

        # 覆盖层也要用 Win32 铺满屏幕 —— 位置一律不交给 pywebview
        threading.Thread(target=self._fit_overlay, daemon=True).start()

        return {"ok": True, "shot": [size[0], size[1]]}

    def _fit_overlay(self):
        mon = _OVERLAY.get("mon") or capture.primary_rect()
        ok = shell_mod.place_window(SEL_TITLE, mon)
        if not ok:
            print("[问了吗] 覆盖层没摆上位", flush=True)

    def set_region(self, nx0, ny0, nx1, ny1):
        """覆盖层回传的是归一化 0~1 坐标，这里换算成屏幕物理 rect。

        ⚠ 用【覆盖层那块屏】的 rect 换算 —— 和底图、铺窗同一个。
          这里要是回退成主屏，双屏下框出来的区域会整体平移一块屏的距离。
        """
        mon = _OVERLAY.get("mon") or capture.primary_rect()
        rect = capture.normalize_to_rect(nx0, ny0, nx1, ny1, monitor=mon)
        self.settings["region"] = list(rect)
        save_settings(self.settings)
        self._close_overlay()
        return {"ok": True, "region": list(rect)}

    def cancel_region(self):
        self._close_overlay()
        return {"ok": True}

    def clear_region(self):
        self.settings["region"] = None
        save_settings(self.settings)
        return {"ok": True}

    def _close_overlay(self, delay=0.25):
        """关掉【此刻这一张】覆盖层，并删掉底图。

        延一点再关：这个方法是从覆盖层自己的 JS 调用进来的，
        当场销毁自己容易出岔子。

        ⚠ 两条都栽过跟头，别改回去：

        1. **要关哪一张，必须在调用的这一刻就定下来**，不能等 0.25 秒后再去找。
           `pick_region` 会先关旧的、紧接着开新的，而开一张新的只要 0.2 秒左右 ——
           等醒来再按标题找，找到的是【新开的那张】，刚建好就被自己关掉。
           症状：点「框选区域」什么都没发生，偶尔又正常，极难复现。

        2. **窗口对象要记在 _OVERLAY 里**，不要按标题去 webview.windows 里翻。
           注意是模块级字典 —— 挂在 Api 实例上会触发 pywebview 的无限递归。
        """
        w = _OVERLAY.pop("win", None)            # ← 此刻就摘走，摘的是旧的那张
        # mon 跟着一起摘：它描述的就是这张覆盖层铺在哪块屏上。
        # ⚠ 调用方要用 mon 的话（set_region 就是）必须在【调本方法之前】读走。
        _OVERLAY.pop("mon", None)

        def job():
            time.sleep(delay)
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
            # 底图是整个桌面，落盘了就得删。
            # 但要是这期间又开了一张新的，新底图正在用，不能删。
            if _OVERLAY.get("win") is None:
                try:
                    if os.path.exists(SHOT_FULL):
                        os.remove(SHOT_FULL)
                except Exception:
                    pass

        threading.Thread(target=job, daemon=True).start()

    # ══════════ 识别：① 截屏 → OCR → 规则初筛 ══════════
    def recognize(self, mode="region"):
        """整个 ①③ 跑完才算一次，中途不许别的管线插进来。

        自动通道接上之后这里【真的会】并发（每 2 秒一次 poll，到点就再来一轮），
        而 SHOT_REGION 是固定单一路径、_last_todo 是按通道分槽的 ——
        不加锁的话两边都不报错，只是结果悄悄是错的。见 _run_lock 的说明。

        mode: "region"（框选/自动，截屏 + OCR）| "clip"（快捷键取词，读剪贴板那段字）。
        ⚠ 两条通道**共用这一个函数**（硬约束 4）。差别只在「文字从哪来」，
          从 prefilter 往后一个字都不分叉。
        """
        mode = mode if mode in ("region", "clip") else "region"
        with self._run_lock:
            return self._recognize_locked(mode)

    def _recognize_locked(self, mode="region"):
        # ★ 区域守卫【只对 region 生效】（决策 50）。
        #   这行是函数第一句，最容易漏 —— 漏了的话 clip 通道一进门就被
        #   「还没框选区域」挡住，而且报的是一句驴唇不对马嘴的话：
        #   用户明明只是在飞书里选了一段字。
        #   clip 的意义就是【不必先框选】，它跟框选是并列的两条入口。
        if mode == "region":
            rect = self.settings.get("region")
            if not rect:
                return {"ok": False, "msg": "还没框选区域"}

        t_cap = t_ocr = 0.0
        text = ""                       # 只有 clip 用得上，日志里要报字数
        if mode == "clip":
            # 不截屏、不 OCR —— 文字是热键线程从剪贴板里取回来的。
            # 顺带：SHOT_REGION 全程不产生文件，决策 33（截图用完即删）
            # 自动满足，不用管那个删图的 finally。
            text = (self.hotkey.last_text() or "").strip()
            if not text:
                # ⚠ 这里【不能】返回一个空的 ok。空 ok 会让前端走到
                #   「没有需要问的词」那条静默分支上：用户按了快捷键，
                #   面板上什么都不出现，也没有任何解释。
                return {"ok": False, "msg":
                        "没抓到文字 —— 可能没选中内容，或者按快捷键时修饰键还没松开。"}
            rows = [(ln, 1.0) for ln in text.splitlines() if ln.strip()]
        else:
            t0 = time.perf_counter()
            capture.grab_region(tuple(rect), SHOT_REGION)
            t_cap = time.perf_counter() - t0

            t0 = time.perf_counter()
            try:
                rows = ocr.ocr_lines(SHOT_REGION)
            finally:
                # ⚠ 这张图就是用户的笔记。识别完立刻删，不留痕。
                #   （全屏那张底图在 _close_overlay 里删，同理）
                try:
                    if os.path.exists(SHOT_REGION):
                        os.remove(SHOT_REGION)
                except Exception:
                    pass
            t_ocr = time.perf_counter() - t0

        texts = [t for t, _ in rows]
        cands = pipeline.prefilter(texts)

        policy = self.settings.get("kb_policy", "dedupe")
        st = kb.stats()

        # 白名单模式 + 空库 = 面板必然一片空白。
        # 那种空白和「程序坏了」在用户眼里没有任何区别，所以这里直接说清楚，
        # 不要让它在后端静默地产生一个空面板。
        if policy == "whitelist" and st["terms"] == 0:
            return {"ok": False, "msg":
                    "知识库还是空的，白名单模式下什么都不会显示。"
                    "先切到「不重复解释」攒几条，或者点「收纳历史」。"}

        # ③ 知识库过滤 —— 必须在 ② 之前，这是省钱那一步的落点
        buckets = kb.filter_candidates(cands, policy)
        todo, over = ai.cap_candidates(buckets["ai_todo"])
        self._last_todo[mode] = todo               # explain() 靠它认领，见那里的说明

        if buckets["kb_hits"]:
            kb.touch_used([h["key"] for h in buckets["kb_hits"]])

        # ⚠ clip 的日志前缀【必须】和 region 的区分开（决策 50）。
        #   `框选联调.py` / `知识库端到端联调.py` 是按 `"[管线]" in ln` 认行的，
        #   而 `"[管线]" in "[管线·取词] …"` 恰好为假（`线` 后面跟的是 `·` 不是 `]`），
        #   两边的解析互不干扰。
        #   更要紧的是：**回归跑到一半，手在键盘上按一下 Ctrl+E 就会多打一条** ——
        #   用同一个前缀的话，红的会是「① 跑通」那条断言，看起来像管线坏了。
        if mode == "clip":
            head = f"[管线·取词] 选中 {len(text)} 字"
        else:
            head = f"[管线] 截屏 {t_cap:.2f}s　OCR {t_ocr:.2f}s　识别 {len(texts)} 行"
        print(f"{head}　初筛 {len(cands)}　库里已有 {len(buckets['kb_hits'])}　"
              f"问模型 {len(todo)}　挡掉 {len(buckets['blocked'])}"
              + (f"　超上限 {over}" if over else ""), flush=True)

        return {
            "ok": True,
            # ★ 把通道带回去。前端渲染（statLine / 空态文案）要靠它分叉 ——
            #   clip 没有「截屏 0.00s　OCR 0.00s」可言，那是它自己的字。
            #   让结果自带通道，前端就不用把 mode 再往下透传三层。
            "mode": mode,
            "t_capture": round(t_cap, 2),
            "t_ocr": round(t_ocr, 2),
            "lines": texts,
            "scores": [round(s, 3) for _, s in rows],
            "candidates": cands,
            # ── ③ 分好的三桶。前端只负责渲染，不再自己判断谁被剔除了 ──
            "kb_hits": buckets["kb_hits"],         # 直接贴出来，不花钱
            "ai_todo": todo,                       # 要交给 ② 的
            "blocked": buckets["blocked"],         # why: excluded | not_in_kb
            "over_limit": over,                    # 撞到 MAX_CANDIDATES 被截掉的个数
            "policy": policy,
            "kb_total": st["terms"],
            # ② 的 user message 就是它。**只放要问的那批** ——
            # 库里已有的词不进这里，那正是「不重复解释」省下来的钱。
            "ai_payload": pipeline.build_ai_payload(texts, todo),
        }


def on_ready(sh):
    """窗口出来后立刻用 Win32 摆正 —— 这是决策 28。"""
    if not sh.attach():
        print("[问了吗] 没能拿到窗口句柄", flush=True)
        return
    sh.layout("right")
    print("[问了吗] 窗口已就位", flush=True)


def main():
    shell_mod.enable_dpi_awareness()          # 必须在建窗口之前

    # 收拾上一次没删干净的截图（决策 33）。放在最前面：
    # 越早删越好，别等界面都画出来了，用户桌面的图还躺在 web 目录里。
    _clear_shots()

    sh = Shell()
    api = Api(sh)

    import webview

    # x / y / width / height 随便传，后面 on_ready 会用 Win32 覆盖掉
    # 返回值【故意不接】—— 见 Api.__init__ 里的说明
    webview.create_window(
        TITLE,
        url=WEB,
        js_api=api,
        frameless=True,
        on_top=True,
        easy_drag=False,
        resizable=False,
        width=PANEL_W,
        height=900,
        x=0, y=0,
    )

    webview.start(on_ready, sh)


if __name__ == "__main__":
    main()
