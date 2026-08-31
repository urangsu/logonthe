// Clean up existing content script runtime if present in this tab
if (globalThis.__NFA_GEMINI_RUNTIME__ && typeof globalThis.__NFA_GEMINI_RUNTIME__.stop === 'function') {
  try {
    globalThis.__NFA_GEMINI_RUNTIME__.stop();
  } catch (_) {}
}

const INSTANCE_ID = Math.random().toString(36).slice(2, 10);
let isStopped = false;
let tickTimer = null;
const eventCleanups = [];

const EDITOR_SELECTORS = [
  'rich-textarea div[contenteditable="true"]',
  'div.ql-editor[contenteditable="true"]',
  'div[role="textbox"][contenteditable="true"]',
  'textarea'
];
const RESPONSE_SELECTORS = 'model-response';
let runtimeContract = {
  extensionVersion: '13.2.3',
  runtimeBuild: '13.2.3-r3',
  protocolVersion: 3,
  bridgeSchemaVersion: 2
};

try {
  chrome.runtime.sendMessage({ type: "getRuntimeContract" }, (res) => {
    if (res && res.ok && res.data) {
      runtimeContract = {
        extensionVersion: res.data.extensionVersion || runtimeContract.extensionVersion,
        runtimeBuild: res.data.runtimeBuild || runtimeContract.runtimeBuild,
        protocolVersion: res.data.protocolVersion || runtimeContract.protocolVersion,
        bridgeSchemaVersion: res.data.bridgeSchemaVersion || runtimeContract.bridgeSchemaVersion
      };
      if (globalThis.__NFA_GEMINI_RUNTIME__) {
        globalThis.__NFA_GEMINI_RUNTIME__.build = runtimeContract.runtimeBuild;
      }
    }
  });
} catch (_) {}

let lastRequestId = null;
let busy = false;
let lastBridgeFailLog = 0;

function stopRuntime() {
  if (isStopped) return;
  isStopped = true;
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
  while (eventCleanups.length > 0) {
    const cleanup = eventCleanups.pop();
    try { cleanup(); } catch (_) {}
  }
  if (globalThis.__NFA_GEMINI_RUNTIME__?.instanceId === INSTANCE_ID) {
    delete globalThis.__NFA_GEMINI_RUNTIME__;
  }
}

function checkExtensionInvalidated(err) {
  const msg = String(err?.message || err || '');
  if (msg.includes('Extension context invalidated')) {
    stopRuntime();
    return true;
  }
  return false;
}

function logBridgeFail(stage, err) {
  if (isStopped) return;
  if (checkExtensionInvalidated(err)) return;
  const now = Date.now();
  if (now - lastBridgeFailLog > 5000) {
    lastBridgeFailLog = now;
    console.warn(`[GEMINI][BRIDGE_FAIL] stage=${stage} error=${String(err?.message || err)}`);
  }
}

function normalizeText(value) {
  return String(value || '').replace(/\r\n?/g, '\n').trim();
}

function visible(element) {
  if (!element) return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 20 && rect.height > 15 && getComputedStyle(element).visibility !== 'hidden';
}

function editor() {
  for (const selector of EDITOR_SELECTORS) {
    const found = [...document.querySelectorAll(selector)].find(visible);
    if (found) return found;
  }
  return null;
}

function pageStatus() {
  const body = (document.body?.innerText || '').slice(0, 5000);
  if (/captcha|로봇이 아닙니다|비정상적인 트래픽/i.test(body)) return 'captcha';
  if (/accounts\.google\.com/.test(location.href) || /로그인/.test(body) && !editor()) return 'auth_required';
  return editor() ? (busy ? 'busy' : 'ready') : 'dom_unsupported';
}

function bridgeFetch(path, method = 'GET', body = null) {
  if (isStopped) return Promise.reject(new Error('runtime_stopped'));
  return new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendMessage({ type: 'bridgeFetch', path, method, body }, response => {
        if (chrome.runtime.lastError) {
          const err = chrome.runtime.lastError;
          if (checkExtensionInvalidated(err)) return reject(new Error('Extension context invalidated'));
          return reject(err);
        }
        if (!response?.ok) return reject(new Error(response?.error || 'bridge_error'));
        resolve(response.data);
      });
    } catch (e) {
      if (checkExtensionInvalidated(e)) return reject(new Error('Extension context invalidated'));
      reject(e);
    }
  });
}

async function heartbeat() {
  if (isStopped) return;
  try {
    await bridgeFetch('/v1/heartbeat', 'POST', {
      status: pageStatus(),
      title: document.title,
      url: location.href,
      extensionVersion: runtimeContract.extensionVersion,
      contentBuild: runtimeContract.runtimeBuild,
      buildId: runtimeContract.runtimeBuild,
      protocolVersion: runtimeContract.protocolVersion,
      bridgeSchemaVersion: runtimeContract.bridgeSchemaVersion
    });
  } catch (err) {
    if (!checkExtensionInvalidated(err)) {
      logBridgeFail('heartbeat', err);
    }
    throw err;
  }
}

function setEditorText(target, text) {
  target.focus();
  if (target instanceof HTMLTextAreaElement) {
    target.value = text;
  } else {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(target);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand('insertText', false, text);
    if ((target.innerText || target.textContent || '').trim() !== text.trim()) {
      target.textContent = text;
    }
  }
  target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
  target.dispatchEvent(new Event('keyup', { bubbles: true }));
  target.dispatchEvent(new Event('change', { bubbles: true }));
  return normalizeText(target.innerText || target.value) === normalizeText(text);
}

