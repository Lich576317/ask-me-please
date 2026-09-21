/* ══════════════════════════════════════════════════════════
   问了吗 · 前端交互（骨架版）
   后端 = app/main.py 里的 Api 类，通过 window.pywebview.api 调用
   ══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const api = () => window.pywebview.api;

  /* OCR 出来的文字是【不可信输入】—— 它来自屏幕上任意内容，
     谁也不能保证里面没有 <script> 或 "></div>。
     凡是拼进 innerHTML 的都必须先过这一层。 */
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* 调试标记。这个窗口的 devtools 不是随手能开的，
     所以把「脚本加载了吗 / 桥就绪了吗」挂在 window 上，方便从外部探。 */
  window.__WL = { scriptLoaded: true, booted: false, bridgeReady: false };

  /* ⚠ 默认 System Prompt 住在后端（app/prompt.py），这里不再存一份。
     两份各写一遍的话，改了一边忘了另一边，界面上显示的和实际发给模型的就对不上了。
     这里只负责把它填进文本框给用户看。 */
  let state = {};

  /* ★ 管线重入锁。
     自动通道会和手动撞上，而【手动那边原本一处保护都没有】：
     #btn-recognize 在 ② 期间会被 askAi 禁用，但 #btn-pick 一直是可点的 ——
     框选完顺手再点一次「立即识别」，两条管线就并发了。
     后端 recognize() 自己也加了锁，但那只护住 _last_todo / SHOT_REGION
     两个单槽状态；「同一时刻只有一条管线」这件事由这里说了算。 */
  let running = false;

  /* ── 启动 ───────────────────────────────────────────── */
  function boot() {
    bindWindow();
    bindModel();
    bindCapture();
    bindKB();
    bindSettle();
    bindConsole();
    bindStream();
    bindPrompt();
    bindAuto();
    bindHotkey();
    loadSettings();
  }

  /* 前后端的桥不一定什么时候就绪，可能在 app.js 执行前，也可能之后。

     ⚠ 判断条件必须是「方法真的能调用」，不能只看 window.pywebview.api
       这个对象在不在 —— pywebview 会先建一个空壳对象，方法稍后才注入。
       只看对象是否存在的话会抢跑，然后报 get_settings is not a function。 */
  function bridgeReady() {
    const a = window.pywebview && window.pywebview.api;
    return !!(a && typeof a.diag === 'function' && typeof a.get_settings === 'function');
  }

  let started = false;
  function startOnce() {
    window.__WL.bridgeReady = bridgeReady();
    if (started || !window.__WL.bridgeReady) return;
    started = true;
    window.__WL.booted = true;
    // 报给后端。这个窗口的 devtools 不是随手能开的，
    // 出问题时全靠这条把前端状态送到能看到的地方。
    try { api().report('启动', JSON.stringify(window.__WL)); } catch (e) { /* 上报失败不该影响启动 */ }
    boot();
  }

  window.addEventListener('pywebviewready', startOnce);
  let tries = 0;
  const poll = setInterval(() => {
    startOnce();
    if (started || ++tries > 200) clearInterval(poll);
  }, 50);

  /* 任何 JS 报错都写到诊断条上 —— 别让错误静悄悄烂在控制台里，
     这个窗口的 devtools 不是随手能开的。 */
  window.addEventListener('error', (e) => {
    const d = document.getElementById('diag');
    if (d) d.textContent = '⚠ JS 报错：' + (e.message || e.type);
    try { api().report('JS报错', e.message || e.type); } catch (_) {}
  });

  /* ══════════ 窗口控制 ══════════ */
  function bindWindow() {
    const menu = $('dockmenu');

    $('btn-move').addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
    });

    menu.querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', async () => {
        menu.hidden = true;
        apply(await api().dock(b.dataset.edge));
      });
    });

    document.addEventListener('click', () => { menu.hidden = true; });

    // 折叠 → 只剩一条标题栏
    $('btn-min').addEventListener('click', async () => {
      apply(await api().collapse());
    });

    // 展开 → 回到该边的完整尺寸
    $('btn-max').addEventListener('click', async () => {
      apply(await api().dock(state.edge || 'right'));
    });

    // ⚠ 关闭确认走【页面内的模态框】，不再用原生 confirm()。
    //   原生那个是 WebView2 说了算的：位置由视口决定（面板是一条窄栏，
    //   它看上去就挂在边上），样式也改不了。见 index.html 里的说明。
    $('btn-close').addEventListener('click', () => { $('closebox').hidden = false; });
    $('close-no').addEventListener('click', () => { $('closebox').hidden = true; });
    $('close-yes').addEventListener('click', () => {
      $('closebox').hidden = true;
      api().close_app();
    });
    // 点遮罩（框外）也当取消 —— 模态框的通行做法。
    // ⚠ 判据是 e.target === 遮罩本身：直接写「点 overlay 就关」的话，
    //   点在框里的按钮上，事件冒泡上来也会把它当成点了外面。
    $('closebox').addEventListener('click', (e) => {
      if (e.target === $('closebox')) $('closebox').hidden = true;
    });

    // 齿轮 ⇄ 返回。用 hidden 切换两个 <main>，不用路由也不用重建 DOM ——
    // 两边都是常驻的，切回来时输入框里还在原处。
    $('btn-console').addEventListener('click', () => showConsole(true));
    $('btn-console-back').addEventListener('click', () => showConsole(false));
    $('btn-kb-manage').addEventListener('click', () => showConsole(true));
  }

  /* 操作台 ⇄ 条目页。
     ⚠ 切走之前要把操作台里的东西刷新一遍：用户可能是从「库中 3 条」
       进来的，而这一页上的数字是他上次打开时的 —— 中间自动通道跑过几轮，
       库早就不止 3 条了。不刷的话这一页会【静静地显示过期数据】。 */
  async function showConsole(on) {
    $('view-stream').hidden = on;
    $('view-console').hidden = !on;
    $('btn-console').dataset.on = String(on);
    if (!on) return;
    try {
      const s = await api().kb_stats();
      renderKb({ kb: s.kb, dir: s.dir });
      renderBlocked(s.excluded || []);
    } catch (e) { /* 刷新失败不该挡住切换 */ }
  }

  /* ══════════ 自动通道 ══════════

     分工：后端只判「到点了」，管线还是【这一条】runPipeline 跑（硬约束 4）。
     所以这里收到 fire 就等价于「用户停手、画面也停了满 1 分钟」。 */
  let autoTimer = null;

  function bindAuto() {
    const el = $('sw-auto');

    el.addEventListener('click', async () => {
      const want = el.dataset.on !== 'true';
      el.dataset.on = String(want);            // 先动外观，别等一个来回
      try {
        const r = await api().auto_set(want);
        el.dataset.on = String(r.on);
      } catch (e) {
        el.dataset.on = String(!want);         // 存不下就退回去，别让界面说谎
        note('⚠ 自动通道开关失败：' + e);
        return;
      }
      // 后端在 auto_set 里已经 tick 过一次，所以这里立刻能拿到真实状态
      await pollAuto();
      syncAutoTimer();
    });
  }

  /* ⚠ 只在开着的时候轮询。关着还每 2 秒问一次的话，每次都是一条
     pywebview 新开的非守护线程（见 main.py close_app 的说明），纯属白烧。 */
  function syncAutoTimer() {
    const on = $('sw-auto').dataset.on === 'true';
    if (on && !autoTimer) {
      autoTimer = setInterval(pollAuto, 2000);
    } else if (!on && autoTimer) {
      clearInterval(autoTimer);
      autoTimer = null;
    }
  }

  async function pollAuto() {
    let p;
    try {
      p = await api().auto_poll();
    } catch (e) {
      return;                                  // 轮询失败不写进面板，下一秒就好了
    }
    if (!p || !p.ok) return;

    $('sw-auto').dataset.on = p.on ? 'true' : 'false';
    $('sw-auto').dataset.busy = String(p.phase === 'busy' || p.phase === 'fired');
    $('sw-label').textContent = p.label || '自动';
    if (p.hint) $('sw-auto').title = p.hint;

    // ★ 到点了 → 跑【同一条】管线。
    //   running 为真时不跑：那说明手动那条正在跑，而它刚跑的正是这一屏，
    //   没必要再花一次钱（那一轮跑完的 auto_report 会把「这屏跑过了」钉上）。
    if (p.fire && !running) runPipeline(true);
  }

  /* ══════════ 快捷键取词（v0.20 / 决策 50） ══════════

     用户在原程序里选中一段字、按 Ctrl+E，热键线程把那段字从剪贴板
     借回来置个标记；**这里轮询发现之后，跑的是同一条 runPipeline()**
     （硬约束 4：管线只有一份，热键那条线程一行管线代码都没有）。

     ⚠ **为什么是轮询，而不是后端推过来**：`winforms.py` 的 `evaluate_js`
       没有包 `self.Invoke(...)`，从非 GUI 线程调它有风险 ——
       和 main.py 拒绝 `create_file_dialog` 是同一条理由。

     ⚠ **为什么是【常开】轮询，不学 syncAutoTimer 挂在开关上**：
       自动通道关着就是关着；而全局热键的意义恰恰是「人在飞书里、
       面板在背后」时也能按。它跟面板可不可见、当前在哪个视图都无关，
       所以这里是结构性常开，不是忘了加开关。

     周期定 500ms 是折中：2000ms（自动通道那个）会让「按下去立刻出」
     变成「按下去等两秒」。 */
  const HOTKEY_POLL_MS = 500;
  let hotkeyChainOn = false;
  let hotkeySeq = 0;              // 已经处理过的最后一次按键号
  let recording = false;

  /* 自续的 setTimeout 链，不是 setInterval。

     ⚠ 差别是【在途保护】：setInterval 到点就发，上一发还没回来（② 最长
       60 秒 + pywebview 的开线程开销）就再发一条，越堆越多 ——
       而每一条都是 pywebview 新开的【非守护线程】，正是 os._exit(0)
       当初要绕开的那种东西。这条链把下一拍排在 await 之后，天然只有一发在途。 */
  function syncHotkeyTimer() {
    if (hotkeyChainOn) return;
    hotkeyChainOn = true;
    scheduleHotkey();
  }

  function scheduleHotkey() {
    setTimeout(async () => {
      try { await pollHotkey(); } catch (e) { /* 轮询失败不写进面板，下一秒就好 */ }
      if (hotkeyChainOn) scheduleHotkey();
    }, HOTKEY_POLL_MS);
  }

  async function pollHotkey() {
    if (recording) return;            // 录制期间后端是挂起的，问也白问
    let p;
    try {
      p = await api().hotkey_poll();
    } catch (e) {
      return;
    }
    if (!p || !p.ok) return;
    markHotkey(p);

    if (!p.seq || p.seq === hotkeySeq) return;      // 没有新按键

    // 取词就失败了（剪贴板里不是纯文字 / 什么都没选中）。
    // ⚠ 这种【要当场销号】：后端不会再重试，不 ack 的话这条失败通知
    //   会每 500ms 重放一遍，面板上那句话永远关不掉。
    if (!p.text) {
      hotkeySeq = p.seq;
      await api().hotkey_ack(p.seq);
      note('⚠ ' + (p.reason || '没抓到文字'));
      return;
    }

    // 上一轮还在跑。★ 这时【先不销号】—— 后端 poll 是「读了还在，
    //   确认了才清」，下一拍这段文字还在，照样跑得了。
    //   销了才是真丢了：用户按了快捷键，然后面板上什么都不发生。
    if (running) {
      setStatus('已抓到你选中的文字，等上一轮跑完接着解释…');
      return;
    }

    const ran = await runPipeline(false, 'clip');
    if (ran) {                        // 只有真跑了才算用掉
      hotkeySeq = p.seq;
      await api().hotkey_ack(p.seq);
    }
  }

  /* 把后端的【生效值】画到界面上。
     ★ 一律用回读值，不用用户刚按的那个 —— 注册失败时 set_combo 会
       保留旧组合，照乐观值画就是显示一个没生效的键（同 markSettle）。 */
  function markHotkey(p) {
    const combo = (p && p.combo) || 'Ctrl+E';
    const ok = !(p && p.available === false);
    $('hotkey-now').textContent = ok ? combo : combo + '（不可用）';
    $('btn-hk-record').disabled = !ok;
    if (p && p.err) $('hotkey-msg').textContent = '⚠ ' + p.err;
  }

  /* ══════════ 录制新快捷键 ══════════ */

  // 只按修饰键不算一次组合 —— 用户按 Ctrl+E 时，Ctrl 会先单独到一次，
  // 不挡的话「Ctrl」本身就被当成一个组合记下来了。
  const MOD_CODES = new Set(['ControlLeft', 'ControlRight', 'AltLeft', 'AltRight',
                             'ShiftLeft', 'ShiftRight', 'MetaLeft', 'MetaRight']);

  /* e.code → 主键名。

     ★ 用 code 不用 key：按 Shift+E 时 `e.key` 是 `'E'`（大写）、
       按住 Shift 打数字时 `e.key` 干脆是 `'!'`，只有 code 是稳定的。
       两处混用的话，「Ctrl+Shift+E」和「Ctrl+e」会被记成同一个键，
       而它们在后端是两个不同的组合。 */
  function mainKeyFromCode(code) {
    if (/^Key[A-Z]$/.test(code)) return code.slice(3);
    if (/^Digit[0-9]$/.test(code)) return code.slice(5);
    if (/^F([1-9]|1[0-2])$/.test(code)) return code;
    return null;                      // 别的键后端也不收，见 hotkey.validate
  }

  async function startRecording() {
    if (recording) return;
    recording = true;
    // ★ 进去之前必须先把后端的全局热键停掉。不停的话，用户在录制界面里
    //   按下的那一下会【真的触发一次取词】—— 半秒后冒出「没抓到文字」，
    //   同时那串键还被前台程序照常收走。
    try {
      await api().hotkey_suspend();
    } catch (e) {
      recording = false;
      $('hotkey-msg').textContent = '⚠ 停不下原来的快捷键：' + e;
      return;
    }
    $('btn-hk-record').dataset.rec = 'true';
    $('btn-hk-record').textContent = '按下组合键…（Esc 取消）';
    $('hotkey-msg').textContent = '现在按下你想用的组合键。至少要带 Ctrl / Alt / Shift / Win 里的一个。';
  }

  async function stopRecording(resume = true) {
    if (!recording) return;
    recording = false;
    $('btn-hk-record').dataset.rec = 'false';
    $('btn-hk-record').textContent = '录制新快捷键';
    if (resume) {
      // ⚠ 恢复走 set_hotkey（它自己会重新注册），成功那条路上后端已经把
      //   _suspended 清掉了。只有取消录制时才需要单独 resume。
      try { await api().hotkey_resume(); } catch (e) { /* 下一步 poll 会显示错误 */ }
    }
    await pollHotkey();
  }

  async function onRecordKey(e) {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();

    if (e.key === 'Escape') { await stopRecording(); return; }
    if (MOD_CODES.has(e.code)) return;         // 等他按到主键

    const mods = [];
    if (e.ctrlKey) mods.push('Ctrl');
    if (e.altKey) mods.push('Alt');
    if (e.shiftKey) mods.push('Shift');
    if (e.metaKey) mods.push('Win');
    const main = mainKeyFromCode(e.code);
    if (!main) {
      $('hotkey-msg').textContent = '这个键不支持。主键请用 A~Z、0~9 或 F1~F12。';
      return;
    }
    const combo = mods.concat([main]).join('+');

    recording = false;                          // 先离开录制态，别再吃后面的键
    $('btn-hk-record').dataset.rec = 'false';
    $('btn-hk-record').textContent = '录制新快捷键';

    let r;
    try {
      r = await api().set_hotkey(combo);
    } catch (err) {
      $('hotkey-msg').textContent = '⚠ 保存快捷键失败：' + err;
      await api().hotkey_resume();
      return;
    }
    // ★ 失败时后端会把【当前生效值】带回来，界面上照它画 ——
    //   照我们自己输的那个画，会显示一个实际没生效的键。
    markHotkey({ combo: r.combo, available: true, err: '' });
    if (!r.ok) {
      $('hotkey-msg').textContent = '⚠ ' + (r.reason || '这个键不支持') + '　（还是用 ' + r.combo + '）';
      return;
    }
    $('hotkey-msg').textContent = r.risky
      ? '⚠ 已改为 ' + r.combo + '。这个键和你平时复制用的 Ctrl+C 撞了 —— 以后每次复制都会触发一次解释，建议换一个。'
      : '已改为 ' + r.combo + '。在飞书 / Obsidian 里选中文字，按它试试。';
    await pollHotkey();
  }

  function bindHotkey() {
    $('btn-hk-record').addEventListener('click', () => {
      if (recording) stopRecording(); else startRecording();
    });
    document.addEventListener('keydown', onRecordKey, true);
  }

  /* ══════════ 模型接入 ══════════ */
  function bindModel() {
    $('btn-key').addEventListener('click', async () => {
      const key = $('api-key').value.trim();
      if (!key) {
        $('key-msg').textContent = '请先填入 API Key';
        return;
      }
      const r = await api().save_api_key(key);
      if (r.ok) {
        // ⚠ 这里必须同步更新 state.api_key_set。
        //   它决定「立即识别」跑不跑 ② —— 存了 Key 却不更新的话，
        //   用户得重启程序才生效，而界面上完全看不出为什么没反应。
        state.api_key_set = true;
        $('vendor').hidden = false;
        $('api-key').value = '';
        $('key-msg').textContent = '已保存到本机。点击「接入」可测试连通性。';
      }
    });

    $('vendor').addEventListener('click', async () => {
      $('key-msg').textContent = '测试中…';
      const r = await api().test_connection();
      $('key-msg').textContent = r.msg;
    });
  }

  /* ══════════ 取词区域 + 识别 ══════════ */
  function bindCapture() {
    $('btn-pick').addEventListener('click', async () => {
      $('region-text').textContent = '正在截屏，稍等一下…';
      try {
        const r = await api().pick_region();
        $('region-text').textContent = r && r.ok
          ? '屏幕上已经铺开一张快照，拖动鼠标框选要识别的区域（Esc 取消）'
          : '⚠ 打开框选失败';
        if (r && r.ok) watchRegion();
      } catch (e) {
        $('region-text').textContent = '⚠ 打开框选失败：' + e;
      }
    });

    $('btn-recognize').addEventListener('click', async () => {
      if (!state.region) {
        $('region-text').textContent = '还没框选区域 —— 先点左边的「框选区域」';
        return;
      }
      await runPipeline();
    });
  }

  /* 自动/手动【共用】的一条管线（硬约束 4）。

     ⚠ runPipeline 之外【不许】有第二份 recognize→explain 的实现。
       两条通道的区别只有「谁按的开始」和第一句话怎么说。 */
  async function runPipeline(auto, mode) {
    if (running) return false;                 // 见 running 的说明
    mode = mode || 'region';
    running = true;
    try {
      await runPipelineOnce(auto, mode);
      return true;                             // 真跑了。快捷键那边靠它决定要不要 ack
    } finally {
      running = false;
      /* ★ 手动跑完【也要】报。不报的话：用户刚点完「立即识别」，
         自动通道立刻对同一屏再跑一遍，白花一次钱。
         另外，发了火却被 running 挡下没跑的那次，也靠这一句把后端 busy 清掉。

         ⚠ 但 clip 通道【跳过】这一句（v0.20 / 决策 50）。
           auto.report() 的 done 有两个副作用：清 busy，**以及把「这一屏
           已经跑过了」钉上**（后端 auto.py 里 _fired_this_screen = True）。
           快捷键解释的是【剪贴板里的那段字，它根本没读那一屏】——
           把「这屏跑过了」钉住，等于白白压掉一次本该发生的自动解释，
           而且是静默的：用户只会觉得自动通道「有时候不灵」。
           框选/自动那条照旧上报 —— 它们读的确实就是那一屏，理由仍然成立。 */
      if (mode !== 'clip') {
        try { await api().auto_report('done'); } catch (e) { /* 上报失败不影响结果 */ }
      }
    }
  }

  /* ① 本地跑完立刻出结果，② 再拿去问模型。
     两段【故意分开渲染】，不攒到最后一起显示 ——
     ① 只要两秒，② 要十几秒。全等完再显示的话，用户会对着空面板干等十几秒。 */
  async function runPipelineOnce(auto, mode) {
    mode = mode || 'region';
    busy(true, auto ? '自动识别中…（你停手了）'
                    : (mode === 'clip' ? '正在解释你选中的文字…'
                                       : '正在截屏识别…（OCR 约 2 秒）'));
    let r1;
    try {
      r1 = await api().recognize(mode);
    } catch (e) {
      busy(false);
      note('⚠ 识别失败：' + e);
      return;
    }
    busy(false);
    renderStage1(r1);

    if (!r1.ok) return;

    const todo = r1.ai_todo || [];

    // ⚠ 判空要按 ai_todo 判，不能按 candidates 判。
    //   candidates 是 ① 的完整产物，里面可能全被知识库回答了。
    //   那样还去调 ② 的话，等于给模型发一个空候选列表 ——
    //   不光白烧 token，它还可能自己从原文里编出几条不属于候选的词来。
    // ⚠ clip 通道【故意不等】这个判空（v0.20 / 决策 50）。
    //   用户选中一段纯中文时，规则初筛（只认英文字母）一个词都给不出来，
    //   但中文术语本来就要靠 ② 从原文里挑 —— 在这里 return 掉的话，
    //   ② 永远调不到，用户按了快捷键，面板上一片空白。
    //   后端 explain() 那边放行了同样一道闸门，**两处必须同时放**：
    //   只放后端，前端在这里就断了；只放前端，后端会返回「没有需要问模型的词」。
    //
    // ⚠⚠ 但【白名单模式】下 clip 也不许绕（2026-09-21 补，决策 52）。
    //   白名单的契约是「不在库里的词一概不显示」——它就写在设置页那行说明里
    //   （见 POLICY_TEXT）。而 clip 的中文路径一个候选都没有，
    //   ② 从原文里挑出来的词【必然不在库里】：放它过去，等于刚在面板上
    //   说完「白名单模式：没有知识库收录的词」，紧接着又画出一堆库外的卡片。
    //   这不是「功能少了」，是**产品当着用户的面破掉自己写的规矩**。
    //   两处闸门必须同时收：只收前端，后端还会被直接调用；只收后端，
    //   前端会停在「正在问模型…」上不动。
    if (!todo.length && (mode !== 'clip' || r1.policy === 'whitelist')) {
      setStatus(skipReason(r1, mode));
      return;
    }

    if (!state.api_key_set) {
      setStatus('②  还没填 API Key，上面只有知识库命中的和规则初筛的候选。'
              + '填上 Key 才会筛选并给解释。');
      return;
    }

    await askAi(r1, mode);
  }

  /* 跳过 ② 时状态行该说什么。

     ⚠ 这个分支必须【显式】设状态。askAi 一进去就把状态改成「正在问模型…」，
       而这里根本不会进 askAi —— 不设的话，状态行会停在上一次那句话上，
       用户会以为程序卡在问模型那一步。 */
  function skipReason(r, mode) {
    const hits = (r.kb_hits || []).length;
    const blocked = (r.blocked || []).length;
    if (hits) {
      return `知识库已回答全部 ${hits} 个候选，本次没调模型 —— 省了一次提问。`;
    }
    if (blocked) {
      return '候选全被挡掉了（不在知识库 / 已加入排除名单），本次没调模型。';
    }
    // ★ 白名单 + 取词（2026-09-21 补）。
    //   白名单的契约是「不在库里的词一概不显示」，所以这条通道【故意不绕】
    //   空候选那道闸门（见 runPipeline 里那段）。
    //   但这里不能沿用下面 clip 那句「没找到值得解释的术语」—— 那句话是假的：
    //   选中这段很可能满篇都是术语，它们只是【不在库里】。
    //   ⚠ 中文尤其要说清楚：① 的规则初筛只认英文字母，中文一个候选都给不出来，
    //     所以白名单模式下中文取词【结构上】永远不会命中 —— 不是这次运气不好。
    if (mode === 'clip' && r.policy === 'whitelist') {
      return `白名单模式只解释知识库里已有的词；取词这条通道没有本地词表`
           + `（中文尤其如此），本次没有命中库内词，没调模型。`;
    }
    // ⚠ clip 这条路在【非白名单】下正常走不到 —— 上面那道判空对 clip 放行了。
    //   留着是因为万一将来有人把放行条件改回去，这句好歹说的是人话，
    //   而不是让状态行停在上一次那句话上（那才是真的看不出发生了什么）。
    if (mode === 'clip') {
      return '这段文字里没找到值得解释的术语。';
    }
    return '没捞到候选词。';
  }

  /* ② 单独一段。失败【不推翻】 ① 的结果 ——
     候选已经捞出来了，模型连不上是模型的事，用户至少还能看到原文。 */
  async function askAi(r1, mode) {
    mode = mode || 'region';
    setStatus('正在问模型…（十几秒，别关窗口）');
    $('btn-recognize').disabled = true;

    let r2;
    try {
      r2 = await api().explain(r1.ai_payload, mode);
    } catch (e) {
      setStatus('⚠ 问模型失败：' + e + '　（上面仍是 ① 的原始候选）');
      $('btn-recognize').disabled = false;
      return;
    }
    $('btn-recognize').disabled = false;

    if (!r2 || !r2.ok) {
      setStatus('⚠ ' + ((r2 && r2.msg) || '问模型失败') + '　（上面仍是 ① 的原始候选）');
      return;
    }
    renderStage2(r1, r2);
  }

  /* 框选是【覆盖层】那个窗口干的活，结果写进后端 settings，
     本窗口并不知道。所以框选完之后盯一会儿，等它出现。

     ⚠ 不盯的话有两个后果，都是真出过的：
       · 卡片一直停在「拖动鼠标框选…」，用户以为没成功
       · state.region 还是 null，接着点「立即识别」会误报「还没框选区域」 */
  function watchRegion() {
    let n = 0;
    const t = setInterval(async () => {
      if (++n > 40) {                    // 约 20 秒还没动静就放弃
        clearInterval(t);
        return;
      }
      try {
        const r = await api().get_region();
        if (r && r.region) {
          state.region = r.region;
          renderRegion(r.region);
          clearInterval(t);
        }
      } catch (e) {
        clearInterval(t);
      }
    }, 500);
  }

  function busy(on, text) {
    $('btn-recognize').disabled = on;
    $('btn-pick').disabled = on;
    if (on) { note(text); }
  }

  function note(text) {
    $('stream-body').innerHTML = '<p class="stream-note">' + esc(text) + '</p>';
  }

  function renderRegion(rect) {
    if (!rect) {
      $('region-pill').textContent = '未设置';
      $('region-text').textContent = '还没框选。点下面按钮，框出笔记所在的那块屏幕。';
      return;
    }
    const [l, t, w, h] = rect;
    $('region-pill').textContent = '已设置';
    $('region-text').textContent = `区域 ${w} × ${h}，屏幕坐标 (${l}, ${t})`;
  }

  /* 状态行是【独立一格】的，因为 ② 要等十几秒。
     整块重绘的话，等待期间用户看不见任何东西；
     留着这一行原地改字，用户才知道程序还活着、在干什么。 */
  function setStatus(text) {
    const el = $('stream-status');
    if (el) el.textContent = text;      // 没有就跳过 —— 诊断不该把主流程带走
  }

  /* 状态行按【通道】分叉。

     ⚠ clip（快捷键取词）通道压根没有截屏、没有 OCR —— 文字是热键线程
       从剪贴板借回来的。照旧打「截屏 0.00s　OCR 0.00s」不是"数字不好看"，
       而是**凭空报告了两个这一轮从未发生的步骤**，还都带着精确到小数点后
       两位的耗时。用户会拿着这两个 0.00 去问「为什么是 0」。
       它自己的那个数是「选中了多少字」。
     `r.mode` 由后端带回来（见 main.py 的 _recognize_locked 返回值），
     所以这里不用把 mode 再往下透传一层。 */
  function statLine(r) {
    const tail = `初筛 ${(r.candidates || []).length}　`
               + `库里已有 ${(r.kb_hits || []).length}　`
               + `问模型 ${(r.ai_todo || []).length}　`
               + `挡掉 ${(r.blocked || []).length}`;
    if (r.mode === 'clip') {
      const chars = (r.lines || []).join('').length;
      return `选中 ${chars} 字　识别 ${r.lines.length} 行　${tail}`;
    }
    return `截屏 ${r.t_capture}s　OCR ${r.t_ocr}s　识别 ${r.lines.length} 行　${tail}`;
  }

  /* 把 ③ 分好的三桶合并回 ① 的候选顺序。

     ⚠ 这一步非做不可。分桶之后如果按「先画库里的、再画要问的」来渲染，
       ② 回来时卡片会【整体重排一次】—— 两段渲染本来就有一次跳变，
       顺序再变一次，用户会看不清哪张是哪张。
       按 ① 的顺序走，② 回来只是往已有卡片里补字，位置一个都不动。 */
  function bucketize(r) {
    const hits = new Map((r.kb_hits || []).map((h) => [h.key, h]));
    const todo = new Map((r.ai_todo || []).map((t) => [t.key, t]));
    const blocked = new Map((r.blocked || []).map((b) => [b.key, b]));
    return (r.candidates || []).map((c) => ({
      key: c.key,
      term: c.term,
      line: c.line,
      hit: hits.get(c.key) || null,
      todo: todo.get(c.key) || null,
      blocked: blocked.get(c.key) || null,
    }));
  }

  /* 术语超过这个长度就截断标题，完整的挪到正文第一行「原句：…」。
     面板只有 380px 宽，一整句英文当标题会折成四五条，把解释挤没了。

     ⚠ 截的只是【显示】。it.term 本身一直完整 ——
       右键排除、知识库主键用的都还是它。别在哪天顺手把 term 也截了。 */
  const TERM_TITLE_MAX = 40;

  function entryCard(it, explanation) {
    const term = it.term || '';
    const clipped = term.length > TERM_TITLE_MAX;
    const title = clipped ? term.slice(0, TERM_TITLE_MAX) + '…' : term;

    // 来源标记【两边都标】。只标知识库那一侧的话，等库养熟了、
    // 大部分卡片都带标记，那张没标记的反而显得像没渲染完。
    const pill = it.hit
      ? '<span class="pill pill-kb">来自知识库</span>'
      : (explanation ? '<span class="pill pill-plain">AI 新解</span>' : '');

    return `<article class="entry" data-term="${esc(term)}" data-key="${esc(it.key)}">
      <div class="entry-head">
        <div class="entry-term"${clipped ? ` title="${esc(term)}"` : ''}>${esc(title)}</div>${pill}
      </div>
      ${clipped ? `<p class="entry-quote">原句：${esc(term)}</p>` : ''}
      ${explanation
          ? `<p class="entry-body">${esc(explanation)}</p>`
          : `<p class="entry-src">${esc(it.line)}</p>`}
    </article>`;
  }

  /* ① 落地就画。

     知识库命中的卡片这一轮【就已经是完整的】—— 有解释、带标记、不花钱。
     要问模型的那批此刻只有「哪个词、出自哪一行」。 */
  function renderStage1(r) {
    if (!r || !r.ok) {
      $('stream-count').textContent = '0 条';
      note((r && r.msg) || '识别失败');
      return;
    }

    const items = bucketize(r);
    const shown = items.filter((it) => !it.blocked);

    $('stream-count').textContent = shown.length + ' 条';

    let html = `<p class="stat" id="stream-status">${esc(statLine(r))}</p>`;

    // 白名单模式下的空态。不给的话就是一片空白，和「程序坏了」长得一模一样。
    if (!shown.length && r.policy === 'whitelist') {
      // ⚠ 文案里的「笔记」要按通道分叉：取词选中的可能是飞书里的一句话，
      //   不是笔记 —— 说成「这段笔记」会让用户以为程序看错了地方。
      //   ★ 而且取词这条通道要【明说不会去问模型】：中文没有本地词表，
      //     白名单下必然一条都命中不了。不说的话，用户按一次 Ctrl+E
      //     得到一片空白，唯一合理的猜测是「快捷键坏了」。
      html += r.mode === 'clip'
        ? `<p class="stream-note">白名单模式只解释知识库里已有的词；`
          + `取词这条通道没有本地词表（中文尤其如此），所以这次不会去问模型。`
          + `（共收录 ${r.kb_total} 个）</p>`
        : `<p class="stream-note">白名单模式：这段笔记里没有知识库收录的词。`
          + `（共收录 ${r.kb_total} 个）</p>`;
    } else if (!shown.length) {
      // ⚠ clip 通道说「没捞到英文候选词」是错的：它本来就不限中英，
      //   而且 **候选为空是它的常态**（纯中文时 ① 那条正则网必然给不出候选，
      //   术语全靠 ② 从原文里挑），此时 ② 还没回来，下面这张空态
      //   只是【② 出发前】的过渡态。照着旧文案写，用户会以为已经结束了。
      html += r.mode === 'clip'
        ? '<p class="stream-note">选中的这段里没有命中知识库的词，正在交给模型挑术语…</p>'
        : '<p class="stream-note">这块区域里没捞到英文候选词。</p>';
    }

    for (const it of shown) {
      html += entryCard(it, it.hit ? it.hit.explanation : '');
    }

    html += notShownBlock(r, null);
    html += rawBlock(r);
    $('stream-body').innerHTML = html;
  }

  /* 「本次没显示的词」—— 一块 details 装全部。

     ⚠ 按【原因】分组，不要按阶段分成两块。
       「AI 没采纳」是不可操作的（那是模型的判断）；
       「不在知识库」「已排除」是可操作的（切策略 / 恢复）。
       把可操作的和不可操作的并排放在两个折叠区里，用户不知道该动哪个。 */
  function notShownBlock(r1, r2) {
    const merged = [];        // 被某个整条收进去了
    const aiDropped = [];     // 模型真的没解释
    if (r2) {
      const entries = r2.entries || [];
      const kept = new Set(entries.map((e) => e.key));

      // ★ 被某个整条 covers 罩住的，是【并进去了】，不是【没采纳】。
      //   两者混在一行「AI 未采纳」下是不实的：模型采纳了它，
      //   只是作为那一整条的一部分（`Long Short-Term Memory` 里的 `Memory`）。
      const covered = new Set();
      for (const e of entries) for (const k of (e.covers || [])) covered.add(k);

      for (const t of (r1.ai_todo || [])) {
        if (kept.has(t.key)) continue;
        (covered.has(t.key) ? merged : aiDropped).push(t.term);
      }
    }

    const notInKb = (r1.blocked || []).filter((b) => b.why === 'not_in_kb');
    const excluded = (r1.blocked || []).filter((b) => b.why === 'excluded');
    const over = r1.over_limit || 0;

    const total = merged.length + aiDropped.length + notInKb.length + excluded.length;
    if (!total && !over) return '';

    const rows = [];
    if (notInKb.length) {
      rows.push(`<div class="missrow"><b>不在知识库</b>`
              + `<span>${esc(notInKb.map((b) => b.term).join('　'))}</span>`
              + `<button class="btn btn-tiny" id="miss-to-repeat">改成可重复解释</button></div>`);
    }
    if (excluded.length) {
      rows.push(`<div class="missrow"><b>已排除</b>`
              + `<span>${esc(excluded.map((b) => b.term).join('　'))}</span>`
              + `<button class="btn btn-tiny" id="miss-restore">恢复</button></div>`);
    }
    if (merged.length) {
      rows.push(`<div class="missrow"><b>已并入整条</b>`
              + `<span>${esc(merged.join('　'))}</span></div>`);
    }
    if (aiDropped.length) {
      rows.push(`<div class="missrow"><b>AI 未采纳</b>`
              + `<span>${esc(aiDropped.join('　'))}</span></div>`);
    }
    if (over) {
      rows.push(`<div class="missrow"><b>超出上限</b>`
              + `<span>还有 ${over} 个候选没发出去（一次最多 60 个）</span></div>`);
    }

    // 这句只在「AI 没采纳」出现时才说得通。
    // 加了知识库之后无条件挂着它，对「不在知识库」那组就是错的。
    const tail = aiDropped.length
      ? '<p class="msg">① 的网是往宽了撒的，捞多了是对的 —— 剔除本来就该由 ② 来做。</p>'
      : '';

    return `<details class="raw"${r2 ? ' open' : ''}>
      <summary>本次没显示的 ${total + over} 个词</summary>
      ${rows.join('')}${tail}
    </details>`;
  }

  /* ② 回来之后重画。把解释补进【已经在那儿的】卡片里。

     ⚠ 这里【不做任何归一化】。以前前端自己算一份
       `key = s => s.toLowerCase().replace(/[^a-z0-9]/g,'')`，
       现在后端在每条候选和每条解释上都带好了 `key`，前端只做字符串相等比较。

       理由：这个 key 现在是【知识库主键】。算错的不再只是「剔除清单不好看」，
       而是钱和正确性 —— 库里的 RAG 命不中、每轮重新问一次。
       两份实现各写一遍，迟早会分叉，而且分叉了不报错，只是变贵。
       归一化只该有后端一份（kb.canon），前端零份。 */
  function renderStage2(r1, r2) {
    const entries = r2.entries || [];
    const byKey = new Map(entries.map((e) => [e.key, e]));
    const items = bucketize(r1);

    // ★ 被整条收进去的候选不再单独成卡 —— 它们已经在那一整条里解释过了。
    //   `Long Short-Term Memory` 拆成「长 + 短期 + 记忆 + 网络」是废话（见 app/prompt.py）。
    const covered = new Set();
    for (const e of entries) for (const k of (e.covers || [])) covered.add(k);
    const shown = items.filter((it) => !it.blocked && !covered.has(it.key));

    // ★ ② 还会带回 ① 根本没捞到的整条（固定词组、成句的英文）。
    //   **卡片不能只由候选表生成** —— 那样这些整条连一张卡都没有，
    //   解释凭空消失，用户看到的反而是满屏没有解释的单词卡。
    const candKeys = new Set((r1.candidates || []).map((c) => c.key));
    const extras = entries.filter((e) => !candKeys.has(e.key));

    $('stream-count').textContent = (shown.length + extras.length) + ' 条';

    const hits = (r1.kb_hits || []).length;
    // ⚠ 口径要和 notShownBlock 里那个一致：数「ai_todo 里没被 ② 解释到的」。
    //   以前写的是 ai_todo.length - entries.length —— 两个集合口径不同，
    //   ② 每带回一条非候选的整条，这个差就少 1，掺够了还会变负，
    //   再被 Math.max(0,…) 悄悄夹成 0：不报错，只是少算了。
    const keptKeys = new Set(entries.map((e) => e.key));
    const droppedByAi = (r1.ai_todo || []).filter((t) => !keptKeys.has(t.key)).length;
    const notShown = (r1.blocked || []).length + droppedByAi + (r1.over_limit || 0);

    let head = `知识库 ${hits} 条（瞬时）　AI 新增 ${entries.length} 条（${r2.seconds}s）`;
    if (notShown) head += `　未显示 ${notShown} 个`;

    let html = `<p class="stat" id="stream-status">${esc(head)}</p>`;

    if (!entries.length) {
      // clip 的措辞必须改两处，都是实打实的错：
      //   ① 那条通道不限中英，说「英文术语」是错的；
      //   ② clip 发过去的候选**通常就是空的**，模型是从【原文】里挑的 ——
      //      说「这批发过去的候选里没有」把责任推给了一张空表，
      //      用户照着这句话去查候选，永远查不出所以然。
      html += r1.mode === 'clip'
        ? '<p class="stream-note">模型没从这段文字里挑出要解释的术语。'
          + '（觉得漏了可以改 System Prompt）</p>'
        : '<p class="stream-note">模型认为这批发过去的候选里，没有需要解释的英文术语。'
          + '（觉得漏了可以改 System Prompt）</p>';
    }

    for (const it of shown) {
      const e = it.todo ? byKey.get(it.key) : null;
      // AI 没给解释的那张卡会退回显示原文出处，看起来就像 ① 还没跑完 ——
      // 这是有意的：它确实还只有证据，没有结论。
      html += entryCard(it, it.hit ? it.hit.explanation : (e ? e.explanation : ''));
    }
    // 整条排在后面：① 的卡片位置一个都不动，② 只是多出几张新的。
    for (const e of extras) {
      html += entryCard({ key: e.key, term: e.term, line: '', hit: null }, e.explanation);
    }

    html += notShownBlock(r1, r2);
    html += rawBlock(r1);
    $('stream-body').innerHTML = html;
  }

  function rawBlock(r) {
    // ⚠ clip 通道没有 OCR。这里原来一律打 「[置信度] 行」，
    //   而 clip 的 scores 是后端补的假 1.0 —— 打出来就是一条条
    //   「[1.0] …」，看着像 OCR 认得特别准。**标签撒谎比没有标签更坏**：
    //   用户会拿这个 1.0 去判断识别质量，而这一轮根本没识别过。
    const clip = r.mode === 'clip';
    const rawLines = r.lines
      .map((l, i) => clip
        ? `${String(i + 1).padStart(2)}. ${l}`
        : `${String(i + 1).padStart(2)}. [${r.scores[i]}] ${l}`)
      .join('\n');

    return `<details class="raw">
      <summary>${clip ? '看选中的原文与发给 AI 的载荷' : '看原始 OCR 与发给 AI 的载荷'}</summary>
      <pre>${esc(rawLines)}</pre>
      <p class="msg">② 的 user message 就是下面这个（System Prompt 见下方折叠区）：</p>
      <pre>${esc(JSON.stringify(r.ai_payload, null, 2))}</pre>
    </details>`;
  }

  /* ══════════ 知识库 ══════════ */

  /* 一次性操作的反馈，统一写这里。
     ⚠ v0.11 起这些控件都搬进了操作台，所以反馈也只有操作台看得到 ——
       条目页那份 #kb-msg 是【固定的说明文字】（解释过的词会进库…），
       不再被操作覆盖。以前两者共用一个元素，操作反馈会把说明冲掉。 */
  function kbMsg(t) {
    const el = $('kb-msg-c');
    if (el) el.textContent = t;
  }

  function bindKB() {
    $('kb-policy').querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', async () => {
        const r = await api().set_kb_policy(b.dataset.policy);
        markPolicy(r.policy);
        renderKb(r);
      });
    });

    $('btn-harvest').addEventListener('click', async () => {
      const r = await api().kb_harvest();
      renderKb(r);
      kbMsg(`收纳历史：新增 ${r.added}　更新 ${r.updated}　跳过 ${r.skipped}`);
    });

    $('btn-export').addEventListener('click', async () => {
      const r = await api().kb_export('all');
      kbMsg(r.ok ? `已导出 ${r.count} 条 → ${r.path}` : `导出失败：${r.msg}`);
    });

    $('btn-dir').addEventListener('click', async () => {
      kbMsg('正在打开选择框…');
      const r = await api().kb_choose_dir();
      if (r.cancelled) { kbMsg('已取消'); return; }
      if (!r.ok) { kbMsg(r.msg + (r.detail ? '：' + r.detail : '')); return; }
      $('kb-dir').value = r.dir;
      kbMsg('导出目录已设为 ' + r.dir);
    });

    // ⚠ 输入框是【可编辑】的 —— 选择框只是一个便利，不是唯一入口。
    //   万一哪天对话框挂了，「导出 md」照样要能用。
    $('kb-dir').addEventListener('change', async () => {
      const r = await api().kb_set_dir($('kb-dir').value);
      $('kb-dir').value = r.dir;
    });

    // ⚠ 只有导入还没接。文案不能再笼统说「骨架阶段」——
    //   另外三个都接上了，那时候骨架早就不叫骨架了。
    $('btn-import').addEventListener('click', () => {
      kbMsg('导入自定义知识库排在下一版（导出格式已经为它留好了）');
    });
  }

  /* ══════════ 自动通道的等待阈值 ══════════ */

  function settleMsg(t) {
    const el = $('settle-msg');
    if (el) el.textContent = t;
  }

  /* ⚠ 生效值【只有后端说了算】（main.get_settings 的 auto_settle_effective）。
     前端不自己写一遍「空/非法就用 15」—— 两份判断迟早分叉，
     而分叉了不报错，只是界面上显示的和实际用的对不上。
     也没必要：后端已经把「真正生效的那个数」算好送过来了。

     ⚠ 生效值可能【不是四个档位里的任何一个】—— 联调时
       WENLEMA_AUTO_SETTLE 会盖过用户设置（那时是 3）。
       这种情况下四个按钮一个都不亮，这是【诚实的显示】：
       确实有个覆盖在起作用。别为了「总得亮一个」去硬凑。 */
  function markSettle(sec) {
    const v = Number(sec);
    $('auto-settle').querySelectorAll('button').forEach((b) => {
      b.dataset.active = String(Number(b.dataset.settle) === v);
    });
    const now = $('settle-now');
    if (now) now.textContent = v >= 60 ? `${v / 60} 分钟` : `${v} 秒`;
  }

  function bindSettle() {
    const box = $('auto-settle');
    if (!box) return;
    box.querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', async () => {
        const r = await api().set_auto_settle(Number(b.dataset.settle));
        if (!r.ok) {
          // 后端有白名单。正常点按钮走不到这里，走到了说明前端数据和后端对不上。
          settleMsg('这个值不被接受（' + r.reason + '），界面可能和后端不一致，重启一下。');
          return;
        }
        // ★ 用后端【回读】的数画选中态，不用按钮上那个数 ——
        //   和 markPolicy(r.policy) 一个道理：保存失败时界面不会被乐观值带偏。
        markSettle(r.effective);
        settleMsg(r.effective === Number(b.dataset.settle)
          ? `已设为 ${b.textContent.trim()}。`
          : `已设为 ${b.textContent.trim()}，但实际生效的是 ${r.effective} 秒 —— 有联调覆盖在起作用。`);
      });
    });
  }

  /* ⚠ 策略现在有【两处显示】：条目页那句 #kb-policy-now（只看），
     操作台那三个按钮（能点）。两处都从这一个函数走，
     否则切完策略回到条目页，那句说明还是旧的。 */
  const POLICY_TEXT = {
    dedupe: '不重复解释 —— 库里有的直接用，不再问模型',
    repeat: '可重复解释 —— 忽略库，全部重新问一遍',
    whitelist: '只解释库内 —— 不在库里的词一概不显示',
  };

  function markPolicy(policy) {
    $('kb-policy').querySelectorAll('button').forEach((b) => {
      b.dataset.active = String(b.dataset.policy === policy);
    });
    const now = $('kb-policy-now');
    if (now) now.textContent = POLICY_TEXT[policy] || POLICY_TEXT.dedupe;
  }

  /* ⚠ 「库中几条」和「操作反馈」是【两件事】，别混：
       kb-count 常驻显示「库里现在有多少条」
       kb-msg   只做一次性操作的反馈，下次操作会被覆盖
     以前两者共用一个元素，结果是「操作反馈」把「库里有几条」冲掉了。 */
  function renderKb(r) {
    if (r && r.kb) {
      let t = `库中 ${r.kb.terms} 条`;
      if (r.kb.excluded) t += `　已排除 ${r.kb.excluded}`;
      // 两处都写：条目页是状态，操作台是操作时的参照。
      // 只写一处的话，另一处就是【静静地显示过期数字】。
      $('kb-count').textContent = t;
      const c = $('kb-count-c');
      if (c) c.textContent = t;
    }
    if (r && r.dir !== undefined) $('kb-dir').value = r.dir;
  }

  /* ══════════ 屏蔽词 ══════════
     复用 ③ 的排除名单（决策 21）：右键「不再解释该词」加的就是这份。
     这里只是给它一个【看得见、删得掉】的地方 —— 没有这一页的话，
     右键加进去的词就再也找不回来了。 */
  function renderBlocked(list) {
    const box = $('blk-list');
    const n = $('blk-count');
    if (n) n.textContent = `${list.length} 个`;
    if (!box) return;
    if (!list.length) { box.innerHTML = ''; return; }
    box.innerHTML = list.map((x) => `
      <div class="blk-item">
        <span>${esc(x.term || x.key)}</span>
        <button class="btn btn-tiny" data-unblk="${esc(x.key)}">恢复</button>
      </div>`).join('');
  }

  /* 名单和计数一起刷新。
     ⚠ kb_exclude() 的返回里【没有】excluded 列表（只有计数），kb_unexclude() 才有 ——
       与其记住哪个返回什么，不如两处都回头读一次 kb_stats()：
       名单是唯一真相，谁改完都从它那儿重新拉一遍。 */
  async function refreshBlocked() {
    const r = await api().kb_stats();
    renderKb(r);
    renderBlocked(r.excluded || []);
    return r;
  }

  function bindConsole() {
    const add = async () => {
      const term = $('blk-input').value.trim();
      if (!term) { $('blk-msg').textContent = '先输入一个词'; return; }
      await api().kb_exclude(term);
      $('blk-input').value = '';
      await refreshBlocked();
      $('blk-msg').textContent = `已屏蔽「${term}」—— 以后不再解释，也不会发给模型`;
    };

    $('btn-blk-add').addEventListener('click', add);
    // 回车即添加。这是「输入框 + 按钮」的通行做法，缺了它会显得很钝。
    $('blk-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); add(); }
    });

    // ⚠ 事件委托：列表是整体重绘的，逐行绑会在第一次刷新后全部失效
    //   （这个坑在 #stream-body 上踩过一次，见 bindStream 的注释）。
    $('blk-list').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-unblk]');
      if (!b) return;
      await api().kb_unexclude(b.dataset.unblk);
      await refreshBlocked();
      $('blk-msg').textContent = '已恢复，下次识别会重新出现';
    });
  }

  /* ══════════ 条目右键菜单 ══════════

     ⚠ 用【事件委托】绑在 #stream-body 上，不要逐张卡片绑。
       stream-body 是整体 innerHTML 重绘的（①② 各画一次），
       逐卡片绑的监听器在第一次重绘后就全部失效了 ——
       症状是「有时候能右键，有时候不能」，而且刷新一次就好，极难查。 */
  function bindStream() {
    const body = $('stream-body');

    body.addEventListener('contextmenu', (e) => {
      // 不拦掉的话，弹出来的是 WebView2 自带的菜单（Reload / Inspect），
      // 在一个桌面小工具里露出来很奇怪
      e.preventDefault();
      const card = e.target.closest('.entry[data-key]');
      if (!card || !card.dataset.key) { hideCtx(); return; }
      showCtx(card.dataset.key, card.dataset.term, e.clientX, e.clientY);
    });

    body.addEventListener('click', async (e) => {
      if (e.target.id === 'miss-to-repeat') {
        const r = await api().set_kb_policy('repeat');
        markPolicy('repeat'); renderKb(r);
        kbMsg('已改成「可重复解释」，重新识别一次即可重新问模型');
        return;
      }
      if (e.target.id === 'miss-restore') {
        const list = (await api().kb_stats()).excluded || [];
        for (const x of list) await api().kb_unexclude(x.key);
        await refreshBlocked();
        kbMsg(`已恢复 ${list.length} 个被排除的词`);
        return;
      }
    });

    $('ctx-exclude').addEventListener('click', async () => {
      const m = $('ctxmenu');
      const key = m.dataset.key, term = m.dataset.term;
      hideCtx();
      await api().kb_exclude(term);
      // ⚠ 走 refreshBlocked 而不是只 renderKb：操作台那一页的屏蔽词名单
      //   也得跟着变。右键是这份名单的另一个入口，两边不同步的话，
      //   用户在操作台会看到一个【少了一条】的名单，而那条恰恰是他刚加的。
      await refreshBlocked();
      toast(`已不再解释「${term}」`, '撤销', async () => {
        await api().kb_unexclude(key);
        await refreshBlocked();
        kbMsg(`已恢复「${term}」，下次识别会重新出现`);
      });
    });

    document.addEventListener('click', hideCtx);
    document.addEventListener('scroll', hideCtx, true);
    window.addEventListener('blur', hideCtx);
  }

  function showCtx(key, term, x, y) {
    const m = $('ctxmenu');
    m.dataset.key = key;
    m.dataset.term = term;
    m.hidden = false;

    // 面板很窄（v0.11 起 520 物理 = 346 CSS），菜单贴着右边缘会把字切掉，
    // 往里收一下。⚠ 这里读的是【运行时】宽度，别再写死一个数 ——
    // 写死的话改了 PANEL_W 之后，这段没有任何地方会报错，只是悄悄失灵。
    const w = m.offsetWidth || 150, h = m.offsetHeight || 40;
    const maxX = document.documentElement.clientWidth - w - 6;
    const maxY = document.documentElement.clientHeight - h - 6;
    m.style.left = Math.max(4, Math.min(x, maxX)) + 'px';
    m.style.top = Math.max(4, Math.min(y, maxY)) + 'px';
  }

  function hideCtx() {
    const m = $('ctxmenu');
    if (m) m.hidden = true;
  }

  /* 排除是个【单向门】，提示里必须带撤销。
     用户手一滑右键一个常用词，它就从所有地方消失了 ——
     没有撤销和「已排除」列表的话，那不叫功能，叫数据丢失。 */
  function toast(text, actionLabel, onAction) {
    const t = $('toast');
    $('toast-text').textContent = text;
    const b = $('toast-act');
    b.textContent = actionLabel || '';
    b.hidden = !actionLabel;
    b.onclick = async () => { t.hidden = true; if (onAction) await onAction(); };
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 6000);
  }

  /* ══════════ System Prompt ══════════ */
  function bindPrompt() {
    const body = $('prompt-body');

    $('btn-prompt-toggle').addEventListener('click', () => {
      body.hidden = !body.hidden;
      $('btn-prompt-toggle').textContent = body.hidden ? '展开' : '收起';
    });

    // 还原 = 把后端给的那份默认填回文本框，用户还得点保存才算数 ——
    // 免得手一滑就把自己写的提示词冲掉了。
    $('btn-prompt-reset').addEventListener('click', () => {
      $('prompt-text').value = promptDefault(promptWhich());
      $('prompt-msg').textContent = '已填回默认（还没保存）';
    });

    $('btn-prompt-save').addEventListener('click', async () => {
      const r = await api().save_prompt($('prompt-text').value, promptWhich());
      $('prompt-msg').textContent = r.using_default
        ? '已保存。和默认一致，以后默认改进了你也跟着用新的。'
        : '已保存。这段是你自己的，以后不会跟着默认改。';
    });

    // ── 两份提示词的切换（v0.20）──────────────────────────────
    // ⚠ 切换时必须【先把当前这份存回去吗】—— 不存。理由：编辑框里那段
    //   没保存的字属于哪一份，只有用户自己知道；自动替他存，
    //   就是「我只是看了一眼那一份，结果把没写完的稿子发出去了」。
    //   所以切走再切回来**不保留草稿**，但下面会把这件事说清楚。
    for (const b of document.querySelectorAll('#prompt-which .seg-btn')) {
      b.addEventListener('click', () => {
        markPromptWhich(b.dataset.which);
        $('prompt-msg').textContent = '没保存的修改已经丢弃 —— 现在编辑的是这一份。';
      });
    }
  }

  /* 当前在编辑哪一份提示词。

     ⚠ 选中态用的是 `data-active`，和 markPolicy / markSettle 一致 ——
       样式表里写的是 `.seg-btn[data-active="true"]`。用别的属性名的话
       点了没反应，而且不报错（选择器匹配不上就是没有样式，不是错误）。 */
  function promptWhich() {
    const b = document.querySelector('#prompt-which .seg-btn[data-active="true"]');
    return (b && b.dataset.which) || 'region';
  }

  function promptDefault(which) {
    return which === 'clip'
      ? (state.default_prompt_any || '')
      : (state.default_prompt || '');
  }

  /* 切到某一份，并把编辑框换成它的内容。
     ★ 一律读后端回读的值（state.system_prompt / system_prompt_any），
       不保留前端草稿 —— 理由同上，草稿属于哪一份是件说不清的事。 */
  function markPromptWhich(which) {
    for (const b of document.querySelectorAll('#prompt-which .seg-btn')) {
      b.dataset.active = String(b.dataset.which === which);
    }
    const saved = which === 'clip' ? state.system_prompt_any : state.system_prompt;
    $('prompt-text').value = saved || promptDefault(which);
    $('prompt-msg').textContent = saved
      ? '正在编辑你自己的这一份。'
      : '正在编辑这一份，当前用的是默认提示词。';
  }

  /* ══════════ 载入设置 ══════════ */
  async function loadSettings() {
    try {
      state = await api().get_settings();
    } catch (e) {
      // 把桥接对象上真实存在的方法名打出来，出问题一眼能看出是「没就绪」还是「名字不对」
      const keys = Object.keys((window.pywebview && window.pywebview.api) || {});
      $('diag').textContent =
        '⚠ 后端接口调用失败：' + e + '　桥上有：[' + keys.join(', ') + ']';
      return;
    }

    if (state.api_key_set) $('vendor').hidden = false;
    markPolicy(state.kb_policy || 'dedupe');
    markSettle(state.auto_settle_effective);
    renderKb({ kb: state.kb, dir: state.kb_dir_effective });
    renderBlocked(state.excluded || []);
    // ⚠ 走 markPromptWhich，不要在这里直接写 $('prompt-text').value ——
    //   两份提示词各有各的默认值，直接写死一份的话，切到另一份时
    //   编辑框里躺着的还是这一份的字，而标题说你在编辑那一份。
    markPromptWhich('region');
    renderRegion(state.region);

    // 自动通道的开关是【落盘】的：重启后接着上次的状态。
    // 这里只是把界面同步过去 —— 后端在 Api.__init__ 里已经按 settings 起好了。
    $('sw-auto').dataset.on = state.auto_on ? 'true' : 'false';
    if (state.auto_on) pollAuto();
    syncAutoTimer();

    // 快捷键那条【无条件常开】，和不常开的 syncAutoTimer 挨着写，
    // 就是为了让这个差别显眼 —— 理由见 syncHotkeyTimer 上面的说明。
    markHotkey(state.hotkey || {});
    pollHotkey();
    syncHotkeyTimer();

    if (!state.seen_notice) {
      $('notice').hidden = false;
    }

    $('btn-notice-ok').addEventListener('click', async () => {
      $('notice').hidden = true;
      await api().dismiss_notice();
    });

    refreshDiag();
  }

  /* ══════════ 窗口状态回读 ══════════ */
  async function refreshDiag() {
    try {
      apply(await api().diag());
    } catch (e) {
      $('diag').textContent = '⚠ 读窗口状态失败：' + e;
    }
  }

  function apply(d) {
    if (!d) return;
    state.edge = d.edge;

    document.body.dataset.dock = d.edge;

    const names = { left: '左', right: '右', top: '上', bottom: '下' };
    $('diag').textContent =
      `停靠：${names[d.edge] || d.edge}` +
      `　尺寸：${d.size[0]}×${d.size[1]}` +
      `　屏幕：${d.monitor[0]}×${d.monitor[1]}` +
      `　置顶：${d.topmost ? '是' : '否'}` +
      `　无边框：${d.frameless ? '是' : '否'}`;
  }
})();
