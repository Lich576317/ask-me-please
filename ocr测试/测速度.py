# -*- coding: utf-8 -*-
"""测两个 OCR 引擎的速度。"""
import os
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PS1 = os.path.join(HERE, "windows_ocr.ps1")
IMG = os.path.join(HERE, "A_正常字号_白底.png")
N = 3


def timeit(fn, n=N):
    ts = []
    for _ in range(n):
        s = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - s)
    return sum(ts) / len(ts), min(ts)


# ---- Windows OCR（含每次冷启 PowerShell 进程的开销）----
def win_call():
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS1, IMG],
        capture_output=True,
    )


win_avg, win_min = timeit(win_call)
print(f"Windows 自带 OCR : 平均 {win_avg:.2f}s   最快 {win_min:.2f}s")
print("                   (每次都要冷启一个 PowerShell 进程)")

# ---- RapidOCR ----
print("\n正在加载 RapidOCR 模型...")
s = time.perf_counter()
from rapidocr import RapidOCR
engine = RapidOCR()
first = time.perf_counter() - s

s = time.perf_counter()
engine(IMG)          # 预热：第一次推理含初始化
warm = time.perf_counter() - s
print(f"RapidOCR 模型加载: {first:.2f}s   首次推理: {warm:.2f}s")

rap_avg, rap_min = timeit(lambda: engine(IMG))
print(f"RapidOCR        : 平均 {rap_avg:.2f}s   最快 {rap_min:.2f}s")
print("                   (模型常驻内存，跨次调用不用重新加载)")

print()
if rap_avg < win_avg:
    print(f"结论：RapidOCR 快 {win_avg / rap_avg:.1f} 倍（持续运行时）")
else:
    print(f"结论：Windows OCR 快 {rap_avg / win_avg:.1f} 倍")
print(f"但 RapidOCR 有一次性启动成本 {first:.2f}s —— 常驻进程可忽略")
