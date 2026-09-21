# -*- coding: utf-8 -*-
"""验 `screen_locked()` —— 它从写下来到今天，**从没被证明返回过 True**。

    python "Desktop\\问了吗\\冒烟测试\\验锁屏判据.py"

────────────────────────────────────────────────────────
⚠⚠ **这个脚本会真的把你的屏幕锁上。** 跑之前请确认你人在机器前、
   并且手上没有别的事 —— 锁上之后要输密码才能回来。

   想取消：跑起来后有 5 秒倒计时，那 5 秒里按 Ctrl+C 就行。

────────────────────────────────────────────────────────
为什么这件事非做不可：

`screen_locked()` 是**所有开窗脚本的第一道门**。它要是永远返回 False
（比如判据写错了、`fg_exe()` 取不到前台进程），那么：

  · 锁着屏跑回归 → 不进「⏸ 整段没跑」那条路，而是照跑
  · 截屏拍到的是锁屏壁纸
  · 于是「面板上没有这几个字」「笔记窗口不在框里」**一条条红给你看**
  · 而它们报的原因全都指错方向 —— 真踩过，人走开了机器锁了，
    脚本只说「示例笔记窗口不在框选区域里」，白查了一轮

**判据本身从没红过，就等于它从没 green 过。** 一个恒假的守卫
和没有守卫，在屏幕上长得一模一样 —— 而这个是「恒假会导致灾难性误导」
的那一种，不是「恒假无所谓」的那一种。

────────────────────────────────────────────────────────
怎么办的：

锁屏之后**本进程不会退出**（`LockWorkStation` 只是锁桌面，不是结束会话），
所以后台那个采样线程在锁着的时候照样在跑 —— 判据就能被真的问到。

采样记两样东西：`screen_locked()` 的返回值、以及当时的 `fg_exe()`。
★ 两个都记，是为了在**没锁成**的时候还能说清是哪一种没锁成。
"""
import ctypes
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import 框选联调 as BOX                                            # noqa: E402

COUNTDOWN = 5
SAMPLE_SECS = 30

OUT = os.path.join(HERE, "_锁屏判据结果.txt")


def main():
    print("=" * 62)
    print("验 `screen_locked()`")
    print("=" * 62)
    print(f"⚠⚠ {COUNTDOWN} 秒后【真的锁屏】。想取消就现在按 Ctrl+C。\n")

    for i in range(COUNTDOWN, 0, -1):
        print(f"   {i}…", flush=True)
        time.sleep(1)

    # 锁之前先采一次 —— 这是【正向对照】：
    # 没锁的时候它必须说 False。少了这一条，一个「永远返回 True」的
    # screen_locked() 也会在下面拿满分（本项目老账：自检必须两个方向都验）。
    before = BOX.screen_locked()
    print(f"\n锁之前：screen_locked() = {before}　fg={BOX.fg_exe()!r}")
    if before:
        print("  ✗ 还没锁就说锁了 —— 判据是坏的（另一个方向），先修它，别锁了。")
        return 1

    samples = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            try:
                samples.append((time.time(), BOX.screen_locked(), BOX.fg_exe()))
            except Exception as e:                # 锁屏期间取不到也算一条证据
                samples.append((time.time(), None, f"<异常 {e!r}>"))
            time.sleep(0.05)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    t0 = time.time()
    print("锁屏中…（下面这些字要等你解锁回来才看得到）", flush=True)
    ctypes.windll.user32.LockWorkStation()
    time.sleep(SAMPLE_SECS)
    stop.set()
    th.join(timeout=2.0)

    locked = [t - t0 for t, v, _ in samples if v is True]
    exes = sorted({e for _, _, e in samples if e})
    lines = [
        f"锁之后采了 {len(samples)} 个点，跨 {SAMPLE_SECS} 秒。",
        f"其中 screen_locked() 说 True 的：{len(locked)} 个点。",
        f"采到的前台 exe：{exes}",
    ]
    if locked:
        lines.append(f"✓ 判据会红 —— 最早在第 {min(locked):.2f} 秒说 True"
                     f"（最晚 {max(locked):.2f} 秒）。")
        lines.append("  这道门真的会拦人，锁屏跑回归会被正确地报成「整段没跑」。")
        rc = 0
    else:
        lines.append("✗ 判据【一次都没说 True】—— 它是个恒假的守卫。")
        lines.append("  后果：锁屏跑回归不会被拦住，会以「面板上没有这几个字」")
        lines.append("  这类指错方向的理由红一屏。这道门等于不存在。")
        rc = 1

    text = "\n".join(lines)
    print("\n" + "=" * 62)
    print(text)
    print("=" * 62)
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"（也写了一份到 {OUT}）")
    except Exception:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
