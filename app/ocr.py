# -*- coding: utf-8 -*-
"""OCR 层 —— 可替换模块（决策 24）。

接口约定（写死，上层只认这个）：

    输入：图片路径  →  输出：[(文本行, 置信度), ...]

将来若因打包分发要换回 Windows 自带 OCR（零依赖），**只改这个文件**，
上层一行都不用动。

实测数据见 进展文档.md 附录 A。
"""
import threading

MIN_SCORE = 0.6        # 决策 25：低于此值的行直接丢弃，不喂给 AI

_engine = None
_lock = threading.Lock()   # ONNX 引擎不是线程安全的。
                           # 现在只有手动通道用它，但自动通道（定时截屏）
                           # 迟早会并发进来，锁先放这儿。


def get_engine():
    """引擎只建一次，常驻复用。

    建一次要 0.3~0.7 秒，每次识别都重建的话，用户能明显感到卡顿。
    """
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    return _engine


def ocr_lines(img_path):
    """图片 → [(文本, 置信度)]，已按 MIN_SCORE 过滤。

    返回列表而不是元组，方便上层接着做规则初筛。
    """
    with _lock:
        res = get_engine()(img_path)
    txts = list(res.txts or [])
    scores = list(res.scores or [])
    return [(t, float(s)) for t, s in zip(txts, scores) if s >= MIN_SCORE]
