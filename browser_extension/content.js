(() => {
  'use strict';

  // Clean up existing content script runtime if present in this tab
  if (globalThis.__NFA_GEMINI_RUNTIME__ && typeof globalThis.__NFA_GEMINI_RUNTIME__.stop === 'function') {
    try {
      globalThis.__NFA_GEMINI_RUNTIME__.stop();
    } catch (_) {}
  }

  const INSTANCE_ID = Math.random().toString(36).slice(2, 10);
  let isStopped = false;
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
    runtimeBuild: '13.2.3-r4',
    protocolVersion: 3,
    bridgeSchemaVersion: 2
  };

  try {
    chrome.runtime.sendMessage({ type: 'getRuntimeContract' }, (res) => {
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

  let busy = false;

  function stopRuntime() {
    if (isStopped) return;
    isStopped = true;
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

  async function execute(command) {
    if (isStopped) return { status: 'failed', text: '', error: 'runtime_stopped' };
    busy = true;
    const initialNodes = responseNodes();
    const input = editor();
    if (!input) {
      busy = false;
      return { status: 'dom_unsupported', text: '', error: 'Gemini 입력창을 찾지 못했습니다.' };
    }

    setEditorText(input, command.prompt);
    await new Promise(resolve => setTimeout(resolve, 300));
    sendPrompt();

    const deadline = Date.now() + Math.min(65000, Math.max(10000, (command.deadlineAt - Date.now() / 1000) * 1000));
    let stableSince = null;
    let previous = '';

    return new Promise((resolve) => {
      let resolved = false;
      const marker = `[[CMT:${command.requestId}]]`;

      const cleanupObserver = () => {
        try { observer.disconnect(); } catch (_) {}
        if (checkTimer) clearInterval(checkTimer);
      };

      const finish = (res) => {
        if (resolved) return;
        resolved = true;
        cleanupObserver();
        busy = false;
        resolve(res);
      };

      const checkOutput = () => {
        if (isStopped) return finish({ status: 'failed', text: '', error: 'runtime_stopped' });
        if (Date.now() > deadline) return finish({ status: 'timeout', text: '', error: 'command_deadline_exceeded' });

        const newNodes = responseNodes().slice(initialNodes.length);
        const current = newNodes.length ? latestResponseText() : '';

        if (current && current !== previous) {
          previous = current;
          stableSince = Date.now();
        } else if (current && stableSince && Date.now() - stableSince >= 1600 && !generationActive()) {
          const correlated = newNodes.find(node => {
            const content = node.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || node;
            return (content.innerText || '').includes(marker);
          });
          if (!correlated) {
            return finish({ status: 'failed', text: '', error: 'request_marker_missing_in_new_node' });
          }
          const correlatedContent = correlated.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || correlated;
          return finish({ status: 'completed', text: (correlatedContent.innerText || '').trim(), error: '' });
        }
      };

      // MutationObserver watches DOM for streaming updates
      const targetNode = document.querySelector('chat-history, main, body') || document.body;
      const observer = new MutationObserver(() => {
        checkOutput();
      });
      observer.observe(targetNode, { childList: true, subtree: true, characterData: true });

      const checkTimer = setInterval(checkOutput, 300);
    });
  }

  // Register message listeners
  const messageListener = (message, _sender, sendResponse) => {
    if (isStopped) return false;

    if (message.type === 'NFA_RUNTIME_PING') {
      sendResponse({
        ok: true,
        alive: true,
        build: runtimeContract.runtimeBuild,
        version: runtimeContract.extensionVersion,
        instanceId: INSTANCE_ID,
        status: pageStatus(),
        url: location.href,
        title: document.title
      });
      return true;
    }

    if (message.type === 'NFA_EXECUTE_COMMAND') {
      execute(message.command)
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ status: 'failed', text: '', error: String(err?.message || err) }));
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

  globalThis.__NFA_GEMINI_RUNTIME__ = {
    build: runtimeContract.runtimeBuild,
    instanceId: INSTANCE_ID,
    stop: stopRuntime,
    ping: () => ({ alive: !isStopped, build: runtimeContract.runtimeBuild, instanceId: INSTANCE_ID })
  };
})();
