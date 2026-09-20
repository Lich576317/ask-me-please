# -*- coding: utf-8 -*-
"""生成 OCR 对比测试图。模拟笔记场景：中文正文里夹着英文缩写/行业黑话。"""
from PIL import Image, ImageDraw, ImageFont
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = r"C:\Windows\Fonts\msyh.ttc"

# 模拟一段真实笔记：中文 + 英文术语混排
LINES = [
    "产品经理需要理解 RAG（检索增强生成）的基本流程",
    "首先把文档做 Chunking，切分成若干 Chunk",
    "然后用 Embedding 模型把每个 Chunk 转成向量",
    "存入 Vector DB 后，用户 Query 会先做 Retrieval",
    "召回 Top-K 结果，再拼进 Prompt 交给 LLM 生成答案",
    "这个过程中 Rerank 环节对精度影响很大",
    "还要注意 Token 成本和 Context Window 上限",
]

# 用于自动打分的术语清单（真实笔记里"该被解释出来"的东西）
TERMS = [
    "RAG", "Chunking", "Chunk", "Embedding", "Vector DB",
    "Query", "Retrieval", "Top-K", "Prompt", "LLM",
    "Rerank", "Token", "Context Window",
]


def make(path, size, fg, bg, line_gap, margin=18):
    font = ImageFont.truetype(FONT, size)
    # 先量一遍尺寸
    tmp = Image.new("RGB", (10, 10))
    d = ImageDraw.Draw(tmp)
    w = max(int(d.textlength(s, font=font)) for s in LINES) + margin * 2
    h = (size + line_gap) * len(LINES) + margin * 2

    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    y = margin
    for s in LINES:
        d.text((margin, y), s, font=font, fill=fg)
        y += size + line_gap
    img.save(path)
    print(f"  已生成 {os.path.basename(path)}  {w}x{h}  字号{size}")
    return path


print("生成测试图:")
make(os.path.join(HERE, "A_正常字号_白底.png"), 20, (0, 0, 0), (255, 255, 255), 10)
make(os.path.join(HERE, "B_小字号_白底.png"),   14, (0, 0, 0), (255, 255, 255), 8)
make(os.path.join(HERE, "C_正常字号_深色底.png"), 20, (235, 240, 245), (32, 38, 46), 10)
print("\n术语清单(用于自动打分):")
print("  " + ", ".join(TERMS))
