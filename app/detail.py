# -*- coding: utf-8 -*-
"""「查看详情」的多轮会话 —— 状态与上下文拼装，**只这一份实现**。

用户在卡片上点「查看详情」，弹出一个对话框，先拿到一段比卡片长很多的解释，
然后可以接着追问（「它和 X 有什么区别」「举个例子」）。

────────────────────────────────────────────────────────
它和 `ai.explain()` 是【两条路】，别混：

  · `ai.explain()` —— 管线 ②。一次调用筛完 + 写完所有词条，输出固定 JSON。
    硬约束 2 管着它：不许拆成「先挑词、再逐个解释」。
  · `ai.chat()`     —— 这个文件服务的。一问一答，输出是**大白话**不是 JSON。
    多轮天然就是「问几次调几次」，硬约束 2 说的不是它。

  两条路共用的只有 `ai.BASE_URL` / `ai.MODEL` / 那几个 HTTP 错误翻译，
  以及那条「模型输出永远当不可信输入」的态度。

────────────────────────────────────────────────────────
⚠ **为什么这个模块不碰 settings、不碰 HTTP、不碰 pywebview**：

  它是纯状态机，这样才能在 `冒烟测试\详情联调.py` 里不起窗口、不联网地
  把「缓存命中 / 重置 / 上下文截断」这几件事逐条验掉。
  提示词由调用方（`main.Api._system_prompt("detail")`）选好了传进来 ——
  这里再判一次的话，同一个判断就有了两处实现（这个项目一贯反对那个）。

────────────────────────────────────────────────────────
⚠ **会话只在内存里，故意不落盘。** 2026-09-21 用户拍板：

  > 同一个词，第二次点「查看详情」—— 这次开着就记住，关程序就忘

  所以这里没有任何 `json.dump` / sqlite / 文件读写。**别顺手加持久化** ——
  那会变成第二个知识库，而且「库中 N 条」那个数的含义会跟着变。
"""

# 一轮 = 一问 + 一答。
#
# ★ 为什么必须有这个上限：多轮对话每追问一句，就要把【之前的全部】再发一遍。
#   不封顶的话，问到第 50 轮时输入 token 是第 1 轮的几十倍 —— 而用户
#   完全感觉不到自己在越问越贵。这和 `ai.py` 里 MAX_LINES / MAX_CANDIDATES
#   是同一条理由，只是那条管的是 ②。
#
# ⚠ **截断了要【说出来】。** 静默丢历史的话，用户会看到模型突然「忘了」
#   前面聊过的内容，而界面上一切正常 —— 那正是这个项目最怕的那种失败：
#   不报错，只是结果不对。所以 build_context() 会把丢掉的轮数一并返回，
#   由 main.py 送到前端，在对话框里明说。
MAX_TURNS = 20


def seed_question(term, line=""):
    """第一轮自动发出去的那句话。

    ★ 它同时是【给模型的输入】和【界面上那个「你：…」气泡的文字】——
      同一个字符串，不是两份。两份的话迟早分叉，而分叉了不报错，
      只是用户看到的和他实际问出去的不是一回事。

    ⚠ 原句（`line`）跟着第一句一起发，因为它是【只有前端才知道】的东西：
      `terms` 表里没存上下文（见 kb.py 的 schema），出了这一轮就再也拿不到了。
      第一句不带，模型这辈子不知道这个词是在什么语境里出现的。
    """
    q = f"「{term}」是什么？请详细讲讲。"
    if line:
        q += f"\n\n（它出现在这段话里：{line}）"
    return q


def build_context(msgs, max_turns=MAX_TURNS):
    """会话历史 → (发给模型的那一段, 被丢掉的【问题】条数)。

    ⚠ 返回的是**条数**不是布尔：调用方要靠它决定界面上说不说、
      以及说「丢了 3 轮」还是「丢了 1 轮」。返回 True/False 的话，
      那句提示就只能写成含糊的「早期内容已省略」—— 用户不知道省了多少，
      也就没法判断要不要重新开一个会话。
    """
    msgs = list(msgs or [])
    keep = max(1, int(max_turns)) * 2          # 一轮 = user + assistant 两条
    if len(msgs) <= keep:
        return msgs, 0

    kept = msgs[-keep:]
    # ⚠ 必须从一个 user 开始。截出来第一句是 assistant 的话，模型看到的是
    #   「自己刚说过一段话却没人问」，很容易接着那句话自己往下编，
    #   而不是回答用户真正问的那句。（正常情况下对齐是好的，
    #   这一步是兜底：将来谁改了写入顺序，这里不会静默出错。）
    while kept and kept[0].get("role") != "user":
        kept.pop(0)

    cut = len(msgs) - len(kept)
    dropped_q = sum(1 for m in msgs[:cut] if m.get("role") == "user")
    return kept, dropped_q


class Chats:
    """内存里的会话表。key 是知识库主键（`kb.canon()` 的产物）。

    ⚠ 用 key 不用 term 当主键，理由和知识库那边一模一样：
      `Chunk` / `Chunking` 是两条会话，而 `RAG` / `rag` 是同一条。
      拿显示用的拼写当键的话，同一个词换个大小写就变成新会话 ——
      用户会觉得「刚才聊过的它又忘了」，而且是静默的。
    """

    def __init__(self):
        self._d = {}

    def has(self, key):
        return bool(key) and key in self._d

    def get(self, key):
        """拿到会话记录（**不是副本**，调用方别改它）。没有就 None。"""
        return self._d.get(key)

    def start(self, key, term, line=""):
        """开一条新会话。**已存在就原样返回旧的，不重置** ——

        ★ 这一条就是用户拍板的「这次开着就记住」的落点。写反了
          （每次 start 都清空）的话，功能看着是好的，只是**每次点都花钱**，
          而且用户完全看不出哪里不对。
        """
        if key in self._d:
            return self._d[key]
        rec = {"term": term or "", "line": line or "", "msgs": []}
        self._d[key] = rec
        return rec

    def say(self, key, role, text):
        """往会话里追加一条。role: "user" | "assistant"。

        ⚠ 只收这两角色。`system` 由调用方在【发请求那一刻】现拼 ——
          存进来的话，用户在操作台改了提示词，这条会话后面几轮还会
          抱着旧的那份不放，而他明明刚改完（决策 37 要的是「改了立刻生效」）。
        """
        if role not in ("user", "assistant"):
            raise ValueError("role 只能是 user / assistant")
        rec = self._d.get(key)
        if rec is None:
            return None
        rec["msgs"].append({"role": role, "text": text or ""})
        return rec

    def reset(self, key):
        """丢掉这条会话。下一次 open 就是全新的一轮（会重新花钱）。

        ⚠ 用 `pop` 不用 `clear` —— 会话没在的时候也得当成功。
          「删除一个不存在的东西」报错的话，前端那个「重新问」按钮
          会因为一次重复点击弹一个没头没脑的错。
        """
        self._d.pop(key, None)

    def count(self):
        """当前开着几条会话。给联调脚本断言用，也给将来可能的状态行用。"""
        return len(self._d)

    def keys(self):
        return list(self._d.keys())
