# -*- coding: utf-8 -*-
"""RapidOCR 最小用法示例 —— 照着这个用就行。"""
import os
import rapidocr

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "A_正常字号_白底.png")

print("=" * 60)
print("0. 包装在哪")
print("=" * 60)
print(f"  {os.path.dirname(rapidocr.__file__)}")
print(f"  版本: {getattr(rapidocr, '__version__', '未知')}")

print()
print("=" * 60)
print("1. 创建引擎（只做一次，加载模型约 0.7 秒）")
print("=" * 60)
from rapidocr import RapidOCR          # 第一步：导入
engine = RapidOCR()                     # 第二步：建引擎
print("  完成")

print()
print("=" * 60)
print("2. 把图片丢给它")
print("=" * 60)
result = engine(IMG)                    # 第三步：识别，传图片路径
print("  完成")

print()
print("=" * 60)
print("3. 取结果")
print("=" * 60)
print(f"  返回类型: {type(result).__name__}")
fields = [a for a in dir(result) if not a.startswith("_") and not callable(getattr(result, a))]
print(f"  可用字段: {fields}")

print()
print("  【最常用的 result.txts】—— 识别出的文字，一行一条:")
for i, line in enumerate(result.txts, 1):
    print(f"    {i}. {line}")

print()
print("  其他字段:")
for f in ("scores", "boxes", "elapse"):
    if hasattr(result, f):
        v = getattr(result, f)
        if isinstance(v, (list, tuple)) and len(v) > 2:
            print(f"    {f}: ({type(v).__name__}, 共 {len(v)} 项) 前两项示例 {list(v)[:2]}")
        else:
            print(f"    {f}: {v}")

print()
print("=" * 60)
print("4. 写成一句话就是")
print("=" * 60)
print("""
    from rapidocr import RapidOCR
    engine = RapidOCR()
    result = engine("图片路径.png")
    for line in result.txts:
        print(line)
""")
