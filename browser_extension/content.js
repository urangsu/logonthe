const EDITOR_SELECTORS = [
  'rich-textarea div[contenteditable="true"]',
  'div.ql-editor[contenteditable="true"]',
  'div[role="textbox"][contenteditable="true"]',
  'textarea'
];
const RESPONSE_SELECTORS = 'model-response, div.response-container';
let lastRequestId = null;
let busy = false;

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
  await bridgeFetch('/v1/heartbeat', 'POST', { status: pageStatus(), title: document.title, url: location.href });
}

function setEditorText(target, text) {
  target.focus();
  if (target instanceof HTMLTextAreaElement) {
    target.value = text;
  } else {
    target.textContent = text;
  }
  target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
  target.dispatchEvent(new Event('change', { bubbles: true }));
  return (target.innerText || target.value || '').includes(text.slice(0, 30));
}

function clickSend() {
  // Gemini renders the send control as a role=button element in some builds,
  // so querying native <button> only causes false send_button_not_found errors.
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
  if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
  button.click();
  return true;
}

function latestResponseText() {
  const nodes = [...document.querySelectorAll(RESPONSE_SELECTORS)];
  const latest = nodes[nodes.length - 1];
  if (!latest) return '';
  const content = latest.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || latest;
  return (content.innerText || '').trim();
}

function generationActive() {
  const buttons = [...document.querySelectorAll('button')].filter(visible);
  return buttons.some(button => /중지|stop responding|stop generating|응답 중지/i.test(
    `${button.getAttribute('aria-label') || ''} ${button.innerText || ''}`
  ));
}

async function postResult(command, status, text = '', error = '') {
  await bridgeFetch('/v1/result', 'POST', {
    requestId: command.requestId,
    postKey: command.postKey,
    navigationVersion: command.navigationVersion,
    status, text, error
  });
}

async function execute(command) {
  busy = true;
  try {
    const target = editor();
    if (!target) return await postResult(command, pageStatus(), '', 'editor_not_found');
    const beforeCount = document.querySelectorAll(RESPONSE_SELECTORS).length;
    if (!setEditorText(target, command.prompt)) return await postResult(command, 'dom_unsupported', '', 'prompt_readback_failed');
    if (!clickSend()) return await postResult(command, 'dom_unsupported', '', 'send_button_not_found');

    const deadline = Date.now() + 50000;
    let previous = '';
    let stableSince = 0;
    while (Date.now() < deadline) {
      const status = pageStatus();
      if (status === 'captcha' || status === 'auth_required') return await postResult(command, status, '', status);
      const count = document.querySelectorAll(RESPONSE_SELECTORS).length;
      const current = count > beforeCount ? latestResponseText() : '';
      if (current && current !== previous) {
        previous = current;
        stableSince = Date.now();
      } else if (current && stableSince && Date.now() - stableSince >= 1800 && !generationActive()) {
        const marker = `[[CMT:${command.requestId}]]`;
        if (!current.includes(marker)) return await postResult(command, 'failed', '', 'request_marker_missing');
        return await postResult(command, 'completed', current, '');
      }
      await new Promise(resolve => setTimeout(resolve, 350));
    }
    await postResult(command, 'timeout', '', 'response_timeout');
  } catch (error) {
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
    lastRequestId = command.requestId;
    await execute(command);
  } catch (_) {}
}

setInterval(tick, 700);
tick();
