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
    runtimeBuild: '13.2.3-r5',
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
    return rect.width > 15 && rect.height > 15 && getComputedStyle(element).visibility !== 'hidden';
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

  function findComposer(input) {
    if (!input) return null;
    return input.closest('chat-window, .input-area, .chat-input-container, .input-wrapper, form, main, [role="main"]') || input.parentElement;
  }

  function scoreSendButton(btn, input, composerRect) {
    if (!btn || !visible(btn)) return -10000;
    const isDis = btn.disabled || btn.getAttribute('aria-disabled') === 'true';
    if (isDis) return -5000;

    const label = [
      btn.getAttribute('aria-label') || '',
      btn.getAttribute('data-tooltip') || '',
      btn.getAttribute('title') || '',
      btn.innerText || ''
    ].join(' ').toLowerCase();

    // Explicitly exclude non-send controls: mic, audio, attach, file, plus, add, menu, settings, expand, help
    if (/마이크|음성|mic|audio|voice|첨부|파일|attach|upload|file|플러스|추가|add|plus|메뉴|menu|설정|settings|확장|expand|help/i.test(label)) {
      return -10000;
    }

    let score = 0;

    // 1. Aria label / text matching send
    if (/보내기|전송|제출|send|submit/i.test(label)) {
      score += 100;
    }

    // 2. Icon check (mat-icon, svg, or class)
    const iconEl = btn.querySelector('mat-icon, svg, [data-icon-name], .send-button-icon');
    const iconName = [
      iconEl?.getAttribute('data-mat-icon-name') || '',
      iconEl?.getAttribute('data-icon-name') || '',
      iconEl?.textContent || '',
      btn.querySelector('mat-icon')?.textContent || ''
    ].join(' ').toLowerCase();

    if (/send|arrow_upward|arrow_up|submit/i.test(iconName) || /send/i.test(btn.className || '')) {
      score += 80;
    }

    // 3. Class or test-id
    const classAndTestId = [btn.className || '', btn.getAttribute('data-test-id') || '', btn.getAttribute('jsname') || ''].join(' ').toLowerCase();
    if (/send|submit|arrow/i.test(classAndTestId)) {
      score += 50;
    }

    // 4. Angular Material touch target presence
    const hasTouchTarget = Boolean(btn.querySelector('.mat-mdc-button-touch-target, .mdc-button__touch-target'));
    if (hasTouchTarget) {
      score += 20;
    }

    // 5. Position scoring: located in bottom-right relative to input / composer
    if (input && composerRect) {
      const btnRect = btn.getBoundingClientRect();
      const inputRect = input.getBoundingClientRect();
      if (btnRect.right >= inputRect.right - 80) {
        score += 30;
      }
      if (btnRect.bottom >= inputRect.bottom - 40) {
        score += 20;
      }
    }

    return score;
  }

  function findSendControl(input) {
    const targetInput = input || editor();
    if (!targetInput) return null;

    const composer = findComposer(targetInput);
    if (!composer) return null;

    const composerRect = composer.getBoundingClientRect();

    // Collect all candidate elements inside composer
    const elements = [
      ...composer.querySelectorAll('button, [role="button"], span.mat-mdc-button-touch-target, span.mdc-button__touch-target')
    ];

    const uniqueButtons = new Set();
    for (const el of elements) {
      const btn = el.tagName.toLowerCase() === 'button' ? el : (el.closest('button') || (el.getAttribute('role') === 'button' ? el : null));
      if (btn && visible(btn)) {
        uniqueButtons.add(btn);
      }
    }

    let bestBtn = null;
    let bestScore = 0;

    for (const btn of uniqueButtons) {
      const score = scoreSendButton(btn, targetInput, composerRect);
      if (score > bestScore) {
        bestScore = score;
        bestBtn = btn;
      }
    }

    return { button: bestBtn, score: bestScore, totalCandidates: uniqueButtons.size, composer };
  }

  function logSendDiag(info) {
    const btn = info.button;
    const iconEl = btn ? btn.querySelector('mat-icon, svg, [data-icon-name]') : null;
    const iconText = (iconEl?.getAttribute('data-mat-icon-name') || iconEl?.textContent || '').trim();
    const diag = {
      editorReadback: Boolean(info.readback),
      candidateCount: info.totalCandidates || 0,
      selectedTag: btn ? btn.tagName : 'NONE',
      selectedClass: btn ? (btn.className || '').slice(0, 60) : '',
      ariaLabel: btn ? (btn.getAttribute('aria-label') || '').slice(0, 60) : '',
      iconText: iconText,
      touchTarget: Boolean(btn?.querySelector('.mat-mdc-button-touch-target, .mdc-button__touch-target')),
      disabled: Boolean(btn?.disabled),
      ariaDisabled: btn?.getAttribute('aria-disabled') === 'true',
      sendConfirmed: Boolean(info.confirmed)
    };
    console.log('[GEMINI][SEND_DIAG]', JSON.stringify(diag));
    return diag;
  }

  async function waitForSendReady(input, timeoutMs = 2500) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const res = findSendControl(input);
      if (res && res.button && !res.button.disabled && res.button.getAttribute('aria-disabled') !== 'true' && res.score > 0) {
        return res;
      }
      await new Promise(r => setTimeout(r, 100));
    }
    return findSendControl(input);
  }

  function responseNodes() {
    return [...document.querySelectorAll(RESPONSE_SELECTORS)].filter(visible);
  }

  function generationActive() {
    const stopButton = [...document.querySelectorAll('button, [role="button"]')].filter(visible).find(b => {
      const label = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
      return /중지|stop/i.test(label);
    });
    return Boolean(stopButton || document.querySelector('.generating, .loading, mat-progress-bar, [aria-busy="true"]'));
  }

  function isSendConfirmed(input, initialNodes, initialUserMsgsCount) {
    // 1. Editor text cleared
    const currentVal = normalizeText(input?.innerText || input?.value || '');
    if (!currentVal) return true;

    // 2. Generation active (stop button, progress bar, aria-busy)
    if (generationActive()) return true;

    // 3. New response node created
    const currentNodes = responseNodes();
    if (currentNodes.length > initialNodes.length) return true;

    // 4. New user message bubble appeared
    const currentUserMessages = document.querySelectorAll('.user-message, user-query, [data-test-id="user-query"], .query-text, .user-query-container');
    if (currentUserMessages.length > initialUserMsgsCount) return true;

    return false;
  }

  async function execute(command) {
    if (isStopped) return { status: 'failed', text: '', error: 'runtime_stopped' };
    busy = true;
    const initialNodes = responseNodes();
    const initialUserMsgs = document.querySelectorAll('.user-message, user-query, [data-test-id="user-query"], .query-text, .user-query-container').length;
    const input = editor();
    if (!input) {
      busy = false;
      return { status: 'dom_unsupported', text: '', error: 'Gemini 입력창을 찾지 못했습니다.' };
    }

    let isTextSet = setEditorText(input, command.prompt);
    if (!isTextSet) {
      await new Promise(r => setTimeout(r, 200));
      isTextSet = setEditorText(input, command.prompt);
    }
    const readbackOk = normalizeText(input.innerText || input.value).includes(normalizeText(command.prompt).slice(0, 30));
    if (!isTextSet && !readbackOk) {
      busy = false;
      logSendDiag({ button: null, readback: false, confirmed: false });
      return { status: 'dom_unsupported', text: '', error: 'prompt_exact_readback_failed' };
    }

    // 1st Send Attempt: Wait for enabled send control and click
    await new Promise(resolve => setTimeout(resolve, 300));
    const sendCtrl = await waitForSendReady(input, 2500);
    let selectedBtn = sendCtrl?.button;

    if (selectedBtn && !selectedBtn.disabled && selectedBtn.getAttribute('aria-disabled') !== 'true') {
      selectedBtn.click();
    } else if (input) {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
    }

    // Verify confirmation for up to 3 seconds
    let confirmed = false;
    const checkDeadline1 = Date.now() + 3000;
    while (Date.now() < checkDeadline1) {
      if (isSendConfirmed(input, initialNodes, initialUserMsgs)) {
        confirmed = true;
        break;
      }
      await new Promise(r => setTimeout(r, 200));
    }

    // 2nd Send Attempt (1 Retry) if not confirmed
    if (!confirmed) {
      const retryCtrl = findSendControl(input);
      if (retryCtrl?.button && !retryCtrl.button.disabled && retryCtrl.button.getAttribute('aria-disabled') !== 'true') {
        selectedBtn = retryCtrl.button;
        retryCtrl.button.click();
      } else if (input) {
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
      }

      const checkDeadline2 = Date.now() + 2000;
      while (Date.now() < checkDeadline2) {
        if (isSendConfirmed(input, initialNodes, initialUserMsgs)) {
          confirmed = true;
          break;
        }
        await new Promise(r => setTimeout(r, 200));
      }
    }

    logSendDiag({
      button: selectedBtn,
      readback: true,
      totalCandidates: sendCtrl?.totalCandidates || 0,
      confirmed: confirmed
    });

    // If still not confirmed, fail-fast immediately (do not wait 70 seconds!)
    if (!confirmed) {
      busy = false;
      return { status: 'failed', text: '', error: 'send_not_confirmed' };
    }

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

        const currentNodes = responseNodes();
        const newNodes = currentNodes.slice(initialNodes.length);
        if (!newNodes.length) return;

        // Bind specifically to the newly created target model-response node
        const targetResponseNode = newNodes[newNodes.length - 1];
        const contentEl = targetResponseNode.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || targetResponseNode;
        const current = normalizeText(contentEl.innerText || contentEl.textContent || '');

        if (current && current !== previous) {
          previous = current;
          stableSince = Date.now();
        } else if (current && stableSince && Date.now() - stableSince >= 1600 && !generationActive()) {
          const correlated = newNodes.find(node => {
            const inner = node.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || node;
            return (inner.innerText || '').includes(marker);
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
