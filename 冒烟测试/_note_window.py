# -*- coding: utf-8 -*-
"""陪练窗口：把示例笔记显示在固定的屏幕位置，供框选。由联调脚本生成。

⚠ 开头那三行不是装饰，缺一样这块区域就【不一定是笔记】：

   1. **先声明 DPI 感知**。不声明的话这个进程会被 Windows 虚拟化，
      `geometry` 里的 1040x540 实际落成 1560x810，整块位置也跟着偏，
      而联调脚本是按 NOTE_RECT 去截的 —— 截到的就不是这个窗口。

   2. **置顶**。不置顶的话，用户随手点一下自己的窗口就把它盖住了，
      截出来的是别人屏幕上正在看的东西。真发生过：某一轮识别出的是
      一篇讲数字人直播的文章，候选词全是 OCR 噪声。
      （框选覆盖层在本窗口之后创建，同样是置顶，会压在它上面，不影响框选。）
"""
import ctypes
import sys
import tkinter as tk

ctypes.windll.shcore.SetProcessDpiAwareness(2)      # ★ 必须在建窗口之前

x, y, w, h = (int(v) for v in sys.argv[1:5])
path = sys.argv[5]

root = tk.Tk()
root.title("示例笔记")
root.geometry("%dx%d+%d+%d" % (w, h, x, y))
root.attributes("-topmost", True)
t = tk.Text(root, font=("Microsoft YaHei UI", 15), wrap="word")
t.insert("1.0", open(path, encoding="utf-8").read())
t.pack(fill="both", expand=True)
root.lift()
root.mainloop()
