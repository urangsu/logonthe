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
    'div[contenteditable="true"]',
    'textarea'
  ];
  const RESPONSE_SELECTORS = 'model-response';
  let runtimeContract = {
    extensionVersion: '13.2.3',
    runtimeBuild: '13.2.3-r7',
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

  // Active execution state machine with lifecycle resolve / finish support
  let activeExecution = null;

  function cancelExecution(reqId, reason = 'cancelled') {
    if (!activeExecution) return false;
    if (!reqId || activeExecution.requestId === reqId) {
      activeExecution.cancelled = true;
      if (typeof activeExecution.finish === 'function') {
        activeExecution.finish({ status: 'failed', text: '', error: reason });
      } else if (typeof activeExecution.resolve === 'function') {
        activeExecution.resolve({ status: 'failed', text: '', error: reason });
      }
      return true;
    }
    return false;
  }

  function stopRuntime() {
    if (isStopped) return;
    isStopped = true;
    cancelExecution(null, 'runtime_stopped');
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

  function canonicalPromptText(value) {
    return String(value ?? '')
      .normalize('NFC')
      .replace(/\r\n?/g, '\n')
      .replace(/[\u2028\u2029]/g, '\n')
      .replace(/[\u00A0\u2007\u202F]/g, ' ')
      .replace(/[\u200B-\u200D\u2060\uFEFF\uFE0E\uFE0F]/g, '')
      .replace(/[\u200E\u200F\u202A-\u202E\u2066-\u2069]/g, '')
      .replace(/\s+/gu, ' ')
      .trim();
  }

  function simpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = (Math.imul(31, hash) + str.charCodeAt(i)) | 0;
    }
    return 'h_' + (hash >>> 0).toString(16);
  }

  function countOccurrences(str, pattern) {
    const matches = (str || '').match(pattern);
    return matches ? matches.length : 0;
  }

  function findFirstMismatchIndex(a, b) {
    const len = Math.min(a.length, b.length);
    for (let i = 0; i < len; i++) {
      if (a[i] !== b[i]) return i;
    }
    return a.length !== b.length ? len : -1;
  }

  function visible(element) {
    if (!element || !element.isConnected) return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 15 && rect.height > 15 && getComputedStyle(element).visibility !== 'hidden' && getComputedStyle(element).display !== 'none';
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

  function scoreEditorCandidate(el) {
    if (!el || !el.isConnected) return -1000;
    if (!visible(el)) return -1000;
    if (el.getAttribute('aria-hidden') === 'true') return -500;
    if (el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true') return -1000;

    let score = 100;
    const isContentEditable = el.isContentEditable || el.getAttribute('contenteditable') === 'true';

    if (el.closest('rich-textarea')) score += 500;
    if (isContentEditable) score += 300;
    if (el.closest('chat-window, .input-area, .composer, .input-container, form, main')) score += 200;

    const rect = el.getBoundingClientRect();
    if (rect.top > window.innerHeight * 0.4) score += 150;

    const sendCandidate = findSendControl(el);
    if (sendCandidate && sendCandidate.button && !sendCandidate.button.disabled) {
      score += 250;
    }

    return score;
  }

  function editor() {
    const candidates = [];
    for (const selector of EDITOR_SELECTORS) {
      const list = document.querySelectorAll(selector);
      for (const el of list) {
        const score = scoreEditorCandidate(el);
        if (score > 0) {
          candidates.push({ el, score });
        }
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates.length > 0 ? candidates[0].el : null;
  }

  function getEditorSurfaces(target) {
    if (!target) return [];
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
      return [{ surface: 'value', text: target.value || '' }];
    }
    return [
      { surface: 'innerText', text: target.innerText || '' },
      { surface: 'textContent', text: target.textContent || '' }
    ];
  }

  function pageStatus() {
    const body = (document.body?.innerText || '').slice(0, 5000);
    if (/captcha|로봇이 아닙니다|비정상적인 트래픽/i.test(body)) return 'captcha';
    if (/accounts\.google\.com/.test(location.href) || /로그인/.test(body) && !editor()) return 'auth_required';

    if (activeExecution) {
      if (Date.now() > activeExecution.deadlineAt) {
        cancelExecution(activeExecution.requestId, 'command_deadline_exceeded');
      } else {
        return 'busy';
      }
    }
    return editor() ? 'ready' : 'dom_unsupported';
  }

  function clearEditor(target) {
    if (!target || !target.isConnected) return;
    target.focus();
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
      target.value = '';
      target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
      target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    } else {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(target);
      selection.removeAllRanges();
      selection.addRange(range);
      try {
        document.execCommand('delete', false, null);
      } catch (_) {
        target.innerHTML = '';
      }
      target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
      target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    }
  }

  function setEditorText(target, text) {
    clearEditor(target);

    target.focus();
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
      target.value = text;
      target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
      target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    } else {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(target);
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand('insertText', false, text);
      target.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: text }));
      target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    }
  }

  async function waitForStableReadback(getTargetFn, expectedText, maxWaitMs = 1500) {
    const expectedCanonical = canonicalPromptText(expectedText);
    const deadline = Date.now() + maxWaitMs;
    let matchStartTime = null;
    let lastMatchedSurface = null;
    let lastActualRaw = '';
    let lastActualCanonical = '';

    while (Date.now() < deadline) {
      if (isStopped || activeExecution?.cancelled) return { ok: false, reason: 'cancelled' };

      let currentTarget = getTargetFn();
      if (!currentTarget || !currentTarget.isConnected) {
        currentTarget = editor();
      }
      if (!currentTarget) {
        await new Promise(r => setTimeout(r, 100));
        continue;
      }

      const surfaces = getEditorSurfaces(currentTarget);
      let matchedThisTick = false;

      for (const s of surfaces) {
        const canonicalActual = canonicalPromptText(s.text);
        lastActualRaw = s.text;
        lastActualCanonical = canonicalActual;

        if (canonicalActual === expectedCanonical) {
          matchedThisTick = true;
          lastMatchedSurface = s.surface;
          break;
        }
      }

      if (matchedThisTick) {
        if (!matchStartTime) {
          matchStartTime = Date.now();
        } else if (Date.now() - matchStartTime >= 200) {
          return {
            ok: true,
            surface: lastMatchedSurface,
            actualRaw: lastActualRaw,
            actualCanonical: lastActualCanonical
          };
        }
      } else {
        matchStartTime = null;
      }

      await new Promise(r => setTimeout(r, 100));
    }

    return {
      ok: false,
      reason: 'readback_mismatch',
      surface: lastMatchedSurface || 'none',
      actualRaw: lastActualRaw,
      actualCanonical: lastActualCanonical
    };
  }

  function responseNodes() {
    return [...document.querySelectorAll(RESPONSE_SELECTORS)].filter(visible);
  }

  function generationActive() {
    const busySelector = '[aria-busy="true"], [data-is-generating="true"], .loading-dots, .streaming, [aria-label*="중지"], [aria-label*="Stop"]';
    return Boolean(document.querySelector(busySelector));
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
        candidateCount: diag.totalCandidates || 0,
        selectedTag: btn?.tagName || null,
        selectedClass: btn ? String(btn.className || '').slice(0, 50) : null,
        ariaLabel: btn?.getAttribute('aria-label') || null,
        iconText: btn ? (btn.innerText || '').trim().slice(0, 20) : null,
        disabled: Boolean(btn?.disabled),
        ariaDisabled: btn?.getAttribute('aria-disabled') === 'true',
        sendConfirmed: Boolean(diag.confirmed),
        boundNode: Boolean(diag.boundNode)
      };
      console.log('[GEMINI][SEND_DIAG]', JSON.stringify(meta));
    } catch (_) {}
  }

  async function executeCore(command, execState) {
    if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
    const initialResponseList = responseNodes();
    const initialResponseSet = new Set(initialResponseList);
    const initialUserMsgs = document.querySelectorAll('.user-message, user-query, [data-test-id="user-query"], .query-text, .user-query-container').length;

    let target = editor();
    if (!target) {
      return { status: 'dom_unsupported', text: '', error: 'Gemini 입력창을 찾지 못했습니다.' };
    }

    // Canonical prompt injection & stabilized readback (with up to 1 retry on newly resolved editor)
    let readbackResult = null;
    for (let attempt = 1; attempt <= 2; attempt++) {
      if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
      if (!target || !target.isConnected) target = editor();
      if (!target) break;

      setEditorText(target, command.prompt);
      readbackResult = await waitForStableReadback(() => target, command.prompt, 1500);
      if (readbackResult.ok) break;

      if (attempt === 1) {
        await new Promise(r => setTimeout(r, 200));
        target = editor();
      }
    }

    const expectedCanonical = canonicalPromptText(command.prompt);
    const actualCanonical = readbackResult?.actualCanonical || '';
    const actualRaw = readbackResult?.actualRaw || '';

    const diagLog = {
      expectedRawLen: (command.prompt || '').length,
      actualRawLen: actualRaw.length,
      expectedCanonicalLen: expectedCanonical.length,
      actualCanonicalLen: actualCanonical.length,
      expectedHash: simpleHash(expectedCanonical),
      actualHash: simpleHash(actualCanonical),
      firstMismatchIndex: findFirstMismatchIndex(actualCanonical, expectedCanonical),
      actualSurface: readbackResult?.surface || 'unknown',
      zeroWidthCount: countOccurrences(actualRaw, /[\u200B-\u200D\u2060\uFEFF\uFE0E\uFE0F]/g),
      nbspCount: countOccurrences(actualRaw, /[\u00A0\u2007\u202F]/g),
      newlineCount: countOccurrences(actualRaw, /\n/g),
      editorConnected: Boolean(target?.isConnected),
      readbackOk: Boolean(readbackResult?.ok)
    };
    console.log('[GEMINI][PROMPT_READBACK_DIAG]', JSON.stringify(diagLog));

    if (!readbackResult?.ok) {
      return { status: 'dom_unsupported', text: '', error: 'prompt_exact_readback_failed' };
    }

    // 1st Send Attempt
    await new Promise(resolve => setTimeout(resolve, 300));
    if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
    const sendCtrl = await waitForSendReady(target, 2500);
    let selectedBtn = sendCtrl?.button;

    if (selectedBtn && !selectedBtn.disabled && selectedBtn.getAttribute('aria-disabled') !== 'true') {
      selectedBtn.click();
    } else if (target && target.isConnected) {
      target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
    }

    // Verify confirmation for up to 5 seconds
    let confirmed = false;
    let boundResponseNode = null;
    const checkDeadline = Date.now() + 5000;

    while (Date.now() < checkDeadline) {
      if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };

      const currentResponses = responseNodes();
      const newResponseNode = currentResponses.find(node => !initialResponseSet.has(node));
      const currentUserMsgs = document.querySelectorAll('.user-message, user-query, [data-test-id="user-query"], .query-text, .user-query-container').length;

      if (newResponseNode) {
        confirmed = true;
        boundResponseNode = newResponseNode;
        break;
      }
      if (currentUserMsgs > initialUserMsgs || generationActive()) {
        confirmed = true;
        break;
      }
      await new Promise(r => setTimeout(r, 150));
    }

    // 2nd Send Attempt if not confirmed after 2.5s
    if (!confirmed) {
      const retryCtrl = findSendControl(target);
      if (retryCtrl?.button && !retryCtrl.button.disabled && retryCtrl.button.getAttribute('aria-disabled') !== 'true') {
        selectedBtn = retryCtrl.button;
        retryCtrl.button.click();
      } else if (target && target.isConnected) {
        target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
      }

      const retryDeadline = Date.now() + 2500;
      while (Date.now() < retryDeadline) {
        if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };
        const currentResponses = responseNodes();
        const newResponseNode = currentResponses.find(node => !initialResponseSet.has(node));
        const currentUserMsgs = document.querySelectorAll('.user-message, user-query, [data-test-id="user-query"], .query-text, .user-query-container').length;

        if (newResponseNode) {
          confirmed = true;
          boundResponseNode = newResponseNode;
          break;
        }
        if (currentUserMsgs > initialUserMsgs || generationActive()) {
          confirmed = true;
          break;
        }
        await new Promise(r => setTimeout(r, 150));
      }
    }

    logSendDiag({
      button: selectedBtn,
      totalCandidates: sendCtrl?.totalCandidates || 0,
      confirmed: confirmed,
      boundNode: Boolean(boundResponseNode)
    });

    if (!confirmed) {
      return { status: 'failed', text: '', error: 'send_not_confirmed' };
    }

    const deadline = Math.min(execState.deadlineAt, Date.now() + 65000);
    let stableSince = null;
    let previous = '';
    let targetResponseNode = boundResponseNode || null;

    return new Promise((resolve) => {
      let resolved = false;

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

      execState.finish = finish;
      execState.resolve = resolve;

      const checkOutput = () => {
        if (isStopped) return finish({ status: 'failed', text: '', error: 'runtime_stopped' });
        if (execState.cancelled) return finish({ status: 'failed', text: '', error: 'cancelled' });
        if (Date.now() > deadline) return finish({ status: 'timeout', text: '', error: 'command_deadline_exceeded' });

        if (!targetResponseNode) {
          const currentNodes = responseNodes();
          const newNode = currentNodes.find(node => !initialResponseSet.has(node));
          if (newNode) {
            targetResponseNode = newNode;
            console.log('[GEMINI][RESPONSE_BOUND]', Boolean(targetResponseNode));
          }
        }

        if (!targetResponseNode) return;

        const contentEl = targetResponseNode.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || targetResponseNode;
        const current = (contentEl.innerText || contentEl.textContent || '').trim();

        if (current && current !== previous) {
          previous = current;
          stableSince = Date.now();
        } else if (current && stableSince && Date.now() - stableSince >= 1600 && !generationActive()) {
          return finish({ status: 'completed', text: current, error: '' });
        }
      };

      const targetRoot = document.querySelector('chat-history, main, body') || document.body;
      const observer = new MutationObserver(() => {
        checkOutput();
      });
      observer.observe(targetRoot, { childList: true, subtree: true, characterData: true });

      const checkTimer = setInterval(checkOutput, 250);
      execState.observer = observer;
      execState.timer = checkTimer;
    });
  }

  async function execute(command) {
    if (isStopped) return { status: 'failed', text: '', error: 'runtime_stopped' };
    if (activeExecution) {
      if (Date.now() > activeExecution.deadlineAt) {
        cancelExecution(activeExecution.requestId, 'command_deadline_exceeded');
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
      timer: null,
      finish: null,
      resolve: null
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
      const cancelled = cancelExecution(message.requestId, 'cancelled_by_bridge');
      sendResponse({ ok: true, cancelled, status: pageStatus() });
      return true;
    }

    if (message.type === 'NFA_EXECUTE_COMMAND') {
      // Two-message protocol: immediate ACK to prevent long-lived sendMessage port timeout
      sendResponse({
        ok: true,
        started: true,
        requestId: message.command?.requestId,
        protocol: 'two-message-v2'
      });

      // Asynchronous core execution followed by NFA_EXECUTION_RESULT push
      execute(message.command)
        .then(result => {
          try {
            chrome.runtime.sendMessage({
              type: 'NFA_EXECUTION_RESULT',
              requestId: message.command?.requestId,
              postKey: message.command?.postKey,
              navigationVersion: message.command?.navigationVersion,
              result: result
            });
          } catch (_) {}
        })
        .catch(err => {
          try {
            chrome.runtime.sendMessage({
              type: 'NFA_EXECUTION_RESULT',
              requestId: message.command?.requestId,
              postKey: message.command?.postKey,
              navigationVersion: message.command?.navigationVersion,
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
