// browser-handoff bridge — MV3 service worker
//
// 作用：把「你正在用的浏览器」暴露给本地 MCP 服务（browser_bridge_win.py）。
// 只连 127.0.0.1，不访问任何外部服务器；不读密码、不注入脚本，
// 只在被要求时取回当前标签页的 innerText。
//
// 协议：
//   GET  /hello?agent=<edge|chrome>&token=…   探活
//   GET  /poll?agent=<edge|chrome>&token=…    长轮询取命令（最多挂 15 秒）
//   POST /result  {id, ok, data, error}       回传结果（带 X-Bridge-Token）
//
// 会同时连接所有应答的桥（8790-8799），因此即使有旧的桥占着端口，
// 新桥也能收到命令。
//
// 令牌由安装器写入同目录的 token.js；开发/自检环境下为空，服务端也就不校验。

try { importScripts('token.js'); } catch (e) { /* 没有 token.js 时按无令牌运行 */ }
const TOKEN = (typeof self.BRIDGE_TOKEN === 'string') ? self.BRIDGE_TOKEN : '';
const QS_TOKEN = TOKEN ? `&token=${encodeURIComponent(TOKEN)}` : '';

const PORTS = [8790, 8791, 8792, 8793, 8794, 8795, 8796, 8797, 8798, 8799];
const AGENT = detectAgent();
const HELLO_TIMEOUT = 600;
const POLL_TIMEOUT = 25000;
const RESCAN_MS = 5000;

const bases = new Set();   // 已连接并在服务的桥

function detectAgent() {
  const ua = navigator.userAgent || '';
  if (ua.includes('Edg/')) return 'edge';
  return 'chrome';
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function hello(url) {
  try {
    const r = await fetch(`${url}/hello?agent=${AGENT}&t=${Date.now()}${QS_TOKEN}`,
                          { signal: AbortSignal.timeout(HELLO_TIMEOUT) });
    if (!r.ok) return false;
    const j = await r.json();
    return !!(j && j.ok && j.name === 'browser-handoff');
  } catch (e) {
    return false;
  }
}

async function serve(base) {
  while (bases.has(base)) {
    let msg;
    try {
      const r = await fetch(`${base}/poll?agent=${AGENT}${QS_TOKEN}`,
                            { signal: AbortSignal.timeout(POLL_TIMEOUT) });
      msg = await r.json();
    } catch (e) {
      bases.delete(base);       // 桥没了：交给下一轮扫描重连
      return;
    }
    if (!msg || typeof msg.id !== 'string' || !msg.command) continue;

    let res;
    try {
      res = await execute(msg.command.tool, msg.command.args || {});
    } catch (e) {
      res = { ok: false, error: (e && e.message) || String(e) };
    }
    // 结果回传失败会让工具误报「没有响应」，但动作其实已经生效了；
    // 重试一次，仍失败才放弃（桥下一轮扫描会重新发现）。
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await fetch(`${base}/result`, {
          method: 'POST',
          headers: TOKEN
            ? { 'Content-Type': 'application/json', 'X-Bridge-Token': TOKEN }
            : { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: msg.id, ok: res.ok, data: res.data,
                                 error: res.error || null }),
          signal: AbortSignal.timeout(5000),
        });
        break;
      } catch (e) {
        if (attempt === 0) await sleep(300);
      }
    }
  }
}

async function scan() {
  for (const port of PORTS) {
    const base = `http://127.0.0.1:${port}`;
    if (bases.has(base)) continue;
    if (await hello(base)) {
      bases.add(base);
      serve(base);
    }
  }
}

async function activeTab() {
  let tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs.length) tabs = await chrome.tabs.query({ active: true });
  return tabs[0] || null;
}

