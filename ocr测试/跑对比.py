# -*- coding: utf-8 -*-
"""OCR 对比：Windows 自带 OCR  vs  RapidOCR，逐图对比并自动打分。"""
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PS1 = os.path.join(HERE, "windows_ocr.ps1")

IMAGES = ["A_正常字号_白底.png", "B_小字号_白底.png", "C_正常字号_深色底.png"]

TERMS = [
    "RAG", "Chunking", "Chunk", "Embedding", "Vector DB",
    "Query", "Retrieval", "Top-K", "Prompt", "LLM",
    "Rerank", "Token", "Context Window",
]

# 每张图应有的完整文本（作为"认对了几个字"的分母）
GROUND_TRUTH = [
    "产品经理需要理解 RAG（检索增强生成）的基本流程",
    "首先把文档做 Chunking，切分成若干 Chunk",
    "然后用 Embedding 模型把每个 Chunk 转成向量",
    "存入 Vector DB 后，用户 Query 会先做 Retrieval",
    "召回 Top-K 结果，再拼进 Prompt 交给 LLM 生成答案",
    "这个过程中 Rerank 环节对精度影响很大",
    "还要注意 Token 成本和 Context Window 上限",
]


def norm(s):
    """归一化：去掉空白、全半角统一、转小写，用于宽松比对"""
    s = s.replace("（", "(").replace("）", ")").replace("，", ",")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def score_terms(text):
    """13 个术语里识别出了几个"""
    n = norm(text)
    return [t for t in TERMS if norm(t) in n]


def score_chars(text):
    """整段文字识别对了多少（按字符重合度粗算）"""
    truth = norm("".join(GROUND_TRUTH))
    got = norm(text)
    hit = sum(1 for c in truth if c in got)
    return hit, len(truth)


def run_windows(path):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS1, path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        return f"[失败] {r.stderr.strip()[:200]}"
    return r.stdout.strip()


def run_rapid(path):
    from rapidocr import RapidOCR
    engine = RapidOCR()
    res = engine(path)
    # rapidocr 3.x 返回对象，取 txts；老版本返回 list
    txts = getattr(res, "txts", None)
    if txts is None and isinstance(res, (list, tuple)):
        txts = [item[1] for item in res[0]] if res and res[0] else []
    return "\n".join(txts or [])


print("=" * 70)
print("OCR 对比测试：Windows 自带 OCR  vs  RapidOCR")
print("=" * 70)

summary = []
for name in IMAGES:
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        print(f"\n[跳过] {name} 不存在")
        continue

    print(f"\n{'─' * 70}")
    print(f"【{name}】")
    print(f"{'─' * 70}")

    win = run_windows(path)
    rap = run_rapid(path)

    for label, text in (("Windows 自带 OCR", win), ("RapidOCR", rap)):
        terms = score_terms(text)
        hit, total = score_chars(text)
        print(f"\n▸ {label}   术语 {len(terms)}/{len(TERMS)}   整段字符覆盖 {hit}/{total} ({hit/total*100:.0f}%)")
        print("  " + "-" * 60)
        for line in (text or "(空)").splitlines():
            print("  " + line)

    wt, rt = score_terms(win), score_terms(rap)
    wh, _ = score_chars(win)
    rh, _ = score_chars(rap)
    summary.append((name, len(wt), len(rt), wh, rh))

print(f"\n{'=' * 70}")
print("汇总")
print(f"{'=' * 70}")
print(f"{'图片':<24}{'Windows术语':>12}{'Rapid术语':>12}")
for name, wt, rt, wh, rh in summary:
    print(f"{name:<24}{wt:>12}{rt:>12}")
print(f"\n(术语满分为 {len(TERMS)})")
