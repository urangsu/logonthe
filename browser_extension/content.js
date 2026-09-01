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

  // Active execution state machine
  let activeExecution = null;

  function cancelExecution(reqId) {
    if (!activeExecution) return false;
    if (!reqId || activeExecution.requestId === reqId) {
      activeExecution.cancelled = true;
      try { activeExecution.observer?.disconnect(); } catch (_) {}
      if (activeExecution.timer) clearInterval(activeExecution.timer);
      activeExecution = null;
      return true;
    }
    return false;
  }

  function stopRuntime() {
    if (isStopped) return;
    isStopped = true;
    cancelExecution();
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

    if (activeExecution) {
      if (Date.now() > activeExecution.deadlineAt) {
        cancelExecution(activeExecution.requestId);
      } else {
        return 'busy';
      }
    }
    return editor() ? 'ready' : 'dom_unsupported';
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
    }
    target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
    target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    return true;
  }

  function responseNodes() {
    return [...document.querySelectorAll(RESPONSE_SELECTORS)].filter(visible);
  }

  function generationActive() {
    const busySelector = '[aria-busy="true"], [data-is-generating="true"], .loading-dots, .streaming, [aria-label*="중지"], [aria-label*="Stop"]';
    return Boolean(document.querySelector(busySelector));
  }

  function findComposer(input) {
    if (!input) return document.body;
    return input.closest('chat-window, .input-area, .composer, .input-container, form, main') || input.parentElement || document.body;
  }

  function scoreSendCandidate(el, composer) {
    if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true') return -1000;
    const tag = el.tagName.toLowerCase();
    if (tag !== 'button' && el.getAttribute('role') !== 'button') return -500;

    const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
    const className = String(el.className || '').toLowerCase();
    const testId = (el.getAttribute('data-test-id') || el.getAttribute('data-testid') || '').toLowerCase();

    if (/mic|voice|음성|마이크|audio|첨부|파일|attach|file|plus|추가|설정|help|도움말/.test(ariaLabel + ' ' + className + ' ' + testId)) {
      return -10000;
    }

    let score = 0;
    if (/send|전송|보내기|제출|submit|prompt/.test(ariaLabel)) score += 500;
    if (/send|arrow_upward|arrow-up|submit/.test(text)) score += 400;
    if (el.querySelector('mat-icon, [data-mat-icon-name*="send"], [data-mat-icon-name*="arrow"]')) score += 300;
    if (/send|submit|btn-send/.test(className + ' ' + testId)) score += 250;
    if (el.querySelector('.mat-mdc-button-touch-target')) score += 150;

    if (composer) {
      const cRect = composer.getBoundingClientRect();
      const bRect = el.getBoundingClientRect();
      if (bRect.right >= cRect.right - 120 && bRect.bottom >= cRect.bottom - 120) {
        score += 200;
      }
    }
    return score;
  }

  function findSendControl(input) {
    const composer = findComposer(input);
    const rawCandidates = composer.querySelectorAll('button, [role="button"], span.mat-mdc-button-touch-target, mat-icon');
    const buttonSet = new Set();

    for (const raw of rawCandidates) {
      if (raw.tagName.toLowerCase() === 'button') {
        buttonSet.add(raw);
      } else {
        const parentBtn = raw.closest('button, [role="button"]');
        if (parentBtn) buttonSet.add(parentBtn);
      }
    }

    const scored = [...buttonSet].map(btn => ({
      button: btn,
      score: scoreSendCandidate(btn, composer)
    })).filter(item => item.score > 0).sort((a, b) => b.score - a.score);

    return scored.length > 0 ? { button: scored[0].button, totalCandidates: scored.length } : null;
  }

  async function waitForSendReady(input, timeoutMs = 2500) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (isStopped || activeExecution?.cancelled) return null;
      const ctrl = findSendControl(input);
      if (ctrl && ctrl.button && !ctrl.button.disabled && ctrl.button.getAttribute('aria-disabled') !== 'true') {
        return ctrl;
      }
      await new Promise(r => setTimeout(r, 150));
    }
    return findSendControl(input);
  }

  function logSendDiag(diag) {
    try {
      const btn = diag.button;
      const meta = {
        editorReadback: Boolean(diag.readback),
        candidateCount: diag.totalCandidates || 0,
        selectedTag: btn?.tagName || null,
        selectedClass: btn ? String(btn.className || '').slice(0, 50) : null,
        ariaLabel: btn?.getAttribute('aria-label') || null,
        iconText: btn ? (btn.innerText || '').trim().slice(0, 20) : null,
        touchTarget: Boolean(btn?.querySelector('.mat-mdc-button-touch-target')),
        disabled: Boolean(btn?.disabled),
        ariaDisabled: btn?.getAttribute('aria-disabled') === 'true',
        sendConfirmed: Boolean(diag.confirmed)
      };
      console.log('[GEMINI][SEND_DIAG]', JSON.stringify(meta));
    } catch (_) {}
  }

  function isSendConfirmed(input, initialNodes, initialUserMsgsCount) {
    const inputEmpty = !input || !input.innerText || input.innerText.trim() === '' || input.value === '';
    if (inputEmpty) return true;
    if (generationActive()) return true;
    const currentNodes = responseNodes();
    if (currentNodes.length > initialNodes.length) return true;
    const currentUserMessages = document.querySelectorAll('.user-message, user-query, [data-test-id="user-query"], .query-text, .user-query-container');
    if (currentUserMessages.length > initialUserMsgsCount) return true;
    return false;
  }

  async function executeCore(command, execState) {
    if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
    const initialNodes = responseNodes();
    const initialUserMsgs = document.querySelectorAll('.user-message, user-query, [data-test-id="user-query"], .query-text, .user-query-container').length;
    const input = editor();
    if (!input) {
      return { status: 'dom_unsupported', text: '', error: 'Gemini 입력창을 찾지 못했습니다.' };
    }

    let isTextSet = setEditorText(input, command.prompt);
    if (!isTextSet) {
      await new Promise(r => setTimeout(r, 200));
      if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
      isTextSet = setEditorText(input, command.prompt);
    }
    const readbackOk = normalizeText(input.innerText || input.value).includes(normalizeText(command.prompt).slice(0, 30));
    if (!isTextSet && !readbackOk) {
      logSendDiag({ button: null, readback: false, confirmed: false });
      return { status: 'dom_unsupported', text: '', error: 'prompt_exact_readback_failed' };
    }

    // 1st Send Attempt
    await new Promise(resolve => setTimeout(resolve, 300));
    if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
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
      if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
      if (isSendConfirmed(input, initialNodes, initialUserMsgs)) {
        confirmed = true;
        break;
      }
      await new Promise(r => setTimeout(r, 200));
    }

    // 2nd Send Attempt if not confirmed
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
        if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
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

    if (!confirmed) {
      return { status: 'failed', text: '', error: 'send_not_confirmed' };
    }

    const deadline = Math.min(execState.deadlineAt, Date.now() + 65000);
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
        resolve(res);
      };

      const checkOutput = () => {
        if (isStopped) return finish({ status: 'failed', text: '', error: 'runtime_stopped' });
        if (execState.cancelled) return finish({ status: 'failed', text: '', error: 'cancelled' });
        if (Date.now() > deadline) return finish({ status: 'timeout', text: '', error: 'command_deadline_exceeded' });

        const currentNodes = responseNodes();
        const newNodes = currentNodes.slice(initialNodes.length);
        if (!newNodes.length) return;

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

      const targetNode = document.querySelector('chat-history, main, body') || document.body;
      const observer = new MutationObserver(() => {
        checkOutput();
      });
      observer.observe(targetNode, { childList: true, subtree: true, characterData: true });

      const checkTimer = setInterval(checkOutput, 300);
      execState.observer = observer;
      execState.timer = checkTimer;
    });
  }

  async function execute(command) {
    if (isStopped) return { status: 'failed', text: '', error: 'runtime_stopped' };
    if (activeExecution) {
      if (Date.now() > activeExecution.deadlineAt) {
        cancelExecution(activeExecution.requestId);
      } else {
        return { status: 'busy', text: '', error: 'runtime_busy' };
      }
    }

    const currentReqId = command?.requestId || Math.random().toString(36).slice(2, 10);
    const deadlineMs = (typeof command?.deadlineAt === 'number' && command.deadlineAt > 1000000000)
      ? command.deadlineAt * 1000
      : (Date.now() + 70000);

    const execState = {
      requestId: currentReqId,
      startedAt: Date.now(),
      deadlineAt: deadlineMs,
      cancelled: false,
      observer: null,
      timer: null
    };
    activeExecution = execState;

    try {
      return await executeCore(command, execState);
    } finally {
      if (activeExecution?.requestId === currentReqId) {
        try { activeExecution.observer?.disconnect(); } catch (_) {}
        if (activeExecution.timer) clearInterval(activeExecution.timer);
        activeExecution = null;
      }
    }
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
        title: document.title,
        busyRequestId: activeExecution?.requestId || null,
        busySince: activeExecution?.startedAt || null,
        busyDeadlineAt: activeExecution?.deadlineAt || null
      });
      return true;
    }

    if (message.type === 'NFA_CANCEL_COMMAND') {
      const cancelled = cancelExecution(message.requestId);
      sendResponse({ ok: true, cancelled, status: pageStatus() });
      return true;
    }

    if (message.type === 'NFA_EXECUTE_COMMAND') {
      sendResponse({ ok: true, started: true });
      execute(message.command)
        .then(result => {
          try {
            chrome.runtime.sendMessage({
              type: 'NFA_COMMAND_RESULT',
              requestId: message.command.requestId,
              postKey: message.command.postKey,
              navigationVersion: message.command.navigationVersion,
              result: result
            });
          } catch (_) {}
        })
        .catch(err => {
          try {
            chrome.runtime.sendMessage({
              type: 'NFA_COMMAND_RESULT',
              requestId: message.command.requestId,
              postKey: message.command.postKey,
              navigationVersion: message.command.navigationVersion,
              result: { status: 'failed', text: '', error: String(err?.message || err) }
            });
          } catch (_) {}
        });
      return false;
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
    cancel: cancelExecution,
    ping: () => ({
      alive: !isStopped,
      build: runtimeContract.runtimeBuild,
      instanceId: INSTANCE_ID,
      busyRequestId: activeExecution?.requestId || null
    })
  };
})();
