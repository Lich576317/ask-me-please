# -*- coding: utf-8 -*-
"""自动通道 —— 盯着框选区域，等你停手了就自己跑一遍。

    触发 = 画面稳定 ≥ N 秒（决策 45，v0.13 修订：不再要求「系统空闲」）

⚠ **这里只做判决，不做执行。**
  到点了就置一个 fire 标记；前端每 2 秒来 poll 一次，拿到 fire 之后去调
  【它自己那条】`runPipeline()`。为什么不在这里直接跑整条管线：
  硬约束 4 要求自动与手动**共用整条管线**。后端自己再跑一遍
  recognize → explain 就是第二份实现，两段式渲染、三桶、`bucketize()`
  的顺序全都要跟着抄一遍，迟早分叉 —— 而且分叉了不报错，只是变贵或错位。

⚠ **这里一个文件都不落。**
  每 2 秒截一帧只是为了看「画面变没变」，落盘的话决策 33（截图用完即删）
  会被高频截屏撑爆：`recognize()` 有 finally 删图，但 `close_app()` 结尾的
  `os._exit(0)` **跳过所有 finally**。走内存就没有「没删掉的文件」这回事。

⚠ 判决器【不自己开线程、不自己截图、不自己读时钟】——
  全都从外面注入。测试要能在不碰屏幕、不 sleep 真时间的前提下
  把「同屏只触发一次」这种烧钱 bug 钉死。
"""
import math
import os
import threading
import time
import traceback

import capture

# ── 参数 ────────────────────────────────────────────────────────────
# 「画面停住多久算你在读这一屏」的默认阈值。
# 用户可在操作台改成 5/15/30/60 秒（settings.auto_settle_sec）；
# 联调时 `WENLEMA_AUTO_SETTLE` 会盖过它 —— 优先级见 resolve_settle()。
DEFAULT_SETTLE_SEC = 15.0

TICK_SEC = 2.0                 # 每 2 秒看一次。截图 ~50ms，占比可忽略
BUSY_TIMEOUT_SEC = 300.0       # 前端崩了/没来 ack 时的兜底放行，见 _busy_expired

# ── 画面比较 ────────────────────────────────────────────────────────
# 整块区域 1039×540×3 ≈ 1.7MB，每 2 秒逐字节比不现实 → 降采样 + 量化。
SAMPLE_STEP = 16               # 每 16 个字节取 1 个（够看出整屏换页，看不出光标）
QUANT_MASK = 0xF0              # 只留高 4 位，抹掉抗锯齿/子像素渲染的抖动
DIFF_THRESHOLD = 32            # 单个样本差多少才算「这个点变了」
CHANGE_RATIO = 0.005           # 变了的样本占比超过 0.5% 才算「画面变了」

# bytes.translate 的查表法：C 层跑，比 Python 生成式快一个量级
_QUANT_TABLE = bytes.maketrans(bytes(range(256)),
                               bytes(b & QUANT_MASK for b in range(256)))


def _env_settle():
    """联调后门 `WENLEMA_AUTO_SETTLE` 的值；没设或写坏了返回 None。

    和 `WENLEMA_BASE_URL` / `WENLEMA_KB` / `WENLEMA_SETTINGS` 一个性质 ——
    **是测试接口，不是用户开关**（`自动通道联调.py` 有一整节在验它）。
    """
    raw = os.environ.get("WENLEMA_AUTO_SETTLE")
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v > 0 else None


def resolve_settle(configured=None):
    """真正生效的等待阈值（秒）。

    优先级：**联调后门 > 用户设置 > 默认 15 秒**。

    ★ 判据【只在这一个函数里】。main.py 那边把 settings 的值直接喂进来，
      不自己再写一遍「空/非法就用 15」—— 两道判断迟早分叉，
      而分叉了不报错，只是测试压不短、白等 15 秒。
    """
    env = _env_settle()
    if env is not None:
        return env
    try:
        v = float(configured)
    except (TypeError, ValueError):
        return DEFAULT_SETTLE_SEC
    return v if v > 0 else DEFAULT_SETTLE_SEC


def sample_of(rgb):
    """原始 RGB 字节 → 降采样 + 量化后的指纹。

    ⚠ 为什么必须带容差，不能精确比较：
      光标闪烁、输入法候选窗、通知弹窗、视频、动画 —— 任何一处小动静
      都会让像素变。精确 hash 的话「画面稳定」永远攒不满 60 秒，
      功能等于不存在。这里要的是「大致没动」，不是「一模一样」。
    """
    return rgb[::SAMPLE_STEP].translate(_QUANT_TABLE)


