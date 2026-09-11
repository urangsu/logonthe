(() => {
  'use strict';

  // Clean up existing content script runtime if present in this tab
  if (globalThis.__NFA_GEMINI_RUNTIME__ && typeof globalThis.__NFA_GEMINI_RUNTIME__.stop === 'function') {
    try {
      globalThis.__NFA_GEMINI_RUNTIME__.stop();
    } catch (_) {}
  }

  const INSTANCE_ID = Math.random().toString(36).slice(2, 10);
  let conversationEpoch = 1;
  let isStopped = false;
  const eventCleanups = [];

  const USER_QUERY_SELECTORS = [
    '.user-message',
    'user-query',
    '[data-test-id="user-query"]',
    '.query-text',
    '.user-query-container',
    'div[data-message-author-role="user"]',
    '.user-query-content'
  ].join(', ');

  const EDITOR_SELECTORS = [
    'rich-textarea div[contenteditable="true"]',
    'div.ql-editor[contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[contenteditable="true"]',
    'textarea'
  ];
  const RESPONSE_SELECTORS = [
    'model-response',
    'div[data-message-author-role="model"]',
    'div.model-response',
    '[data-test-id="model-response"]',
    '.response-container-content',
    'message-content',
    '.model-response-text'
  ].join(', ');
  let runtimeContract = {
    extensionVersion: '13.2.3',
    runtimeBuild: '13.2.3-r10',
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
  const cancelledRequestIds = new Set();

  function cancelExecution(reqId, reason = 'cancelled') {
    if (reqId) {
      cancelledRequestIds.add(reqId);
      if (cancelledRequestIds.size > 100) {
        const oldest = cancelledRequestIds.values().next().value;
        cancelledRequestIds.delete(oldest);
      }
    }
    if (!activeExecution) return false;
    if (!reqId || activeExecution.requestId === reqId) {
      activeExecution.cancelled = true;
      if (activeExecution.requestId) {
        cancelledRequestIds.add(activeExecution.requestId);
      }
      try { activeExecution.observer?.disconnect(); } catch (_) {}
      if (activeExecution.timer) {
        clearInterval(activeExecution.timer);
        activeExecution.timer = null;
      }
      if (typeof activeExecution.finish === 'function') {
        activeExecution.finish({ status: 'failed', text: '', error: reason });
      } else if (typeof activeExecution.resolve === 'function') {
        activeExecution.resolve({ status: 'failed', text: '', error: reason });
      }
      activeExecution = null;
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
    const style = window.getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const rect = element.getBoundingClientRect();
    if (rect.width > 15 && rect.height > 15) return true;
    // Check if element has display: contents or custom elements whose children are visible
    if (style.display === 'contents' || (rect.width === 0 && rect.height === 0)) {
      const childWithText = element.querySelector('p, div, span, .markdown, .model-response-text');
      if (childWithText) {
        const cStyle = window.getComputedStyle(childWithText);
        if (cStyle.visibility !== 'hidden' && cStyle.display !== 'none') {
          const cRect = childWithText.getBoundingClientRect();
          if (cRect.width > 15 && cRect.height > 15) return true;
        }
      }
      try {
        const range = document.createRange();
        range.selectNodeContents(element);
        const rRect = range.getBoundingClientRect();
        if (rRect.width > 15 && rRect.height > 15) return true;
      } catch (_) {}
    }
    return false;
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
      if (Date.now() > activeExecution.deadlineAtMs) {
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
    const deadlineAtMs = Date.now() + maxWaitMs;
    let matchStartTimeMs = null;
    let lastMatchedSurface = null;
    let lastActualRaw = '';
    let lastActualCanonical = '';
    let resolvedTarget = null;

    while (Date.now() < deadlineAtMs) {
      if (isStopped || activeExecution?.cancelled) return { ok: false, reason: 'cancelled' };

      let currentTarget = getTargetFn();
      if (!currentTarget || !currentTarget.isConnected) {
        currentTarget = editor();
      }
      if (!currentTarget) {
        await new Promise(r => setTimeout(r, 100));
        continue;
      }
      resolvedTarget = currentTarget;

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
        if (!matchStartTimeMs) {
          matchStartTimeMs = Date.now();
        } else if (Date.now() - matchStartTimeMs >= 200) {
          return {
            ok: true,
            target: resolvedTarget,
            surface: lastMatchedSurface,
            actualRaw: lastActualRaw,
            actualCanonical: lastActualCanonical
          };
        }
      } else {
        matchStartTimeMs = null;
      }

      await new Promise(r => setTimeout(r, 100));
    }

    return {
      ok: false,
      target: resolvedTarget,
      reason: 'readback_mismatch',
      surface: lastMatchedSurface || 'none',
      actualRaw: lastActualRaw,
      actualCanonical: lastActualCanonical
    };
  }

  function getUserTurnContainer(el) {
    if (!el || !el.isConnected) return null;
    return el.closest(USER_QUERY_SELECTORS) || el;
  }

  function getTurnContainer(el) {
    if (!el || !el.isConnected) return null;
    return el.closest('model-response, div[data-message-author-role="model"], div.model-response, [data-test-id="model-response"]') || el;
  }

  const EXCLUDED_STATUS_SELECTORS = [
    'button',
    '[role="button"]',
    '[role="status"]',
    '[role="progressbar"]',
    '[aria-live]',
    '.thinking',
    '.thought-container',
    '.thinking-container',
    '.status-container',
    '.status-indicator',
    'header',
    '.response-header',
    '.model-response-header',
    '.toolbar',
    '.actions',
    '.response-actions',
    'mat-progress-bar',
    '[data-test-id*="status"]',
    '[data-test-id*="thought"]',
    '[data-test-id*="thinking"]',
    '[data-test-id*="header"]',
    '.loading-dots',
    '.streaming'
  ].join(', ');

  const CONTAMINATION_PATTERNS = [
    /Initiating the Analysis/i,
    /Gemini의\s*응답/i,
    /Gemini\s*response/i,
    /Thinking\.\.\./i,
    /Analyzing\.\.\./i,
    /생각\s*중/i,
    /분석\s*중/i,
    /Show\s*thinking/i,
    /Hide\s*thinking/i,
    /생각\s*과정/i,
    /View\s*other\s*drafts/i,
    /다른\s*답안\s*보기/i
  ];

  function isExcludedNode(el) {
    if (!el || typeof el.matches !== 'function') return false;
    try {
      if (el.matches(EXCLUDED_STATUS_SELECTORS)) return true;
      if (typeof el.closest === 'function' && el.closest(EXCLUDED_STATUS_SELECTORS)) return true;
    } catch (_) {}
    return false;
  }

  function extractCleanText(node) {
    if (!node || !node.isConnected) return '';
    if (node.nodeType === 1 && isExcludedNode(node)) {
      return '';
    }

    let text = '';
    if (typeof document !== 'undefined' && typeof document.createTreeWalker === 'function' && typeof NodeFilter !== 'undefined') {
      const walker = document.createTreeWalker(
        node,
        NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
        {
          acceptNode(n) {
            if (n.nodeType === 1) {
              if (isExcludedNode(n)) {
                return NodeFilter.FILTER_REJECT;
              }
              return NodeFilter.FILTER_SKIP;
            }
            if (n.nodeType === 3) {
              if (n.parentElement && isExcludedNode(n.parentElement)) {
                return NodeFilter.FILTER_REJECT;
              }
              return NodeFilter.FILTER_ACCEPT;
            }
            return NodeFilter.FILTER_SKIP;
          }
        }
      );

      let curr;
      while ((curr = walker.nextNode())) {
        text += curr.textContent || '';
      }
    } else {
      try {
        if (typeof node.cloneNode === 'function') {
          const clone = node.cloneNode(true);
          const excludedElements = clone.querySelectorAll ? clone.querySelectorAll(EXCLUDED_STATUS_SELECTORS) : [];
          for (const el of excludedElements) {
            el.remove();
          }
          text = clone.innerText || clone.textContent || '';
        } else {
          text = node.innerText || node.textContent || '';
        }
      } catch (_) {
        text = node.innerText || node.textContent || '';
      }
    }

    const cleaned = text.trim();
    if (!cleaned) return '';

    for (const pat of CONTAMINATION_PATTERNS) {
      if (pat.test(cleaned)) {
        const stripped = cleaned.replace(pat, '').trim();
        if (stripped.length === 0 || stripped.length < 10) {
          return '';
        }
      }
    }

    return cleaned;
  }

  function resolveTurnCandidate(turnNode) {
    if (!turnNode || !turnNode.isConnected) return null;

    const bodySelectors = [
      'message-content',
      'div.markdown',
      'div.model-response-text',
      '.response-body-inner',
      '.response-container-content'
    ];

    let answerBodyNode = null;
    for (const sel of bodySelectors) {
      const candidates = turnNode.querySelectorAll(sel);
      for (const el of candidates) {
        if (!el || !el.isConnected) continue;
        if (isExcludedNode(el)) continue;
        const st = window.getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none') continue;
        answerBodyNode = el;
        break;
      }
      if (answerBodyNode) break;
    }

    const statusNodes = [...turnNode.querySelectorAll(EXCLUDED_STATUS_SELECTORS)];
    const streamingNode = turnNode.querySelector('.loading-dots, .streaming, [aria-busy="true"], mat-progress-bar, [data-is-generating="true"]');

    const targetTextNode = answerBodyNode || turnNode;
    const cleanText = extractCleanText(targetTextNode);

    let isCandidateVisible = false;
    const tStyle = window.getComputedStyle(turnNode);
    if (tStyle.visibility !== 'hidden' && tStyle.display !== 'none') {
      const rect = turnNode.getBoundingClientRect();
      if (rect.width > 5 && rect.height > 5) {
        isCandidateVisible = true;
      } else {
        try {
          const range = document.createRange();
          range.selectNodeContents(turnNode);
          const r = range.getBoundingClientRect();
          if (r.width > 5 && r.height > 5) isCandidateVisible = true;
        } catch (_) {}
      }
    }

    if (!isCandidateVisible) {
      if (streamingNode || answerBodyNode || cleanText.length > 0) {
        isCandidateVisible = true;
      }
    }

    return {
      turnNode,
      answerBodyNode,
      statusNodes,
      streamingNode,
      textNode: answerBodyNode || turnNode,
      text: cleanText,
      isVisible: isCandidateVisible
    };
  }

  function extractResponseText(node) {
    if (!node) return '';
    const cand = resolveTurnCandidate(node);
    if (cand) {
      return cand.text || '';
    }
    return extractCleanText(node);
  }

  function extractUserQueryText(node) {
    if (!node) return '';
    const queryEl = node.querySelector('.query-text, .user-query-text, p, div') || node;
    return (queryEl.innerText || queryEl.textContent || '').trim();
  }

  function getCandidateInventory(initialResponseSet = new Set(), baselineResponseFingerprints = new Set(), currentUserTurn = null) {
    const allMatches = [...document.querySelectorAll(RESPONSE_SELECTORS)];
    const responseSelectorMatches = allMatches.length;

    // Deduplicate turn containers by DOM node identity
    const uniqueTurnNodes = [];
    const seenTurnSet = new Set();
    for (const el of allMatches) {
      const turnContainer = getTurnContainer(el);
      if (turnContainer && !seenTurnSet.has(turnContainer)) {
        seenTurnSet.add(turnContainer);
        uniqueTurnNodes.push(turnContainer);
      }
    }
    const responseUniqueTurns = uniqueTurnNodes.length;

    const inventory = [];
    for (const turnNode of uniqueTurnNodes) {
      const cand = resolveTurnCandidate(turnNode);
      if (!cand) continue;

      const isConn = Boolean(turnNode.isConnected);
      const isVis = cand.isVisible;
      const textCanonical = canonicalPromptText(cand.text);

      let excludeReason = null;
      if (!isConn) excludeReason = 'disconnected';
      else if (!isVis) excludeReason = 'not_visible';
      else if (initialResponseSet.has(turnNode)) excludeReason = 'initial_baseline_node';
      else if (textCanonical && baselineResponseFingerprints.has(textCanonical)) excludeReason = 'baseline_text_match';
      else if (currentUserTurn && (currentUserTurn.compareDocumentPosition(turnNode) & Node.DOCUMENT_POSITION_FOLLOWING) === 0) excludeReason = 'precedes_user_turn';

      cand.excludeReason = excludeReason;
      cand.isCandidate = !excludeReason;
      inventory.push(cand);
    }

    const validCandidates = inventory.filter(c => c.isCandidate);
    const visibleTextCandidates = validCandidates.length;

    return {
      responseSelectorMatches,
      responseUniqueTurns,
      visibleTextCandidates,
      inventory,
      validCandidates
    };
  }

  function getUserInventory() {
    const allMatches = [...document.querySelectorAll(USER_QUERY_SELECTORS)];
    const userSelectorMatches = allMatches.length;
    const uniqueUserNodes = [];
    const seenUserSet = new Set();
    for (const el of allMatches) {
      const container = getUserTurnContainer(el);
      if (container && !seenUserSet.has(container)) {
        seenUserSet.add(container);
        uniqueUserNodes.push(container);
      }
    }
    const userUniqueTurns = uniqueUserNodes.length;
    const visibleUserNodes = uniqueUserNodes.filter(visible);
    return {
      userSelectorMatches,
      userUniqueTurns,
      visibleUserNodes,
      allMatches
    };
  }

  function responseNodes() {
    const inv = getCandidateInventory();
    return inv.inventory.filter(c => c.isVisible).map(c => c.turnNode);
  }

  function userQueryNodes() {
    return getUserInventory().visibleUserNodes;
  }

  function detectGenerationEvidence(boundNode) {
    if (!boundNode) return 'none';
    // Strictly scoped to indicator inside currently bound turn OR composer generation control (never global document)
    if (boundNode.querySelector('.loading-dots, .streaming, [aria-busy="true"], mat-progress-bar, [data-is-generating="true"]')) {
      return 'local_streaming';
    }
    const comp = findComposer(editor());
    if (comp?.querySelector('[aria-label*="중지"], [aria-label*="Stop"], [aria-label*="생성 중지"]')) {
      return 'composer_stop_button';
    }
    return 'idle';
  }

  async function waitForSendReady(input, timeoutMs = 2500) {
    const deadlineAtMs = Date.now() + timeoutMs;
    while (Date.now() < deadlineAtMs) {
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
    function isExecutionCancelled() {
      return isStopped || execState.cancelled || cancelledRequestIds.has(command.requestId);
    }
    if (isExecutionCancelled()) return { status: 'failed', text: '', error: 'cancelled' };

    const initialResponseList = responseNodes();
    const initialResponseSet = new Set(initialResponseList);
    const baselineResponseFingerprints = new Set(
      initialResponseList.map(n => canonicalPromptText(extractResponseText(n))).filter(Boolean)
    );
    const initialUserQueries = userQueryNodes();
    const initialUserQuerySet = new Set(initialUserQueries);
    const baselineUserQueryFingerprints = new Set(
      initialUserQueries.map(q => canonicalPromptText(extractUserQueryText(q))).filter(Boolean)
    );

    function emitEvent(type, payload = {}) {
      if (isExecutionCancelled()) return;
      try {
        chrome.runtime.sendMessage({
          type: 'NFA_EVENT',
          event: { type, rid: command.requestId, ...payload }
        });
      } catch (_) {}
    }

    const freshChatVerified = (initialResponseList.length === 0 && initialUserQueries.length === 0);
    if (freshChatVerified) {
      console.log('[GEMINI][FRESH_CHAT_READY]', JSON.stringify({
        tab: command.tabId || null,
        instance: INSTANCE_ID,
        epoch: conversationEpoch
      }));
      emitEvent('FRESH_CHAT_READY', {
        tab: command.tabId || null,
        instance: INSTANCE_ID,
        epoch: conversationEpoch
      });
    }

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
      if (readbackResult.ok) {
        if (readbackResult.target) target = readbackResult.target;
        break;
      }

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

    // Pre-send validation: Ensure target editor is connected and still contains canonical prompt
    if (!target || !target.isConnected) {
      target = editor();
    }
    const finalSurfaces = getEditorSurfaces(target);
    const finalReadbackOk = finalSurfaces.some(s => canonicalPromptText(s.text) === expectedCanonical);
    if (!finalReadbackOk) {
      logSendDiag({ button: null, confirmed: false, boundNode: false });
      return { status: 'dom_unsupported', text: '', error: 'prompt_editor_changed_before_send' };
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

    // Response and User Turn Tracking Variables
    let currentUserTurn = null;
    let targetResponseNode = null;
    let boundAtMs = 0;
    let reResolveAttempted = false;
    let textNonEmptyLogged = false;
    let textStableLogged = false;

    function bindResponseNode(node, evidence) {
      if (!node) return null;
      if (targetResponseNode && targetResponseNode.isConnected && targetResponseNode === node) {
        return targetResponseNode;
      }

      // Unified inventory invariant verification
      const inv = getCandidateInventory(initialResponseSet, baselineResponseFingerprints, currentUserTurn);
      const cand = inv.inventory.find(c => c.turnNode === node);
      if (!cand || !cand.isVisible || inv.visibleTextCandidates === 0) {
        console.error('[GEMINI][INVARIANT_VIOLATION] Attempted to bind node when visibleTextCandidates=0 or node not visible', {
          nodeTag: node.tagName,
          visibleTextCandidates: inv.visibleTextCandidates,
          responseSelectorMatches: inv.responseSelectorMatches,
          excludeReason: cand?.excludeReason || 'not_in_inventory'
        });
        return null;
      }

      targetResponseNode = node;
      boundAtMs = Date.now();
      reResolveAttempted = false;
      const rInv = getCandidateInventory(initialResponseSet, baselineResponseFingerprints, currentUserTurn);
      console.log('[GEMINI][RESPONSE_TURN_BOUND]', JSON.stringify({
        rid: command.requestId,
        evidence: evidence,
        responseUniqueTurns: rInv.responseUniqueTurns,
        visibleTextCandidates: rInv.visibleTextCandidates,
        hasUserTurnAnchor: Boolean(currentUserTurn),
        nodeTag: node.tagName
      }));
      emitEvent('RESPONSE_TURN_BOUND', {
        responseUniqueTurns: rInv.responseUniqueTurns,
        visibleTextCandidates: rInv.visibleTextCandidates,
        evidence: evidence
      });
      return targetResponseNode;
    }

    function findNewUserQuery() {
      const userInv = getUserInventory();
      const currentQueries = userInv.visibleUserNodes;
      const exactPromptMatch = currentQueries.find(q => {
        const qText = canonicalPromptText(extractUserQueryText(q));
        return qText === expectedCanonical && !initialUserQuerySet.has(q);
      }) || currentQueries.find(q => canonicalPromptText(extractUserQueryText(q)) === expectedCanonical && !initialUserQuerySet.has(q));
      if (exactPromptMatch) return exactPromptMatch;

      const novelQuery = currentQueries.find(q => {
        if (initialUserQuerySet.has(q)) return false;
        const qText = canonicalPromptText(extractUserQueryText(q));
        return qText && !baselineUserQueryFingerprints.has(qText);
      });
      if (novelQuery) return novelQuery;

      for (const q of currentQueries) {
        if (!initialUserQuerySet.has(q)) return q;
      }

      return null;
    }

    function findTurnResponseCandidate() {
      const inv = getCandidateInventory(initialResponseSet, baselineResponseFingerprints, currentUserTurn);
      const valid = inv.validCandidates;

      if (freshChatVerified) {
        if (valid.length > 0) {
          return valid[valid.length - 1].turnNode;
        }
        return null;
      }

      if (!currentUserTurn || !currentUserTurn.isConnected) {
        const newQuery = findNewUserQuery();
        if (newQuery) {
          currentUserTurn = newQuery;
          console.log('[GEMINI][USER_TURN_BOUND]', JSON.stringify({ rid: command.requestId }));
        }
      }

      // Strategy 1: Find first response strictly following currentUserTurn in document order
      if (currentUserTurn && currentUserTurn.isConnected) {
        for (const cand of valid) {
          if ((currentUserTurn.compareDocumentPosition(cand.turnNode) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0) {
            return cand.turnNode;
          }
        }
      }

      // Strategy 2: If user turn not yet bound or no following candidate, adopt latest valid candidate
      if (valid.length > 0) {
        return valid[valid.length - 1].turnNode;
      }

      return null;
    }

    // Verify send confirmation structurally (up to 12s, then 1 retry up to 8s)
    let confirmed = false;
    let userTurnConfirmedLogged = false;
    const checkDeadline = Date.now() + 12000;

    while (Date.now() < checkDeadline) {
      if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };

      // 1st Priority: New user turn with prompt correlation confirmed
      const newQuery = findNewUserQuery();
      if (newQuery) {
        currentUserTurn = newQuery;
        confirmed = true;
        if (!userTurnConfirmedLogged) {
          userTurnConfirmedLogged = true;
          const uInv = getUserInventory();
          console.log('[GEMINI][USER_TURN_CONFIRMED]', JSON.stringify({
            rid: command.requestId,
            userUniqueTurns: uInv.userUniqueTurns
          }));
          emitEvent('USER_TURN_CONFIRMED', {
            userUniqueTurns: uInv.userUniqueTurns
          });
        }
        const candidate = findTurnResponseCandidate();
        if (candidate) {
          bindResponseNode(candidate, 'send_phase_user_correlated');
          break;
        }
      }

      // 2nd Priority (fallback): STRICTLY ONLY when freshChatVerified is true AND baseline model turns = 0
      if (!confirmed && freshChatVerified && initialResponseSet.size === 0) {
        const candidate = findTurnResponseCandidate();
        if (candidate) {
          confirmed = true;
          bindResponseNode(candidate, 'send_phase_fresh_fallback');
          break;
        }
      }

      if (confirmed && currentUserTurn) {
        break;
      }

      await new Promise(r => setTimeout(r, 150));
    }

    // 2nd Send Attempt if not structurally confirmed after 12s
    if (!confirmed) {
      if (!target || !target.isConnected) target = editor();
      const retryCtrl = findSendControl(target);
      let retryDispatched = false;
      if (retryCtrl?.button && !retryCtrl.button.disabled && retryCtrl.button.getAttribute('aria-disabled') !== 'true') {
        selectedBtn = retryCtrl.button;
        retryCtrl.button.click();
        retryDispatched = true;
      }
      if (target && target.isConnected) {
        target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
        retryDispatched = true;
      }

      const retryDeadline = Date.now() + 8000;
      while (Date.now() < retryDeadline) {
        if (isStopped || execState.cancelled) return { status: 'failed', text: '', error: 'cancelled' };

        // 1st Priority: New user turn with prompt correlation confirmed
        const newQuery = findNewUserQuery();
        if (newQuery) {
          currentUserTurn = newQuery;
          confirmed = true;
          if (!userTurnConfirmedLogged) {
            userTurnConfirmedLogged = true;
            const uInv = getUserInventory();
            console.log('[GEMINI][USER_TURN_CONFIRMED]', JSON.stringify({
              rid: command.requestId,
              userUniqueTurns: uInv.userUniqueTurns
            }));
            emitEvent('USER_TURN_CONFIRMED', {
              userUniqueTurns: uInv.userUniqueTurns
            });
          }
          const candidate = findTurnResponseCandidate();
          if (candidate) {
            bindResponseNode(candidate, 'send_phase_retry_user_correlated');
            break;
          }
        }

        // 2nd Priority (fallback): STRICTLY ONLY when freshChatVerified is true AND baseline model turns = 0
        if (!confirmed && freshChatVerified && initialResponseSet.size === 0) {
          const candidate = findTurnResponseCandidate();
          if (candidate) {
            confirmed = true;
            bindResponseNode(candidate, 'send_phase_retry_fresh_fallback');
            break;
          }
        }

        if (confirmed && currentUserTurn) {
          break;
        }

        await new Promise(r => setTimeout(r, 150));
      }
    }

    logSendDiag({
      button: selectedBtn,
      totalCandidates: sendCtrl?.totalCandidates || 0,
      confirmed: confirmed,
      boundNode: Boolean(targetResponseNode)
    });

    if (!confirmed) {
      const failReason = selectedBtn ? 'user_turn_not_created' : 'send_not_confirmed';
      return { status: 'failed', text: '', error: failReason };
    }

    const deadlineAtMs = execState.deadlineAtMs;
    let lastMutationAtMs = Date.now();
    let previous = '';

    function checkResponseRecovery() {
      if (freshChatVerified && (Date.now() - execState.startedAtMs >= 5000) && confirmed && !targetResponseNode) {
        const candidate = findTurnResponseCandidate();
        if (candidate) {
          bindResponseNode(candidate, 'fresh_chat_latest_visible');
          return candidate;
        }
      }
      return null;
    }

    let lastDiagReportAtMs = 0;
    function reportWaitDiag(reason = 'periodic') {
      if (isExecutionCancelled()) return;
      const nowMs = Date.now();
      if (reason === 'periodic' && nowMs - lastDiagReportAtMs < 4800) return;
      lastDiagReportAtMs = nowMs;
      const curText = targetResponseNode ? extractResponseText(targetResponseNode) : '';
      const candDiag = getCandidateInventory(initialResponseSet, baselineResponseFingerprints, currentUserTurn);
      const userDiag = getUserInventory();

      if (targetResponseNode && candDiag.visibleTextCandidates === 0) {
        console.error('[GEMINI][INVARIANT_VIOLATION] targetResponseNode bound but visibleTextCandidates=0');
      }
      if (confirmed && userDiag.userUniqueTurns === 0 && candDiag.responseUniqueTurns === 0) {
        console.error('[GEMINI][INVARIANT_VIOLATION] sendConfirmed=true but userUniqueTurns=0 and responseUniqueTurns=0');
      }

      const diag = {
        rid: command.requestId,
        elapsedMs: nowMs - execState.startedAtMs,
        freshChatVerified: Boolean(freshChatVerified),
        tabId: command.tabId || null,
        contentInstanceId: INSTANCE_ID,
        conversationEpoch: conversationEpoch,
        sendConfirmed: Boolean(confirmed),
        userSelectorMatches: userDiag.userSelectorMatches,
        userUniqueTurns: userDiag.userUniqueTurns,
        responseSelectorMatches: candDiag.responseSelectorMatches,
        responseUniqueTurns: candDiag.responseUniqueTurns,
        visibleTextCandidates: candDiag.visibleTextCandidates,
        responseBound: Boolean(targetResponseNode),
        responseTextLength: curText.length,
        lastMutationAgeMs: targetResponseNode ? (nowMs - lastMutationAtMs) : 0,
        generationEvidence: detectGenerationEvidence(targetResponseNode),
        runtimeBuild: runtimeContract.runtimeBuild
      };
      console.log('[GEMINI][WAIT_DIAG]', JSON.stringify(diag));
      try {
        chrome.runtime.sendMessage({ type: 'NFA_WAIT_DIAG', diag });
      } catch (_) {}
    }

    if (isExecutionCancelled()) {
      return { status: 'failed', text: '', error: 'cancelled' };
    }

    return new Promise((resolve) => {
      let resolved = false;

      const cleanupObserver = () => {
        try { observer?.disconnect(); } catch (_) {}
        if (checkTimer) {
          clearInterval(checkTimer);
          checkTimer = null;
        }
        if (execState.observer === observer) execState.observer = null;
        if (execState.timer === checkTimer) execState.timer = null;
      };

      const finish = (res) => {
        if (resolved) return;
        resolved = true;
        cleanupObserver();
        if (isExecutionCancelled()) {
          resolve({ status: 'failed', text: '', error: 'cancelled' });
          return;
        }
        resolve(res);
      };

      execState.finish = finish;
      execState.resolve = resolve;

      if (isExecutionCancelled()) {
        return finish({ status: 'failed', text: '', error: 'cancelled' });
      }

      const checkOutput = () => {
        if (isStopped) return finish({ status: 'failed', text: '', error: 'runtime_stopped' });
        if (isExecutionCancelled()) return finish({ status: 'failed', text: '', error: 'cancelled' });
        const nowMs = Date.now();
        if (nowMs > deadlineAtMs) {
          reportWaitDiag('timeout');
          const hasText = targetResponseNode && extractResponseText(targetResponseNode).length > 0;
          const timeoutErr = hasText ? 'response_stalled' : (targetResponseNode ? 'response_stream_no_text' : 'response_turn_not_found');
          return finish({ status: 'timeout', text: '', error: timeoutErr });
        }

        reportWaitDiag('periodic');

        // Check invariant: response selector matches > 0, visible = 0, but bound = true!
        if (targetResponseNode) {
          const inv = getCandidateInventory(initialResponseSet, baselineResponseFingerprints, currentUserTurn);
          if (inv.visibleTextCandidates === 0) {
            console.error('[GEMINI][INVARIANT_VIOLATION] bound=true but visibleTextCandidates=0');
            return finish({ status: 'failed', text: '', error: 'response_binding_invariant_violation' });
          }
        }

        if (!targetResponseNode || !targetResponseNode.isConnected) {
          const candidate = findTurnResponseCandidate() || checkResponseRecovery();
          if (candidate) {
            bindResponseNode(candidate, 'observer_phase');
          }
        }

        if (!targetResponseNode) return;

        const current = extractResponseText(targetResponseNode);

        // Zero-length handling: differentiate empty-node misbinding vs legitimate thinking grace
        if (!current || current.length === 0) {
          if (!boundAtMs) boundAtMs = Date.now();
          const zeroLenDuration = Date.now() - boundAtMs;
          const localEvidence = detectGenerationEvidence(targetResponseNode);
          const hasStreamingEvidence = (localEvidence === 'local_streaming' || localEvidence === 'composer_stop_button');

          if (hasStreamingEvidence) {
            // Legitimate thinking/streaming grace: allow up to 15,000ms for first token
            if (zeroLenDuration >= 15000) {
              return finish({ status: 'failed', text: '', error: 'response_stream_no_text' });
            }
          } else {
            // No streaming evidence: empty/dead node misbinding after 3,500ms -> discard & re-resolve 1 time
            if (zeroLenDuration >= 3500) {
              if (!reResolveAttempted) {
                reResolveAttempted = true;
                console.warn('[GEMINI][RE_RESOLVE] responseBound textLen=0 without streaming evidence for 3.5s -> discarding binding and re-resolving 1회');
                targetResponseNode = null;
                boundAtMs = 0;
                const reCandidate = findTurnResponseCandidate();
                if (reCandidate) {
                  bindResponseNode(reCandidate, 're_resolve_after_stalled_zero_len');
                } else {
                  return finish({ status: 'failed', text: '', error: 'response_text_target_not_found' });
                }
                return;
              } else {
                // 1회 re-resolve 후에도 여전히 textLen=0 and no streaming evidence
                return finish({ status: 'failed', text: '', error: 'response_stream_no_text' });
              }
            }
          }
          return;
        }

        // CRITICAL GUARD: Never accept text identical to any baseline response
        const currentCanonical = canonicalPromptText(current);
        if (baselineResponseFingerprints.has(currentCanonical)) {
          targetResponseNode = null;
          return;
        }

        // Text is non-empty!
        if (!textNonEmptyLogged) {
          textNonEmptyLogged = true;
          console.log('[GEMINI][TEXT_NONEMPTY]', JSON.stringify({
            rid: command.requestId,
            chars: current.length
          }));
          emitEvent('TEXT_NONEMPTY', {
            chars: current.length
          });
        }

        if (current !== previous) {
          previous = current;
          lastMutationAtMs = Date.now();
        } else {
          const mutationAge = Date.now() - lastMutationAtMs;
          const localEvidence = detectGenerationEvidence(targetResponseNode);
          const isGenerating = (localEvidence === 'local_streaming' || localEvidence === 'composer_stop_button');

          // Authoritative completion: text is stable for >= 1800ms and no local streaming / composer stop button
          if (mutationAge >= 1800 && !isGenerating) {
            if (!textStableLogged) {
              textStableLogged = true;
              console.log('[GEMINI][TEXT_STABLE]', JSON.stringify({
                rid: command.requestId,
                chars: current.length,
                stableMs: mutationAge
              }));
              emitEvent('TEXT_STABLE', {
                chars: current.length,
                stableMs: mutationAge
              });
            }
            return finish({ status: 'completed', text: current, error: '' });
          }
          // Fallback completion: text has been stable for >= 2500ms regardless of external indicators
          if (mutationAge >= 2500) {
            if (!textStableLogged) {
              textStableLogged = true;
              console.log('[GEMINI][TEXT_STABLE]', JSON.stringify({
                rid: command.requestId,
                chars: current.length,
                stableMs: mutationAge
              }));
              emitEvent('TEXT_STABLE', {
                chars: current.length,
                stableMs: mutationAge
              });
            }
            return finish({ status: 'completed', text: current, error: '' });
          }
        }
      };

      const targetRoot = document.querySelector('chat-history, main, body') || document.body;
      const observer = new MutationObserver(() => {
        checkOutput();
      });
      observer.observe(targetRoot, { childList: true, subtree: true, characterData: true });

      const checkTimer = setInterval(checkOutput, 200);
      execState.observer = observer;
      execState.timer = checkTimer;
    });
  }

  async function execute(command) {
    if (isStopped) return { status: 'failed', text: '', error: 'runtime_stopped' };
    const currentReqId = command?.requestId || Math.random().toString(36).slice(2, 10);
    if (cancelledRequestIds.has(currentReqId)) {
      return { status: 'failed', text: '', error: 'cancelled' };
    }
    if (activeExecution) {
      if (Date.now() > activeExecution.deadlineAtMs) {
        cancelExecution(activeExecution.requestId, 'command_deadline_exceeded');
      } else {
        return { status: 'busy', text: '', error: 'runtime_busy' };
      }
    }

    // Standardize external time to milliseconds once upon receiving, never reconvert inside
    let deadlineAtMs;
    if (typeof command?.deadlineAtMs === 'number' && command.deadlineAtMs > 0) {
      deadlineAtMs = command.deadlineAtMs;
    } else if (typeof command?.deadlineAt === 'number' && command.deadlineAt > 0) {
      deadlineAtMs = command.deadlineAt < 1e11 ? Math.round(command.deadlineAt * 1000) : Math.round(command.deadlineAt);
    } else {
      deadlineAtMs = Date.now() + 55000;
    }

    const execState = {
      requestId: currentReqId,
      startedAtMs: Date.now(),
      deadlineAtMs: deadlineAtMs,
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
        if (activeExecution.timer) {
          clearInterval(activeExecution.timer);
          activeExecution.timer = null;
        }
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
        busySince: activeExecution?.startedAtMs || null,
        busyDeadlineAt: activeExecution?.deadlineAtMs || null,
        busySinceMs: activeExecution?.startedAtMs || null,
        busyDeadlineAtMs: activeExecution?.deadlineAtMs || null
      });
      return true;
    }

    if (message.type === 'NFA_CANCEL_COMMAND') {
      const cancelled = cancelExecution(message.requestId, 'cancelled_by_bridge');
      sendResponse({ ok: true, cancelled, status: pageStatus() });
      return true;
    }

    if (message.type === 'NFA_CHECK_FRESH_CHAT') {
      const userDiag = getUserInventory();
      const candDiag = getCandidateInventory();
      const ed = editor();
      const isFresh = userDiag.visibleUserNodes.length === 0 && candDiag.visibleTextCandidates === 0 && Boolean(ed);
      sendResponse({
        ok: true,
        fresh: isFresh,
        contentInstanceId: INSTANCE_ID,
        conversationEpoch: conversationEpoch,
        userQueryCount: userDiag.visibleUserNodes.length,
        userSelectorMatches: userDiag.userSelectorMatches,
        userUniqueTurns: userDiag.userUniqueTurns,
        selector_matches: candDiag.responseSelectorMatches,
        visible_candidates: candDiag.visibleTextCandidates,
        responseSelectorMatches: candDiag.responseSelectorMatches,
        responseUniqueTurns: candDiag.responseUniqueTurns,
        visibleTextCandidates: candDiag.visibleTextCandidates,
        bound_response: Boolean(activeExecution && activeExecution.targetResponseNode),
        responseCount: candDiag.visibleTextCandidates,
        composerAvailable: Boolean(ed)
      });
      return true;
    }

    if (message.type === 'NFA_RESET_FRESH_CHAT') {
      conversationEpoch++;
      const newChatSelectors = [
        'a[href="/app"]',
        'button[aria-label*="새 대화"]',
        'button[aria-label*="New chat"]',
        '[data-test-id="new-chat-button"]',
        '.new-chat-button'
      ];
      let clicked = false;
      for (const sel of newChatSelectors) {
        const btn = document.querySelector(sel);
        if (btn && visible(btn)) {
          btn.click();
          clicked = true;
          break;
        }
      }
      sendResponse({ ok: true, clicked, conversationEpoch, contentInstanceId: INSTANCE_ID });
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
    }),
    resolveTurnCandidate,
    extractCleanText,
    extractResponseText
  };
})();
