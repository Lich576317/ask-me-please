# -*- coding: utf-8 -*-
"""自动通道 联调 —— 判决逻辑层（纯内存，不开窗口，不碰屏幕，秒级跑完）。

    python "Desktop\\问了吗\\冒烟测试\\自动通道联调.py"

────────────────────────────────────────────────────────
为什么这个脚本值得存在：

自动通道和别的模块【失败方式不一样】。它坏了不会报错、不会崩、界面上
什么异常都看不到 —— 它只会**安静地多花你的钱**。所以这里要钉的不是
「功能有没有」，而是几条「错了也看不出来」的性质：

  ★ 同屏只触发一次   —— 写成每 60 秒一次的话，用户盯着同一页笔记看十分钟，
                        模型就被问了十次。这是本模块最容易写出来的烧钱 bug。
  ★ 只看画面稳定     —— 判定条件是「这一屏画面停住 N 秒」（v0.13 起）。
                        原来还叠了一个「系统空闲 N 秒」，两个都要，结果
                        在主路径上几乎不触发（见 app/auto.py 判决处注释）。
                        去掉之后「用户在别处打字、这一屏恰好没变」会多花一次钱，
                        这笔账由「同屏只跑一次」兜住，§③ 钉的就是这条兜底。
  ★ 首用告知之前不触发 —— 隐私顺序，见 §隐私。

靠注入假时钟 / 假画面来做 —— 所以它一秒跑完，
不需要真的等 15 秒，也不需要 `WENLEMA_AUTO_SETTLE` 那个后门。
（后门本身在最后一节单独验。）
"""
import os
import sys
import time

# ⚠ 输出被重定向到管道/文件时 Python 默认用本地编码 GBK，
#   而这个脚本的输出全是中文和 ★ ✅ 这类符号 —— 不统一成 UTF-8 会炸在编码上，
#   看着像功能挂了，其实一个字都还没测。main.py 开头改的是同一句。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)

import auto                                                       # noqa: E402

W, H = 64, 64                     # 假画面尺寸，够大能体现比例，够小跑得快
FRAME_LEN = W * H * 3

_ok = 0
_fail = 0


