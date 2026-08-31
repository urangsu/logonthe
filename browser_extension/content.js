const EDITOR_SELECTORS = [
  'rich-textarea div[contenteditable="true"]',
  'div.ql-editor[contenteditable="true"]',
  'div[role="textbox"][contenteditable="true"]',
  'textarea'
];
const RESPONSE_SELECTORS = 'model-response';
const EXTENSION_VERSION = '13.2.3';
const CONTENT_BUILD = '13.2.3-r1';
const PROTOCOL_VERSION = 3;
const BRIDGE_SCHEMA_VERSION = 2;
let lastRequestId = null;
let busy = false;
let lastBridgeFailLog = 0;

function logBridgeFail(stage, err) {
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
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ type: 'bridgeFetch', path, method, body }, response => {
      if (chrome.runtime.lastError) return reject(chrome.runtime.lastError);
      if (!response?.ok) return reject(new Error(response?.error || 'bridge_error'));
      resolve(response.data);
    });
  });
}

async function heartbeat() {
  try {
    await bridgeFetch('/v1/heartbeat', 'POST', {
      status: pageStatus(),
      title: document.title,
      url: location.href,
      extensionVersion: EXTENSION_VERSION,
      contentBuild: CONTENT_BUILD,
      buildId: CONTENT_BUILD,
      protocolVersion: PROTOCOL_VERSION,
      bridgeSchemaVersion: BRIDGE_SCHEMA_VERSION
    });
  } catch (err) {
    logBridgeFail('heartbeat', err);
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
    ].filter(Boolean).join(' ');
    return /보내기|전송|메시지 보내기|send message|send/i.test(label);
  });
  return button || null;
}

function responseNodes() {
  const primary = [...document.querySelectorAll('model-response')];
  return primary.length ? primary : [...document.querySelectorAll('div.response-container')];
}

function latestResponseText() {
  const nodes = responseNodes();
  if (!nodes.length) return '';
  const last = nodes[nodes.length - 1];
  const content = last.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || last;
  return (content.innerText || '').trim();
}

function generationActive() {
  const stopButton = [...document.querySelectorAll('button, [role="button"]')].find(b => {
    const label = [b.getAttribute('aria-label'), b.innerText].filter(Boolean).join(' ');
    return /답변 중지|생성 중지|중지|stop/i.test(label);
  });
  return Boolean(stopButton && visible(stopButton));
}

async function postResult(command, status, text = '', error = '') {
  return await bridgeFetch('/v1/result', 'POST', {
    requestId: command.requestId,
    postKey: command.postKey,
    navigationVersion: command.navigationVersion,
    status,
    text,
    error
  });
}

async function execute(command) {
  busy = true;
  const initialNodes = responseNodes();
  const target = editor();
  if (!target) {
    busy = false;
    return await postResult(command, 'dom_unsupported', '', 'editor_not_found');
  }
  const textSet = setEditorText(target, command.prompt);
  if (!textSet) {
    busy = false;
    return await postResult(command, 'failed', '', 'editor_text_set_failed');
  }
  await new Promise(resolve => setTimeout(resolve, 300));
  const sendButton = findSendControl();
  if (!sendButton) {
    busy = false;
    return await postResult(command, 'failed', '', 'send_button_not_found');
  }
  sendButton.click();
  const deadline = Date.now() + Math.min(65000, Math.max(10000, (command.deadlineAt - Date.now() / 1000) * 1000));
  let stableSince = null;
  let previous = '';
  try {
    while (Date.now() < deadline) {
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
    await postResult(command, 'timeout', '', 'command_deadline_exceeded');
  } catch (error) {
    logBridgeFail('execute', error);
    await postResult(command, 'failed', '', String(error.message || error)).catch(() => {});
  } finally {
    busy = false;
  }
}

async function tick() {
  try {
    await heartbeat();
    if (busy || pageStatus() !== 'ready') return;
    const data = await bridgeFetch('/v1/command');
    const command = data.command;
    if (!command || command.requestId === lastRequestId) return;
    const claim = await bridgeFetch('/v1/claim', 'POST', { requestId: command.requestId, claimant: CONTENT_BUILD });
    if (!claim.claimed) return;
    lastRequestId = command.requestId;
    await execute(command);
  } catch (err) {
    logBridgeFail('tick', err);
  }
}

setInterval(tick, 1000);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    tick();
  }
});
window.addEventListener('focus', tick);
window.addEventListener('pageshow', tick);
tick();