async function execute(tool, args) {
  switch (tool) {
    case 'list_tabs': {
      const tabs = await chrome.tabs.query({});
      const windowNo = new Map();
      const out = [];
      for (const t of tabs) {
        if (!windowNo.has(t.windowId)) windowNo.set(t.windowId, windowNo.size + 1);
        out.push({
          window: windowNo.get(t.windowId),
          tab: (t.index === undefined ? 0 : t.index) + 1,
          title: t.title || '',
          url: t.url || '',
        });
      }
      return { ok: true, data: { tabs: out } };
    }

    case 'current_tab': {
      const t = await activeTab();
      if (!t) return { ok: true, data: {} };
      return { ok: true, data: { title: t.title || '', url: t.url || '' } };
    }

    case 'get_text': {
      const t = await activeTab();
      if (!t) return { ok: false, error: '没有打开的窗口。' };
      if (!/^https?:/i.test(t.url || '')) {
        return { ok: false, error: '当前页面不是 http/https（浏览器内置页面不允许读取）。' };
      }
      try {
        const [res] = await chrome.scripting.executeScript({
          target: { tabId: t.id },
          func: () => (document.body
            ? document.body.innerText
            : (document.documentElement ? document.documentElement.innerText : '')),
        });
        return { ok: true, data: { text: (res && res.result) || '' } };
      } catch (e) {
        return { ok: false, error: (e && e.message) || String(e) };
      }
    }

    case 'snapshot': {
      const t = await activeTab();
      if (!t) return { ok: false, error: '没有打开的窗口。' };
      if (!/^https?:/i.test(t.url || '')) {
        return { ok: false, error: '当前页面不是 http/https（浏览器内置页面不允许操作）。' };
      }
      try {
        const [res] = await chrome.scripting.executeScript({
          target: { tabId: t.id },
          func: () => {
            const SEL = 'a[href],button,input,textarea,select,summary,'
              + '[role="button"],[role="link"],[role="textbox"],[role="checkbox"],'
              + '[role="radio"],[role="combobox"],[role="tab"],[role="menuitem"],'
              + '[contenteditable="true"],[contenteditable=""],[onclick]';
            const nodes = [];
            const seen = new Set();
            for (const el of document.querySelectorAll(SEL)) {
              if (seen.has(el)) continue;
              seen.add(el);
              const r = el.getBoundingClientRect();
              const st = getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden'
                  || r.width === 0 || r.height === 0) continue;
              nodes.push(el);
              if (nodes.length >= 200) break;
            }
            const roleOf = (el) => {
              const tag = el.tagName.toLowerCase();
              const role = el.getAttribute('role');
              if (role) return role;
              if (tag === 'a') return 'link';
              if (tag === 'button') return 'button';
              if (tag === 'textarea') return 'textbox';
              if (tag === 'select') return 'combobox';
              if (tag === 'input') {
                const ty = (el.getAttribute('type') || 'text').toLowerCase();
                if (ty === 'checkbox') return 'checkbox';
                if (ty === 'radio') return 'radio';
                if (ty === 'submit' || ty === 'button') return 'button';
                return 'textbox';
              }
              return 'button';
            };
            const labelOf = (el) => {
              const isInput = el instanceof HTMLInputElement;
              const bits = [
                el.getAttribute('aria-label'),
                el.getAttribute('placeholder'),
                el.getAttribute('alt'),
                el.getAttribute('title'),
                isInput && (el.type === 'submit' || el.type === 'button') ? el.value : null,
                (el.innerText || '').trim(),
              ].filter(Boolean);
              return String(bits[0] || '').replace(/\s+/g, ' ').slice(0, 80);
            };
            // ref 存在隔离世界的 Map 里（页面看不见，也不改 DOM 属性）：
            // 既避免污染用户页面，也避免页面脚本读到/伪造 ref。
            const store = new Map();
            globalThis.__bhRefs = store;

            const lines = [];
            nodes.forEach((el, i) => {
              const ref = 'e' + (i + 1);
              store.set(ref, el);
              const extra = [];
              if (el.disabled) extra.push('disabled');
              if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
                if (el.value) extra.push('value=' + JSON.stringify(String(el.value).slice(0, 60)));
              }
              lines.push('- ' + roleOf(el) + ' ' + JSON.stringify(labelOf(el))
                + ' [ref=' + ref + ']'
                + (extra.length ? ' (' + extra.join(', ') + ')' : ''));
            });
            return {
              url: location.href,
              title: document.title,
              count: nodes.length,
              tree: lines.join('\n'),
            };
          },
        });
        return { ok: true, data: (res && res.result) || {} };
      } catch (e) {
        return { ok: false, error: (e && e.message) || String(e) };
      }
    }

    case 'click': {
      const t = await activeTab();
      if (!t) return { ok: false, error: '没有打开的窗口。' };
      const ref = String(args.ref || '');
      if (!/^e\d+$/.test(ref)) return { ok: false, error: 'ref 形如 e3，请先 snapshot。' };
      try {
        const [res] = await chrome.scripting.executeScript({
          target: { tabId: t.id },
          args: [ref],
          func: (ref) => {
            const store = globalThis.__bhRefs;
            const el = store && store.get(ref);
            if (!el) return { ok: false, error: '找不到 ' + ref + '（页面可能变了），请重新 snapshot。' };
            if (el.isConnected === false) {
              return { ok: false, error: ref + ' 已从页面移除，请重新 snapshot。' };
            }
            el.scrollIntoView({ block: 'center', inline: 'center' });
            if (el.focus) el.focus();
            const info = {
              tag: el.tagName.toLowerCase(),
              text: String(el.innerText || el.value || '').trim().slice(0, 60),
            };
            el.click();
            return { ok: true, clicked: info };
          },
        });
        const r = (res && res.result) || {};
        return r.ok === false ? { ok: false, error: r.error } : { ok: true, data: r };
      } catch (e) {
        return { ok: false, error: (e && e.message) || String(e) };
      }
    }

    case 'type': {
      const t = await activeTab();
      if (!t) return { ok: false, error: '没有打开的窗口。' };
      const ref = String(args.ref || '');
      if (!/^e\d+$/.test(ref)) return { ok: false, error: 'ref 形如 e3，请先 snapshot。' };
      const text = String(args.text == null ? '' : args.text);
      const submit = !!args.submit;
      try {
        const [res] = await chrome.scripting.executeScript({
          target: { tabId: t.id },
          args: [ref, text, submit],
          func: (ref, text, submit) => {
            const store = globalThis.__bhRefs;
            const el = store && store.get(ref);
            if (!el) return { ok: false, error: '找不到 ' + ref + '（页面可能变了），请重新 snapshot。' };
            if (el.isConnected === false) {
              return { ok: false, error: ref + ' 已从页面移除，请重新 snapshot。' };
            }
            el.scrollIntoView({ block: 'center' });
            el.focus();
            if (el.isContentEditable) {
              el.textContent = text;
              el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }));
            } else {
              const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
                  : HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(el, text); else el.value = text;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (submit) {
              // 部分站点只监听 keypress/keyup，三个事件都发一遍
              const keys = {
                key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
              };
              el.dispatchEvent(new KeyboardEvent('keydown', keys));
              el.dispatchEvent(new KeyboardEvent('keypress', keys));
              el.dispatchEvent(new KeyboardEvent('keyup', keys));
              // 表单优先走 requestSubmit()，它比 click 按钮更接近真实提交
              const form = el.form || el.closest('form');
              if (form && form.requestSubmit) {
                try { form.requestSubmit(); } catch (e) { /* 有些表单不允许 */ }
              }
            }
            return { ok: true, value: String(el.value != null ? el.value : el.textContent || '').slice(0, 80) };
          },
        });
        const r = (res && res.result) || {};
        return r.ok === false ? { ok: false, error: r.error } : { ok: true, data: r };
      } catch (e) {
        return { ok: false, error: (e && e.message) || String(e) };
      }
    }

    case 'open_url': {
      if (!/^https?:\/\//i.test(args.url || '')) {
        return { ok: false, error: '只接受 http/https 网址。' };
      }
      try {
        const tab = await chrome.tabs.create({ url: args.url });
        try {
          await chrome.windows.update(tab.windowId, { focused: true });
        } catch (e) {
          // 拿不到窗口焦点不影响结果
        }
        return { ok: true, data: {} };
      } catch (e) {
        const msg = (e && e.message) || String(e);
        // 浏览器只剩后台进程、没有任何窗口时，tabs.create 会报 No current window
        if (/no current window/i.test(msg)) {
          await chrome.windows.create({ url: args.url, focused: true });
          return { ok: true, data: {} };
        }
        return { ok: false, error: msg };
      }
    }

    default:
      return { ok: false, error: `未知工具：${tool}` };
  }
}

let scanning = false;
async function tick() {
  if (scanning) return;
  scanning = true;
  try {
    await scan();
  } finally {
    scanning = false;
  }
}

chrome.runtime.onInstalled.addListener(() => { tick(); });
chrome.runtime.onStartup.addListener(() => { tick(); });

// 浏览器进出前台、开关窗口/标签都会唤醒 service worker；
// 借这些事件立刻恢复轮询，避免等 30 秒的闹钟。
chrome.windows.onCreated.addListener(() => { tick(); });
chrome.windows.onFocusChanged.addListener(() => { tick(); });
chrome.tabs.onActivated.addListener(() => { tick(); });
chrome.tabs.onCreated.addListener(() => { tick(); });

// MV3 的 service worker 会被浏览器休眠；闹钟是兜底唤醒，正常情况下
// 长轮询本身就让 worker 一直活着。
chrome.alarms.create('bridge-heartbeat', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm && alarm.name === 'bridge-heartbeat') tick();
});

setInterval(tick, RESCAN_MS);
tick();