function findSendControl() {
  const buttons = [...document.querySelectorAll('button, [role="button"]')].filter(visible);
  const button = buttons.find(b => {
    const label = [
      b.getAttribute('aria-label'),
      b.getAttribute('data-tooltip'),
      b.getAttribute('title'),
      b.innerText
    ].join(' ').toLowerCase();
    return /보내기|전송|send|submit/i.test(label) || b.querySelector('mat-icon[data-mat-icon-name="send"], .send-button-icon');
  });
  return button || null;
}

function sendPrompt() {
  const button = findSendControl();
  if (button && !button.disabled && button.getAttribute('aria-disabled') !== 'true') {
    button.click();
    return true;
  }
  const el = editor();
  if (el) {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
    return true;
  }
  return false;
}

function responseNodes() {
  return [...document.querySelectorAll(RESPONSE_SELECTORS)].filter(visible);
}

function latestResponseText() {
  const nodes = responseNodes();
  if (!nodes.length) return '';
  const latest = nodes[nodes.length - 1];
  const content = latest.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || latest;
  return normalizeText(content.innerText || content.textContent);
}

function generationActive() {
  const stopButton = [...document.querySelectorAll('button, [role="button"]')].filter(visible).find(b => {
    const label = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
    return /중지|stop/i.test(label);
  });
  return Boolean(stopButton || document.querySelector('.generating, .loading, mat-progress-bar, [aria-busy="true"]'));
}

async function postResult(command, status, text = '', error = '') {
  if (isStopped) return;
  return bridgeFetch('/v1/result', 'POST', {
    requestId: command.requestId,
    postKey: command.postKey,
    navigationVersion: command.navigationVersion,
    status,
    text,
    error
  });
}

async function execute(command) {
  if (isStopped) return;
  busy = true;
  const initialNodes = responseNodes();
  const input = editor();
  if (!input) {
    busy = false;
    return await postResult(command, 'dom_unsupported', '', 'Gemini 입력창을 찾지 못했습니다.');
  }

  setEditorText(input, command.prompt);
  await new Promise(resolve => setTimeout(resolve, 300));
  sendPrompt();

  const deadline = Date.now() + Math.min(65000, Math.max(10000, (command.deadlineAt - Date.now() / 1000) * 1000));
  let stableSince = null;
  let previous = '';
  try {
    while (Date.now() < deadline && !isStopped) {
      const newNodes = responseNodes().slice(initialNodes.length);
      const current = newNodes.length ? latestResponseText() : '';
      if (current && current !== previous) {
        previous = current;
        stableSince = Date.now();
      } else if (current && stableSince && Date.now() - stableSince >= 1800 && !generationActive()) {
        const marker = `[[CMT:${command.requestId}]]`;
        const correlated = newNodes.find(node => {
          const content = node.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || node;
          return (content.innerText || '').includes(marker);
        });
        if (!correlated) {
          return await postResult(command, 'failed', '', 'request_marker_missing_in_new_node');
        }
        const correlatedContent = correlated.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || correlated;
        return await postResult(command, 'completed', (correlatedContent.innerText || '').trim(), '');
      }
      await new Promise(resolve => setTimeout(resolve, 350));
    }
    if (!isStopped) {
      await postResult(command, 'timeout', '', 'command_deadline_exceeded');
    }
  } catch (error) {
    if (!checkExtensionInvalidated(error)) {
      logBridgeFail('execute', error);
      await postResult(command, 'failed', '', String(error.message || error)).catch(() => {});
    }
  } finally {
    busy = false;
  }
}

async function tick() {
  if (isStopped) return;
  try {
    await heartbeat();
    if (isStopped || busy || pageStatus() !== 'ready') return;
    const data = await bridgeFetch('/v1/command');
    const command = data.command;
    if (!command || command.requestId === lastRequestId) return;
    const claim = await bridgeFetch('/v1/claim', 'POST', { requestId: command.requestId, claimant: runtimeContract.runtimeBuild });
    if (!claim.claimed) return;
    lastRequestId = command.requestId;
    await execute(command);
  } catch (err) {
    if (!checkExtensionInvalidated(err)) {
      logBridgeFail('tick', err);
    }
  }
}

// Register ping message handler
const messageListener = (message, _sender, sendResponse) => {
  if (isStopped) return false;
  if (message.type === 'NFA_RUNTIME_PING') {
    sendResponse({
      ok: true,
      alive: true,
      build: runtimeContract.runtimeBuild,
      version: runtimeContract.extensionVersion,
      instanceId: INSTANCE_ID,
      status: pageStatus()
    });
    return true;
  }
  return false;
};

chrome.runtime.onMessage.addListener(messageListener);
eventCleanups.push(() => {
  try {
    chrome.runtime.onMessage.removeListener(messageListener);
  } catch (_) {}
});

// Setup tick intervals and visibility listeners
tickTimer = setInterval(tick, 1000);

const onVisibilityChange = () => {
  if (document.visibilityState === 'visible') tick();
};
document.addEventListener('visibilitychange', onVisibilityChange);
eventCleanups.push(() => document.removeEventListener('visibilitychange', onVisibilityChange));

window.addEventListener('focus', tick);
eventCleanups.push(() => window.removeEventListener('focus', tick));

window.addEventListener('pageshow', tick);
eventCleanups.push(() => window.removeEventListener('pageshow', tick));

globalThis.__NFA_GEMINI_RUNTIME__ = {
  build: runtimeContract.runtimeBuild,
  instanceId: INSTANCE_ID,
  stop: stopRuntime,
  ping: () => ({ alive: !isStopped, build: runtimeContract.runtimeBuild, instanceId: INSTANCE_ID })
};

tick();