def looks_changed(prev, cur):
    """两个指纹是不是「明显不一样」。

    ⚠ 判定方向是【宁可说没变】：说错了只是多等一下，
      说反了（把没变当成变了）会让计时器永远重置，功能直接失效。
    """
    if prev is None or len(prev) != len(cur):
        return True               # 第一次，或者框选区域换了大小
    limit = len(cur) * CHANGE_RATIO
    n = 0
    for a, b in zip(prev, cur):
        d = a - b
        if d > DIFF_THRESHOLD or -d > DIFF_THRESHOLD:
            n += 1
            if n > limit:         # 够数了就走，不必扫完
                return True
    return False


class Watcher:
    """自动通道的判决器。纯逻辑，无副作用（除了读屏幕）。"""

    def __init__(self, *, rect_getter, notice_getter, grab=None,
                 settle=None, settle_getter=None, now=None):
        self._rect = rect_getter
        self._notice = notice_getter
        # ★ settle 是【每次现问】的，不是构造时冻结的 —— 见下面的 settle 属性。
        #   测试传 settle=数字（固定值）；main.py 传 settle_getter=（现读设置）。
        if settle is not None:
            self._settle_getter = lambda: float(settle)
        else:
            self._settle_getter = settle_getter or resolve_settle
        # 注入的一律是【普通可调用】，和 rect_getter / notice_getter / now 保持一致。
        # 注入一个「带 .grab 方法的对象」要多记一条规矩，测试写起来还容易踩空。
        #
        # 默认那条就是 capture.grab_region_bytes 本身 —— 它每次都新建一个 mss
        # 实例用完就关，所以**谁在哪个线程调它都无所谓**。这里曾经包了一层
        # 「复用 mss 实例」的 ScreenGrabber，正是那层缓存踩了 DC 归线程所有的坑，
        # 详见 capture.grab_region_bytes 的说明。
        self._grab = grab or capture.grab_region_bytes
        self._now = now or time.monotonic

        self._on = False
        self._prev = None             # 上一 tick 的指纹
        self._stable_since = None     # 「画面大致没动」是从什么时候开始的
        self._fired_this_screen = False
        self._busy = False
        self._busy_since = 0.0
        self._fire = False            # 待前端来取的触发标记
        self._err_count = 0           # 连续截屏失败次数，用来给报错限流

        # 只给界面显示用
        self.stable_now = 0.0
        self.phase = "off"

    @property
    def settle(self):
        """当前生效的等待阈值（秒）。每次读都现问一次。

        ★ 这样【不需要任何「配置改了通知后台线程」的机制】：
          操作台存完设置，下一 tick（最多 2 秒）自己就是新值。
        """
        return float(self._settle_getter())

    # ── 外部控制 ────────────────────────────────────────────────────
    def set_on(self, flag):
        self._on = bool(flag)
        if not self._on:
            self._reset_tracking()
        return self._on

    def report(self, state):
        """前端汇报一次运行的生命周期。

        done / fail 都要**顺手把「这一屏已经跑过了」钉上** ——
        手动跑完也算，否则用户刚点完「立即识别」，自动通道会立刻
        对同一屏再跑一遍，白花一次钱。
        """
        if state == "start":
            self._busy = True
            self._busy_since = self._now()
        elif state in ("done", "fail"):
            self._busy = False
            if self._prev is not None:
                self._fired_this_screen = True
        return state

    # ── 判决 ────────────────────────────────────────────────────────
    def tick_once(self, now=None):
        now = self._now() if now is None else now

        # 兜底超时放在最前面：前端要是崩了/没来 ack，不能让自动通道
        # 永久卡在 busy 上。这不是「恢复得漂亮」，是「别死透」。
        if self._busy and now - self._busy_since > BUSY_TIMEOUT_SEC:
            self._busy = False

        if not self._on:
            self.phase = "off"
            return self.phase
        if not self._notice():
            # ★ 隐私顺序：自动通道会把笔记【主动】发去第三方。
            #   用户还没被告知就先自动外发了一次，性质和「他自己点的」完全不同。
            #   手动通道有遮罩天然挡着，自动通道没有，得显式挡。
            self.phase = "notice"
            return self.phase

        rect = self._rect()
        if not rect:
            self.phase = "no_region"
            return self.phase
        if self._busy:
            self.phase = "busy"
            return self.phase

        try:
            rgb = self._grab(rect)
        except Exception as e:
            # 监视器自己崩了不能把整个程序带走，也不能让 tick 线程死掉
            self._err_count += 1
            # ⚠ 报错要限流。截屏可能【持续】失败（锁屏、远程桌面断开），
            #   而这段是每 2 秒跑一次的 —— 不限流的话一分钟刷 30 行，
            #   真出事的时候反而把有用的日志冲没了。
            if self._err_count == 1 or self._err_count % 30 == 0:
                print(f"[自动] 截屏失败（第 {self._err_count} 次）：{e}", flush=True)
                # 连栈一起打。截屏失败的信息量全在栈里 —— 上面那句异常文案
                # 可能只有「BitBlt」四个字母，光看它根本无从下手（真吃过这个亏）。
                # 限流在，所以最多每 30 次失败刷一屏。
                traceback.print_exc()
            self.phase = "error"
            return self.phase

        self._err_count = 0

        cur = sample_of(rgb)
        if looks_changed(self._prev, cur):
            self._prev = cur
            self._stable_since = now
            # 画面变了 = 这是新的一屏，之前跑过的那次不算数了
            self._fired_this_screen = False
            self.stable_now = 0.0
            self.phase = "waiting"
            return self.phase

        self.stable_now = now - self._stable_since

        # ★ 只看画面稳定（决策 45，v0.13 修订）。
        #   原来是「系统空闲 且 画面稳定」两个都要，结果几乎不触发：
        #   读笔记的节奏是滚一屏、读二三十秒、再滚，而「整整 60 秒没碰键鼠」
        #   在这个节奏里基本永远不成立 —— 一个不触发的省钱功能等于不存在。
        #
        #   只看画面【确实】漏掉一种情况：用户在别处打字、而这一屏恰好没变。
        #   那时会为这一屏花一次钱。代价由下面 _fired_this_screen 兜住 ——
        #   同一屏最多多花这一次，绝不会反复花，所以这是可控的。
        if self.stable_now >= self.settle and not self._fired_this_screen:
            self._fired_this_screen = True    # ★ 同屏只跑一次，不反复烧钱
            self._busy = True
            self._busy_since = now
            self._fire = True
            self.phase = "fired"
            return self.phase

        self.phase = "waiting"
        return self.phase

    # ── 给前端 ──────────────────────────────────────────────────────
    def poll(self):
        """前端每 2 秒问一次。

        ⚠ 这个方法【绝不持锁、绝不做重活】。
          pywebview 每次 js_api 调用都新开一条【非守护线程】——
          这里要是阻塞在 recognize() 那几十秒的运行锁上，
          就会攒出一堆卡住的非守护线程，正好把 os._exit(0) 要绕开的问题
          原样搬回来。所以它只读几个标量字段。
        """
        fire = self._fire
        self._fire = False
        return {
            "ok": True,
            "on": self._on,
            "fire": fire,
            "phase": self.phase,
            "wait": self.wait_seconds(),
            "label": self.label(),
            "hint": self.hint(),
        }

    def wait_seconds(self):
        if self.phase in ("off", "notice", "no_region", "busy", "error"):
            return 0
        # ★ 只减 stable_now。这里曾经是 min(idle_now, stable_now)，
        #   idle 一去掉就会变成「永远返回 settle、倒计时冻住」——
        #   而 0 < wait <= settle 那两条断言照样是绿的（抓不住）。
        need = self.settle - self.stable_now
        return max(0, int(math.ceil(need)))

    def label(self):
        """开关上那行短字。要短 —— 标题栏就那么点地方。"""
        if not self._on:
            return "自动"
        if self.phase == "no_region":
            return "自动 · 待框选"
        if self.phase == "notice":
            return "自动 · 待确认"
        if self.phase in ("busy", "fired"):
            return "自动 · 识别中"
        if self.phase == "error":
            return "自动 · 截屏失败"
        return f"自动 · {self.wait_seconds()}s"

    def hint(self):
        """鼠标悬停时的完整说法。用户得知道它在等什么。"""
        if not self._on:
            return "自动通道：开着之后，你停手一会儿它就自己识别一次"
        if self.phase == "no_region":
            return "自动通道开着，但还没框选区域 —— 先点「框选区域」"
        if self.phase == "notice":
            return "自动通道开着，但要先点掉「开始之前」那个提示才会开始"
        if self.phase in ("busy", "fired"):
            return "自动通道正在识别…"
        if self.phase == "error":
            return "自动通道截屏失败，窗口可能被最小化了"
        w = self.wait_seconds()
        if w <= 0:
            return "自动通道：条件已满足，马上开始"
        return (f"自动通道：这一屏停住 {int(self.settle)} 秒就自动识别一次。\n"
                f"（还要约 {w} 秒；这期间画面一变就重新计时）")

    # ── 内部 ────────────────────────────────────────────────────────
    def _reset_tracking(self):
        self._prev = None
        self._stable_since = None
        self._fired_this_screen = False
        self._busy = False
        self._busy_since = 0.0
        self._fire = False
        self.stable_now = 0.0


def start(watcher, tick_sec=TICK_SEC):
    """把判决器跑起来。

    daemon 线程：它不该拦着解释器退出（`close_app()` 是直接
    `os._exit(0)` 走的，但也别指望那个）。
    """
    def loop():
        while True:
            try:
                watcher.tick_once()
            except Exception as e:
                # ⚠ 诊断代码必须比被测代码更不容易出错 ——
                #   它自己崩了会把整个自动通道一起带走，而且悄无声息。
                print(f"[自动] tick 异常：{e!r}", flush=True)
            time.sleep(tick_sec)

    t = threading.Thread(target=loop, name="wl-auto", daemon=True)
    t.start()
    return t
