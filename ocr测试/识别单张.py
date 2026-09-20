# -*- coding: utf-8 -*-
"""对单张图片跑两个 OCR 引擎并对比。

用法:
    python 识别单张.py                  # 自动找最新的 screenshot-*.png
    python 识别单张.py <图片路径>
"""
import os
import re
import sys
import glob
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PS1 = os.path.join(HERE, "windows_ocr.ps1")


def pick_image():
    if len(sys.argv) > 1:
        return sys.argv[1]
    shots = sorted(glob.glob(os.path.join(HERE, "screenshot-*.png")))
    if not shots:
        sys.exit("没找到 screenshot-*.png，也没给路径")
    return shots[-1]          # 最新的那张


def run_windows(path):
    t = time.perf_counter()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS1, path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dt = time.perf_counter() - t
    if r.returncode != 0:
        return None, dt, r.stderr.strip()[:300]
    return r.stdout.strip(), dt, None


def run_rapid(path):
    from rapidocr import RapidOCR
    global _ENGINE
    try:
        _ENGINE
    except NameError:
        _ENGINE = RapidOCR()
    t = time.perf_counter()
    res = _ENGINE(path)
    dt = time.perf_counter() - t
    return res, dt


path = pick_image()
print("=" * 72)
print(f"图片: {os.path.basename(path)}")
try:
    from PIL import Image
    with Image.open(path) as im:
        print(f"尺寸: {im.width} x {im.height}")
except Exception:
    pass
print("=" * 72)

# ---------- Windows 自带 OCR ----------
print("\n【Windows 自带 OCR】")
win_text, win_dt, win_err = run_windows(path)
if win_err:
    print(f"  ✗ 失败: {win_err}")
    win_text = ""
else:
    print(f"  耗时 {win_dt:.2f}s")
    print("  " + "-" * 66)
    for line in win_text.splitlines():
        print("  " + line)

# ---------- RapidOCR ----------
print("\n【RapidOCR】")
res, rap_dt = run_rapid(path)
txts = list(res.txts or [])
scores = list(res.scores or [])
print(f"  耗时 {rap_dt:.2f}s   识别出 {len(txts)} 行")
print("  " + "-" * 66)
for i, (t, s) in enumerate(zip(txts, scores), 1):
    flag = "  ⚠低" if s < 0.6 else ""
    print(f"  {i:>2}. [{s:.3f}]{flag} {t}")

# ---------- 汇总 ----------
print("\n" + "=" * 72)
print("汇总")
print("=" * 72)
print(f"  Windows 自带 OCR : {win_dt:.2f}s, {len(win_text.splitlines())} 行")
print(f"  RapidOCR        : {rap_dt:.2f}s, {len(txts)} 行")
low = sum(1 for s in scores if s < 0.6)
if low:
    print(f"\n  RapidOCR 里有 {low} 行置信度低于 0.6，建议过滤掉再喂给 AI")