def check(name, cond, detail=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [通过] {name}" + (f"　{detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  [不通过] {name}　{detail}")
    return cond


class Rig:
    """一个可以随便摆布时间、空闲、画面的假环境。

    ⚠ 注入的 grab 是个【普通可调用】，不是带 .grab 方法的对象 ——
      和 rect_getter / settle_getter / now 保持同一种形状。
      （这条踩过：注入函数结果 Watcher 去调 .grab，一路 error 静默降级。）
    """

    def __init__(self, settle=10.0, rect=(0, 0, W, H), notice=True):
        self.t = 0.0
        self.frame = bytes([200]) * FRAME_LEN
        self.rect = rect
        self.notice = notice
        self.grabs = 0
        self.boom = None                      # 设成异常就模拟截屏失败
        self._gen = 0                         # touch 的代数，保证每次改出来都不一样

        self.w = auto.Watcher(
            rect_getter=lambda: self.rect,
            notice_getter=lambda: self.notice,
            grab=self._grab,
            settle=settle,                    # 固定值，不现读 —— 这个脚本要的是可复现
            now=lambda: self.t,
        )

    def _grab(self, rect):
        self.grabs += 1
        if self.boom:
            raise self.boom
        return self.frame

    def step(self, seconds=2.0):
        self.t += seconds
        return self.w.tick_once()

    def run_until_fired(self, limit=400.0):
        """一直 tick 到触发。返回「从开始等到触发」用了多少秒；没触发返回 None。"""
        t0 = self.t
        end = self.t + limit
        while self.t < end:
            if self.step() == "fired":
                return self.t - t0
        return None

    def run_for(self, seconds, step=2.0):
        """往前走一段时间，返回期间【触发了几次】。"""
        n = 0
        end = self.t + seconds
        while self.t < end:
            if self.step(step) == "fired":
                n += 1
        return n

    # 改画面 ────────────────────────────────────────
    def touch(self, n_bytes):
        """把开头 n_bytes 个字节改掉，模拟「画面动了」。

        ⚠ 每次调用的结果必须和上一次【不一样】。
          写成「把它们置 0」的话，从第二次起就是同一个值 ——
          画面反而被判成「稳定」，于是「画面一直在变所以不触发」
          那条测试会以错误的理由变绿（真踩过）。
        """
        self._gen += 1
        v = 0x00 if self._gen % 2 else 0xFF
        self.frame = bytes([v]) * min(n_bytes, FRAME_LEN) + self.frame[n_bytes:]


def section(title):
    print(f"\n{title}")


# ══════════════════════════════════════════════════════════
def test_tolerance():
    section("① 画面比较必须带容差（不带的话功能等于不存在）")
    print("     光标闪烁、输入法候选、通知弹窗都会让像素变。精确比较的话")
    print("     「画面稳定」永远攒不满 60 秒 —— 自动通道一次都不会触发。")

    samples = FRAME_LEN // auto.SAMPLE_STEP
    print(f"     这一屏 {FRAME_LEN} 字节 → 抽样后 {samples} 个样本，"
          f"变化阈值 {samples * auto.CHANGE_RATIO:.1f} 个样本")

    # 小块变化（≈光标）：必须【不算】变 —— 直接看稳定起点有没有被推后。
    # ⚠ 不能拿 stable_now 判断：它分不清「刚被重置」和「刚起步」。
    r = Rig()
    r.w.set_on(True)
    r.step()                                   # 第一 tick 只是把画面记下来
    r.step()
    since = r.w._stable_since
    r.touch(1)                                 # 1 个字节 ≈ 1 个样本，远低于阈值
    r.step()
    check("一小块变化（光标级）不重置稳定计时",
          r.w._stable_since == since,
          f"稳定起点从 t={since} 被推到了 t={r.w._stable_since}")

    # 整屏换页：必须【算】变
    r4 = Rig()
    r4.w.set_on(True)
    r4.step()
    r4.step()
    since4 = r4.w._stable_since
    r4.touch(FRAME_LEN // 2)                   # 半屏都变了
    r4.step()
    check("整屏换页会重置稳定计时",
          r4.w._stable_since != since4,
          f"稳定起点还停在 t={r4.w._stable_since}")

    # 上面两条钉的是「判据」，下面这条钉的是「后果」——能不能按时触发。
    r2 = Rig()
    r2.w.set_on(True)
    used = r2.run_until_fired(limit=60)
    check("静止不动时能正常触发（基线）", used is not None, f"used={used}")

    r3 = Rig()
    r3.w.set_on(True)
    end = r3.t + 40
    while r3.t < end:                          # 每 2 秒闪一下光标
        r3.touch(1)
        r3.step()
    check("每隔 2 秒闪一下光标，照样能触发（容差真的落到了行为上）",
          r3.w._fire or r3.w._fired_this_screen,
          f"phase={r3.w.phase}　stable_now={r3.w.stable_now}")


def test_fire_once():
    section("② ★ 同屏只触发一次（本模块最容易写出来的烧钱 bug）")
    print("     写成「每 60 秒触发一次」的话，用户盯着同一页看十分钟，")
    print("     模型就被问了十次 —— 而且界面上完全看不出异常。")

    r = Rig(settle=10.0)
    r.w.set_on(True)
    used = r.run_until_fired(limit=60)
    check("停下来之后会触发", used is not None, f"用了 {used}s")
    check("触发时机合理（等满 settle 才动）",
          used is not None and 10 <= used <= 16, f"used={used}")

    p1 = r.w.poll()
    p2 = r.w.poll()
    check("fire 标记只给一次（第二次 poll 就该没了）",
          p1["fire"] and not p2["fire"], f"第一次={p1['fire']} 第二次={p2['fire']}")

    r.w.report("done")
    again = r.run_for(300.0)                   # 同一屏再盯五分钟
    check("★ 同一屏再盯 5 分钟，一次都不再触发",
          again == 0, f"又触发了 {again} 次")

    r.touch(FRAME_LEN // 2)                    # 用户翻页了
    used2 = r.run_until_fired(limit=60)
    check("画面变了之后，新的一屏能重新触发", used2 is not None, f"用了 {used2}s")


def test_stability_only():
    section("③ ★ 只看画面稳定（v0.13 起；原来还要叠一个「系统空闲」）")

    # 只看空闲的话：用户手没动，但页面在自动滚 / 视频在播
    r = Rig(settle=10.0)
    r.w.set_on(True)
    r.step()
    end = r.t + 120
    while r.t < end:
        r.touch(FRAME_LEN // 2)                # 画面一直在变
        r.step()
    check("画面一直在变（自动滚动/播视频）→ 不触发",
          not r.w._fired_this_screen, f"phase={r.w.phase}")

    # ⚠ 这一条原来叫「手一直在动（在别处打字）→ 不触发」，钉的是
    #   【系统空闲 ≥N 秒】那个条件。v0.13 用户拍板把它去掉了，理由见
    #   app/auto.py 判决处那段注释：读笔记的节奏是滚一屏读二三十秒再滚，
    #   「整整 60 秒没碰键鼠」在主路径上基本不成立，功能等于不存在。
    #
    #   所以这条【测的那条规矩已经不存在了】—— 它红的理由是对的。
    #   但代价是真实存在的：用户在别处打字、而这一屏恰好没变，会为这一屏
    #   花一次钱。这笔账之所以敢认，全靠 ↓ 这句「同一屏只跑一次」兜底：
    #   最多多花【一次】，绝不会反复花。所以这里改钉那条兜底，
    #   而不是把断言删掉了事。
    #
    #   ⚠ 这段【必须跑得比 BUSY_TIMEOUT_SEC(300s) 还长】。触发之后 _busy
    #     会挡 300 秒，那段时间里有没有「同一屏只跑一次」都只会触发一次 ——
    #     只跑 200 秒的话这条断言是【绿的，但什么都没测】。
    #     变异验证过：把守卫拿掉、跑 200s 它照样绿，跑 400s 才红。
    r2 = Rig(settle=10.0)
    r2.w.set_on(True)
    n = r2.run_for(400.0)
    check("画面一直没变时，同屏只触发 1 次（跑满 busy 超时也没再触发）",
          n == 1, f"触发了 {n} 次，期望 1")


def test_gates():
    section("④ 该拦住的时候要拦住")

    r = Rig()
    r.w.set_on(True)
    r.rect = None
    r.step()
    check("没框选区域 → phase=no_region", r.w.phase == "no_region", r.w.phase)
    check("没框选区域时【不去截屏】", r.grabs == 0, f"截了 {r.grabs} 次")
    check("没框选区域 → 永不触发", r.run_for(300.0) == 0)

    r.rect = (0, 0, W, H)
    r.notice = False                           # 首用告知还没关
    r.step()
    check("首用告知没关 → phase=notice", r.w.phase == "notice", r.w.phase)
    check("首用告知没关 → 不去截屏", r.grabs == 0, f"截了 {r.grabs} 次")
    check("★ 首用告知没关 → 永不触发（隐私顺序）",
          r.run_for(300.0) == 0)

    r.notice = True
    r.step()
    check("关掉告知之后就正常了", r.w.phase in ("waiting", "fired"), r.w.phase)

    r.w.set_on(False)
    r.step()
    check("关掉总开关 → phase=off", r.w.phase == "off", r.w.phase)
    check("关掉总开关 → 不再触发", r.run_for(300.0) == 0)


def test_busy():
    section("⑤ 跑着的时候不许再触发；前端崩了要能自愈")

    r = Rig(settle=10.0)
    r.w.set_on(True)
    r.run_until_fired(limit=60)
    r.w.report("start")
    r.step()
    check("运行中 → phase=busy", r.w.phase == "busy", r.w.phase)
    check("运行中 → 不触发", r.run_for(60.0) == 0)

    # 前端崩了 / 没来 ack：不能把自动通道永久卡死。
    # ⚠ 这一段要【一次跑完】再断言。中途分两次看是看不出名堂的：
    #   自愈之后它会立刻按新的一屏再触发一次，于是又进入 busy ——
    #   那是【对】的行为，不是没自愈。
    r.touch(FRAME_LEN // 2)                    # 换一屏，好让它有理由再触发
    n = r.run_for(auto.BUSY_TIMEOUT_SEC + 20)
    check("busy 超时后自愈，并对新的一屏重新触发了一次",
          n == 1, f"期间触发了 {n} 次")

    # 这一次正常走完（前端来 ack 了），再换一屏，应该还能触发
    r.w.report("done")
    r.touch(FRAME_LEN // 2)
    used = r.run_until_fired(limit=60)
    check("ack 之后换一屏，还能正常触发", used is not None, f"用了 {used}s")


def test_failure():
    section("⑥ 监视器自己坏了，不许把程序一起带走")

    r = Rig()
    r.w.set_on(True)
    r.boom = OSError("模拟：锁屏 / 远程桌面断开")
    ok = True
    try:
        for _ in range(5):
            r.step()
    except Exception as e:
        ok = False
        print(f"       tick 抛出去了：{e!r}")
    check("截屏持续失败也不会抛异常", ok)
    check("截屏失败 → phase=error", r.w.phase == "error", r.w.phase)
    check("截屏失败 → 不触发", r.run_for(100.0) == 0)
    check("截屏失败后标签能说明情况", "失败" in r.w.label(), r.w.label())

    r.boom = None
    r.step()
    check("恢复之后能自己好", r.w.phase in ("waiting", "fired"), r.w.phase)


def test_labels():
    section("⑦ 界面拿得到「它在等什么」")

    r = Rig()
    r.w.set_on(True)
    r.rect = None
    r.step()
    check("没框选时标签不是空话", "框选" in r.w.label(), r.w.label())
    check("没框选时 hint 说得清怎么办", "框选" in r.w.hint(), r.w.hint())

    r.rect = (0, 0, W, H)
    r.step()
    check("等待中标签带倒计时", "s" in r.w.label(), r.w.label())
    check("倒计时是正数且不超过 settle",
          0 < r.w.wait_seconds() <= r.w.settle, f"wait={r.w.wait_seconds()}")
    # ⚠ 这条是用【子串】把文案钉死的（v0.13 改文案时它红过一次 ——
    #   那是预期的，不是 bug）。想钉的是「hint 里得说出在等什么」，
    #   但只钉得住字面。改文案时记得同步这里。
    check("hint 讲清了触发条件",
          "停住" in r.w.hint() and "变" in r.w.hint(), r.w.hint()[:40])

    r.w.set_on(False)
    r.step()
    check("关了之后标签就是「自动」两个字", r.w.label() == "自动", r.w.label())
    check("关了之后 wait 归零", r.w.wait_seconds() == 0)


def test_backdoor():
    section("⑧ WENLEMA_AUTO_SETTLE（联调后门，不是用户开关）")

    old = os.environ.get("WENLEMA_AUTO_SETTLE")
    try:
        os.environ.pop("WENLEMA_AUTO_SETTLE", None)
        check("不设时就是默认 15 秒（v0.13 起；再往前是 60）",
              auto.resolve_settle() == auto.DEFAULT_SETTLE_SEC == 15.0,
              f"{auto.resolve_settle()}")

        os.environ["WENLEMA_AUTO_SETTLE"] = "3"
        check("设了之后生效", auto.resolve_settle() == 3.0, f"{auto.resolve_settle()}")

        os.environ["WENLEMA_AUTO_SETTLE"] = "乱写的"
        check("值写坏了要退回 15，不能崩",
              auto.resolve_settle() == 15.0, f"{auto.resolve_settle()}")

        os.environ["WENLEMA_AUTO_SETTLE"] = "0"
        check("0 或负数要退回 15（否则会疯狂触发）",
              auto.resolve_settle() == 15.0, f"{auto.resolve_settle()}")

        # ★ 后门必须【盖过】用户设置 —— 否则联调时压不短，白等 15 秒，
        #   而失败的样子是「没触发」，看着像功能坏了。
        #   ⚠ 上面那条刚把 env 设成 "0"（非法 → 当作没设），这里要重新设成 "3"，
        #     否则测的是「非法后门 vs 用户值」，跟这条想钉的优先级没关系。
        os.environ["WENLEMA_AUTO_SETTLE"] = "3"
        check("后门盖过操作台里设的值（联调要能压短）",
              auto.resolve_settle(60) == 3.0, f"{auto.resolve_settle(60)}")
        check("后门也盖过默认值（谁都没设过的时候）",
              auto.resolve_settle() == 3.0, f"{auto.resolve_settle()}")

        os.environ.pop("WENLEMA_AUTO_SETTLE", None)
        check("后门撤了就用操作台设的值", auto.resolve_settle(60) == 60.0,
              f"{auto.resolve_settle(60)}")
        # 操作台的值本身写坏/为空时退回默认 —— 「空/非法就用 15」这件事
        # 【只有 resolve_settle 一个人说了算】，别处不许再写一遍。
        check("操作台的值非法时退回 15", auto.resolve_settle("乱写的") == 15.0,
              f"{auto.resolve_settle('乱写的')}")
        check("操作台的值是空时退回 15", auto.resolve_settle(None) == 15.0,
              f"{auto.resolve_settle(None)}")
    finally:
        os.environ.pop("WENLEMA_AUTO_SETTLE", None)
        if old is not None:
            os.environ["WENLEMA_AUTO_SETTLE"] = old


def test_cost():
    section("⑨ 开销（每 2 秒跑一次的东西，不能太重）")

    # 真实面板尺寸的一帧
    real = bytes([200]) * (1039 * 540 * 3)
    t0 = time.perf_counter()
    s = auto.sample_of(real)
    t1 = time.perf_counter()
    auto.looks_changed(s, s)
    t2 = time.perf_counter()

    print(f"     真实尺寸一帧：抽样 {t1 - t0:.4f}s　比较 {t2 - t1:.4f}s")
    check("抽样够快（每 2 秒一次，要远低于 0.5s）", t1 - t0 < 0.5, f"{t1 - t0:.4f}s")
    check("比较够快", t2 - t1 < 0.5, f"{t2 - t1:.4f}s")

    # 位置一变就该算变
    s2 = auto.sample_of(bytes([0]) * (1039 * 540 * 3))
    check("全黑和全亮会被判成「变了」", auto.looks_changed(s, s2))

    # 长度不一致（用户重新框选，区域大小变了）不能崩，且要算变
    check("区域尺寸变了不崩，且算作「变了」",
          auto.looks_changed(s, s[:10]) is True)


def main():
    print("=" * 62)
    print("问了吗 · 自动通道 联调（判决逻辑层，不碰屏幕）")
    print("=" * 62)

    test_tolerance()
    test_fire_once()
    test_stability_only()
    test_gates()
    test_busy()
    test_failure()
    test_labels()
    test_backdoor()
    test_cost()

    print("-" * 62)
    print(f"通过 {_ok} 项，不通过 {_fail} 项")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
