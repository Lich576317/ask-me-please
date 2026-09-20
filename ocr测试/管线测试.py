# -*- coding: utf-8 -*-
"""跑通「OCR → 规则初筛 → (AI 边界)」这条管线，看看实际效果。

用法:
    python 管线测试.py                 # 自动找最新的 screenshot-*.png
    python 管线测试.py <图片路径>
"""
import os
import re
import sys
import glob
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────────────────
# ① 规则初筛
#    定位:最宽的一道网,不是最终判据。捞多了没关系,AI 会剔。
# ──────────────────────────────────────────────────────────

# 英文词块:字母开头，可带数字、连字符、点、斜杠
# 命中: RAG / Chunking / Top-K / CI/CD / GPT-4 / B2B / o1-mini
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-/.][A-Za-z0-9]+)*")

# 明显不值得解释的功能词——只挡最没争议的一批，其余交给 AI
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "be", "been", "being", "for", "on", "at", "by", "with", "from", "as", "it",
    "its", "this", "that", "these", "those", "you", "your", "we", "our", "they",
    "their", "he", "she", "his", "her", "not", "no", "yes", "can", "will",
    "would", "should", "could", "may", "might", "must", "do", "does", "did",
    "have", "has", "had", "but", "if", "then", "else", "when", "where", "which",
    "who", "what", "how", "all", "any", "each", "every", "some", "more", "most",
    "other", "such", "only", "own", "same", "so", "than", "too", "very", "just",
    "also", "into", "over", "after", "before", "between", "under", "above",
}


def prefilter(lines):
    """输入:OCR 文本行 → 输出:{词: 首次出现的上下文行}"""
    found = {}
    for line in lines:
        for m in WORD.finditer(line):
            w = m.group()
            if len(w) < 2:
                continue
            if w.isdigit():
                continue
            if w.lower() in STOP:
                continue
            key = w.lower()
            if key not in found:
                found[key] = (w, line)     # 保留首次出现的原始大小写 + 上下文
    return found


# ──────────────────────────────────────────────────────────

def pick_image():
    if len(sys.argv) > 1:
        return sys.argv[1]
    shots = sorted(glob.glob(os.path.join(HERE, "screenshot-*.png")))
    if not shots:
        sys.exit("没找到 screenshot-*.png")
    return shots[-1]


path = pick_image()
print("=" * 74)
print(f"输入图片: {os.path.basename(path)}")

from rapidocr import RapidOCR
engine = RapidOCR()
t = time.perf_counter()
res = engine(path)
dt = time.perf_counter() - t
txts = [t_ for t_ in (res.txts or [])]
scores = list(res.scores or [])
print(f"OCR 耗时: {dt:.2f}s   识别 {len(txts)} 行")
print("=" * 74)

print("\n【OCR 输出原文】")
for i, (t_, s) in enumerate(zip(txts, scores), 1):
    flag = "  ⚠低置信度" if s < 0.6 else ""
    print(f"  {i:>2}. [{s:.3f}]{flag} {t_}")

# 置信度过滤（决策 25）
kept = [t_ for t_, s in zip(txts, scores) if s >= 0.6]
dropped = len(txts) - len(kept)
if dropped:
    print(f"\n  → 置信度 < 0.6 的行已丢弃 {dropped} 行")

full_text = "\n".join(kept)

print("\n" + "=" * 74)
print("【① 规则初筛:捞出的候选】")
print("=" * 74)
cands = prefilter(kept)
for i, (key, (orig, ctx)) in enumerate(sorted(cands.items()), 1):
    print(f"  {i:>2}. {orig:<22} ← 出现在: {ctx.strip()[:56]}")

print(f"\n  → 共捞出 {len(cands)} 个候选")

print("\n" + "=" * 74)
print("【② 送给 AI 的载荷长这样】")
print("=" * 74)
print(f"""
  模型: deepseek-chat
  System: {{默认提示词 —— 只解释英文术语}}

  User:
  ┌──────────────────────────────────────────────────────┐
  │ 原文（OCR 识别结果）:                                   │""")
for line in kept:
    print(f"  │   {line[:50]:<50}│")
print("""  │                                                       │
  │ 候选词（规则初筛结果）:                                  │""")
for key, (orig, _) in sorted(cands.items()):
    print(f"  │   {orig[:50]:<50}│")
print(f"""  └──────────────────────────────────────────────────────┘

  要求:从候选中挑出真正需要解释的英文术语，各写一句解释。
        输出 JSON: [{{"术语": "...", "解释": "..."}}]

  → 注意:全文只有 {len(kept)} 行、{len(full_text)} 字，本来也不算大。
    但如果是一整篇笔记（几百行），规则初筛就能省下大头的 token。
""")
