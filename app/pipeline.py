# -*- coding: utf-8 -*-
"""三段式管线的 ① 规则初筛。

    ① 规则初筛（本文件）  ② AI 单次调用（app/ai.py）  ③ 知识库过滤（app/kb.py）

定位（硬约束）：
    这是【最宽的一道网，不是最终判据】。捞多了没关系，② 的 AI 会剔除。
    所以这里宁可宽松，也不要做自作聪明的过滤。

⚠ 三段在代码里的调用顺序是 ① → ③ → ②，不是 ① → ② → ③。
  ③ 要在 ② 之前跑，才能把「库里已经解释过的词」挡在模型调用之外 ——
  那正是「不重复解释」省钱的地方。CLAUDE.md 硬约束 3 写的是流水线的
  逻辑分工，别把它读成调用次序。

规则说明见 进展文档 待定事项第 1 项（仍未完全定稿）。
"""
import re

import kb

# 英文词块：字母开头，可带数字、连字符、点、斜杠，尾部可跟 ++ 或 #
# 命中：RAG / Chunking / Top-K / CI/CD / GPT-4 / B2B / o1-mini / C++ / C# / F#
# 不命中：1000-2000（数字开头）—— 这是刻意的，避免把数字区间当成术语
#
# ⚠ 尾部的 `(?:\+\+|#)?` 是后补的，别当成多余的删掉。
#   没有它的话 `C++` 只匹配到 `C`，而 `C` 长度 1 又被下面滤掉 ——
#   结果是 C++ / C# / F# 这几个中文技术笔记里的高频词【一个都抓不到】。
#   对「读笔记抓英文术语」这个产品来说，这是个第一轮就会被发现的问题。
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-/.+][A-Za-z0-9]+)*(?:\+\+|#)?")

# 明显不值得解释的功能词。只挡最没争议的一批，其余交给 AI 判断。
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
    """文本行列表 → 候选词列表（按在原文中首次出现的顺序）。

    返回 [{"term": 原始大小写, "line": 首次出现的整行, "at": 第几行, "key": 归一化}, ...]

    同一个词只保留首次出现 —— 后面那次通常没有额外信息，
    而且「首次出现」往往就是它被定义/引入的位置，上下文最有用。

    ⚠ 这里的去重键是 `w.lower()`，**不是 canon()** ——
      所以 `Top-K` 和 `TopK` 会各自成为一条候选。这是【故意的】：
      ① 只管撒网，合并同类项是 ③ 的活（见 kb.filter_candidates 里的二次合并）。
      改这里之前先想清楚，别把两道网合成一道。

    `key` 由 kb.canon() 给出，前端只做字符串比较，不再自己算归一化 ——
    算错就是钱和正确性，而这种逻辑只该有后端一份。
    """
    seen = {}
    for idx, line in enumerate(lines):
        for m in WORD.finditer(line):
            w = m.group()
            if len(w) < 2:
                continue
            if w.isdigit():
                continue
            if w.lower() in STOP:
                continue
            if w.lower() not in seen:
                seen[w.lower()] = {
                    "term": w,
                    "line": line.strip(),
                    "at": idx,
                    "key": kb.canon(w),
                }
    return list(seen.values())


def build_ai_payload(lines, candidates):
    """把「原文 + 候选」组装成要发给 AI 的东西。

    ② 直接拿它当 user message 用（见 ai.build_messages）。
    结构是 {"原文": [...], "候选词": [...]}，前端「看原始 OCR 与发给 AI 的载荷」
    折叠区里展示的就是它。
    """
    return {
        "原文": list(lines),
        "候选词": [c["term"] for c in candidates],
    }
