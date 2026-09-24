(() => {
  'use strict';

  if (globalThis.__NFA_GEMINI_RUNTIME__ && typeof globalThis.__NFA_GEMINI_RUNTIME__.stop === 'function') {
    try { globalThis.__NFA_GEMINI_RUNTIME__.stop(); } catch (_) {}
  }

  const INSTANCE_ID = Math.random().toString(36).slice(2, 10);
  let conversationEpoch = 1;
  let isStopped = false;
  const eventCleanups = [];

  const PRIMARY_USER_TURN_ROOT_SELECTORS = [
    'user-query', 'div[data-message-author-role="user"]',
    '[data-test-id="user-query"]', '.user-message'
  ].join(', ');
  const FALLBACK_USER_TURN_ROOT_SELECTOR = '.user-query-container';
  const USER_TURN_ROOT_SELECTORS = `${PRIMARY_USER_TURN_ROOT_SELECTORS}, ${FALLBACK_USER_TURN_ROOT_SELECTOR}`;
  const USER_TURN_TEXT_SELECTORS = [
    '.query-text', '.user-query-text', '.user-query-content', 'p', 'div'
  ].join(', ');
  const USER_QUERY_SELECTORS = USER_TURN_ROOT_SELECTORS;
  const EDITOR_SELECTORS = [
    'rich-textarea div[contenteditable="true"]', 'div.ql-editor[contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]', 'div[contenteditable="true"]', 'textarea'
  ];
  const RESPONSE_SELECTORS = [
    'model-response', 'div[data-message-author-role="model"]', 'div.model-response',
    '[data-test-id="model-response"]', '.response-container-content', 'message-content', '.model-response-text'
  ].join(', ');
  let runtimeContract = {
    extensionVersion: '13.2.4', runtimeBuild: '13.2.4-send-harden-v3', protocolVersion: 3, bridgeSchemaVersion: 2
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
        if (globalThis.__NFA_GEMINI_RUNTIME__) globalThis.__NFA_GEMINI_RUNTIME__.build = runtimeContract.runtimeBuild;
      }
    });
  } catch (_) {}

  let activeExecution = null;
  const cancelledRequestIds = new Set();

  function cancelExecution(reqId, reason = 'cancelled') {
    if (reqId) {
      cancelledRequestIds.add(reqId);
      if (cancelledRequestIds.size > 100) cancelledRequestIds.delete(cancelledRequestIds.values().next().value);
    }
    if (!activeExecution) return false;
    if (!reqId || activeExecution.requestId === reqId) {
      activeExecution.cancelled = true;
      if (activeExecution.requestId) cancelledRequestIds.add(activeExecution.requestId);
      if (typeof activeExecution.cleanupObserver === 'function') {
        try { activeExecution.cleanupObserver(); } catch (_) {}
      }
      try { activeExecution.observer?.disconnect(); } catch (_) {}
      if (activeExecution.timer) { clearInterval(activeExecution.timer); activeExecution.timer = null; }
      if (typeof activeExecution.finish === 'function') activeExecution.finish({ status: 'failed', text: '', error: reason });
      else if (typeof activeExecution.resolve === 'function') activeExecution.resolve({ status: 'failed', text: '', error: reason });
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
    if (globalThis.__NFA_GEMINI_RUNTIME__?.instanceId === INSTANCE_ID) delete globalThis.__NFA_GEMINI_RUNTIME__;
  }

  function checkExtensionInvalidated(err) {
    const msg = String(err?.message || err || '');
    if (msg.includes('Extension context invalidated')) { stopRuntime(); return true; }
    return false;
  }

  function canonicalPromptText(value) {
    return String(value ?? '').normalize('NFC').replace(/\r\n?/g, '\n').replace(/[\u2028\u2029]/g, '\n')
      .replace(/[\u00A0\u2007\u202F]/g, ' ').replace(/[\u200B-\u200D\u2060\uFEFF\uFE0E\uFE0F]/g, '')
      .replace(/[\u200E\u200F\u202A-\u202E\u2066-\u2069]/g, '')
      .replace(/[\u201C\u201D\u201E\u201F\u2033\u2036]/g, '"')
      .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035]/g, "'")
      .replace(/[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]/g, '-')
      .replace(/[•·∙]/g, '-')
      .replace(/…/g, '...')
      .replace(/\s+/gu, ' ').trim();
  }

  function strictNormalizePrompt(text) {
    if (typeof text !== 'string') return '';
    return text
      .normalize('NFC')
      .replace(/\r\n?/g, '\n')
      .replace(/[\u2028\u2029]/g, '\n')
      .replace(/[\u200B-\u200D\u2060\uFEFF\u200E\u200F\u202A-\u202E\u2066-\u2069]/g, '')
      .replace(/[\u00A0\u2007\u202F]/g, ' ')
      .trim();
  }

  function isExactPromptMatch(actualText, expectedText) {
    const act = strictNormalizePrompt(actualText);
    const exp = strictNormalizePrompt(expectedText);
    if (!act || !exp) return false;
    return act === exp;
  }
  const isStrictPromptMatch = isExactPromptMatch;

  function isPromptMatch(actualText, expectedText) {
    const act = canonicalPromptText(actualText);
    const exp = canonicalPromptText(expectedText);
    if (!act && !exp) return true;
    if (!act || !exp) return false;
    if (act === exp) return true;
    if (exp.length >= 30) {
      const prefixLen = Math.min(40, Math.floor(exp.length * 0.3));
      const expPrefix = exp.slice(0, prefixLen);
      const actPrefix = act.slice(0, prefixLen);
      const ratio = act.length / exp.length;
      if (expPrefix === actPrefix && ratio >= 0.85 && ratio <= 1.15) return true;
      if ((act.startsWith(expPrefix) || exp.startsWith(actPrefix)) && ratio >= 0.80) return true;
    }
    return false;
  }

  function simpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) hash = (Math.imul(31, hash) + str.charCodeAt(i)) | 0;
    return 'h_' + (hash >>> 0).toString(16);
  }
  function countOccurrences(str, pattern) { const m = (str || '').match(pattern); return m ? m.length : 0; }
  function findFirstMismatchIndex(a, b) {
    const len = Math.min(a.length, b.length);
    for (let i = 0; i < len; i++) if (a[i] !== b[i]) return i;
    return a.length !== b.length ? len : -1;
  }

  function visible(element) {
    if (!element || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const rect = element.getBoundingClientRect();
    if (rect.width > 15 && rect.height > 15) return true;
    if (style.display === 'contents' || (rect.width === 0 && rect.height === 0)) {
      const childWithText = element.querySelector('p, div, span, .markdown, .model-response-text');
      if (childWithText) {
        const cStyle = window.getComputedStyle(childWithText);
        if (cStyle.visibility !== 'hidden' && cStyle.display !== 'none') {
          const cRect = childWithText.getBoundingClientRect();
          if (cRect.width > 15 && cRect.height > 15) return true;
        }
      }
      try { const range = document.createRange(); range.selectNodeContents(element); const r = range.getBoundingClientRect(); if (r.width > 15 && r.height > 15) return true; } catch (_) {}
    }
    return false;
  }

  function visibleAndActive(element) {
    if (!element || !element.isConnected) return false;
    if (element.disabled || (typeof element.getAttribute === 'function' && element.getAttribute('aria-disabled') === 'true')) return false;
    if (typeof element.getAttribute === 'function' && element.getAttribute('aria-hidden') === 'true') return false;
    const style = window.getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const opacity = parseFloat(style.opacity);
    if (Number.isFinite(opacity) && opacity <= 0) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 && rect.height <= 0) {
      if (style.display !== 'contents') return false;
      const childWithText = element.querySelector('p, div, span, .markdown, .model-response-text');
      if (childWithText) {
        const cStyle = window.getComputedStyle(childWithText);
        if (cStyle.visibility === 'hidden' || cStyle.display === 'none') return false;
        const cOpacity = parseFloat(cStyle.opacity);
        if (Number.isFinite(cOpacity) && cOpacity <= 0) return false;
        const cRect = childWithText.getBoundingClientRect();
        if (cRect.width > 0 || cRect.height > 0) return true;
      }
      return false;
    }
    return true;
  }

  function findComposer(input) { return input?.closest('chat-window, .input-area, .composer, .input-container, form, main') || input?.parentElement || document.body; }
  const SEND_SELECTORS = [
    'button[aria-label*="전송"]',
    'button[aria-label*="보내기"]',
    'button[aria-label*="Send" i]',
    'button[aria-label*="submit" i]',
    'button.send-button',
    '[data-test-id="send-button"]',
    'button[mat-icon-button][aria-label*="전송"]',
    'button[mat-icon-button][aria-label*="보내기"]',
    'button[mat-icon-button][aria-label*="Send" i]'
  ];

  function scoreSendCandidate(el, composer) {
    if (!el || !el.isConnected) return -1000;
    const tag = el.tagName.toLowerCase();
    if (tag !== 'button' && el.getAttribute('role') !== 'button') return -500;
    const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
    const className = String(el.className || '').toLowerCase();
    const testId = (el.getAttribute('data-test-id') || el.getAttribute('data-testid') || '').toLowerCase();
    if (/mic|voice|음성|마이크|audio|첨부|파일|attach|file|plus|추가|설정|help|도움말/.test(ariaLabel + ' ' + className + ' ' + testId)) return -10000;
    let score = 0;
    if (/send|전송|보내기|제출|submit|prompt/.test(ariaLabel)) score += 500;
    if (/send|arrow_upward|arrow-up|submit/.test(text)) score += 400;
    if (el.querySelector('mat-icon, [data-mat-icon-name*="send"], [data-mat-icon-name*="arrow"]')) score += 300;
    if (/send|submit|btn-send/.test(className + ' ' + testId)) score += 250;
    if (el.querySelector('.mat-mdc-button-touch-target')) score += 150;
    if (composer) {
      const cRect = composer.getBoundingClientRect(), bRect = el.getBoundingClientRect();
      if (bRect.right >= cRect.right - 120 && bRect.bottom >= cRect.bottom - 120) score += 200;
    }
    if (el.disabled) score -= 100;
    if (el.getAttribute('aria-disabled') === 'true') score -= 50;
    return score;
  }

  function findSendControl(input) {
    const target = input || editor();
    const composer = findComposer(target);
    if (!composer || typeof composer.querySelectorAll !== 'function') return null;
    const rawCandidates = composer.querySelectorAll('button, [role="button"], span.mat-mdc-button-touch-target, mat-icon');
    const buttonSet = new Set();
    for (const raw of rawCandidates) {
      if (raw.tagName && raw.tagName.toLowerCase() === 'button') buttonSet.add(raw);
      else {
        const parentBtn = raw.closest('button, [role="button"]');
        if (parentBtn && composer.contains(parentBtn)) buttonSet.add(parentBtn);
      }
    }
    const scored = [...buttonSet].filter(button => {
      if (!visibleAndActive(button)) return false;
      const identity = [button.getAttribute('aria-label'), button.getAttribute('data-test-id'), button.getAttribute('data-testid'), button.className, button.innerText || button.textContent].join(' ');
      return /send|submit|전송|보내기|제출|arrow_upward|arrow-up/i.test(identity);
    }).map(button => ({ button, score: scoreSendCandidate(button, composer) })).filter(x => x.score > 0).sort((a,b) => b.score - a.score);
    if (scored.length > 1 && scored[0].score === scored[1].score) return null;
    return scored.length ? { button: scored[0].button, totalCandidates: scored.length } : null;
  }

  function scoreEditorCandidate(el) {
    if (!el || !el.isConnected || !visible(el)) return -1000;
    if (el.getAttribute('aria-hidden') === 'true') return -500;
    if (el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true') return -1000;
    let score = 100;
    const ce = el.isContentEditable || el.getAttribute('contenteditable') === 'true';
    if (el.closest('rich-textarea')) score += 500;
    if (ce) score += 300;
    if (el.closest('chat-window, .input-area, .composer, .input-container, form, main')) score += 200;
    const rect = el.getBoundingClientRect(); if (rect.top > window.innerHeight * 0.4) score += 150;
    const sendCandidate = findSendControl(el); if (sendCandidate?.button && !sendCandidate.button.disabled) score += 250;
    return score;
  }

  function editor() {
    const candidates = [];
    for (const selector of EDITOR_SELECTORS) for (const el of document.querySelectorAll(selector)) {
      const score = scoreEditorCandidate(el); if (score > 0) candidates.push({ el, score });
    }
    candidates.sort((a,b) => b.score - a.score);
    return candidates.length ? candidates[0].el : null;
  }

  function getEditorSurfaces(target) {
    if (!target) return [];
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) return [{ surface: 'value', text: target.value || '' }];
    return [{ surface: 'innerText', text: target.innerText || '' }, { surface: 'textContent', text: target.textContent || '' }];
  }

  function composerEmpty(target) {
    return Boolean(target?.isConnected) && getEditorSurfaces(target).every(s => !canonicalPromptText(s.text));
  }

  function freshConversationState() {
    const ed = editor();
    const userInv = getUserInventory();
    const candInv = getCandidateInventory();
    return Boolean(ed) && composerEmpty(ed) && !activeExecution && !findActiveStopButton()
      && userInv.userUniqueTurns === 0
      && candInv.responseUniqueTurns === 0
      && document.readyState !== 'loading';
  }

  function pageStatus() {
    const body = (document.body?.innerText || '').slice(0, 5000);
    if (/captcha|로봇이 아닙니다|비정상적인 트래픽/i.test(body)) return 'captcha';
    if (/accounts\.google\.com/.test(location.href) || (/로그인/.test(body) && !editor())) return 'auth_required';
    if (activeExecution) {
      if (Date.now() > (activeExecution.overallDeadlineAtMs || activeExecution.deadlineAtMs)) cancelExecution(activeExecution.requestId, 'command_deadline_exceeded');
      else return 'busy';
    }
    return editor() ? 'ready' : 'dom_unsupported';
  }

  function clearEditor(target) {
    if (!target || !target.isConnected) return;
    target.focus();
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
      target.value = ''; target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true })); target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    } else {
      const selection = window.getSelection(), range = document.createRange(); range.selectNodeContents(target); selection.removeAllRanges(); selection.addRange(range);
      try { document.execCommand('delete', false, null); } catch (_) { target.innerHTML = ''; }
      target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true })); target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    }
  }

  function setEditorText(target, text) {
    clearEditor(target); target.focus();
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
      const prototype = target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (!setter) return false;
      setter.call(target, text);
      target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
      target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    } else {
      const selection = window.getSelection(), range = document.createRange(); range.selectNodeContents(target); selection.removeAllRanges(); selection.addRange(range);
      try { document.execCommand('insertText', false, text); } catch (_) {}
      const curText = (target.innerText || target.textContent || '').trim();
      if (!curText || curText.length < Math.min(10, text.trim().length)) {
        return false;
      }
      try { target.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'insertText', data: text })); } catch (_) {}
      try { target.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: text })); } catch (_) {}
      try { target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true })); } catch (_) {}
      try { target.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, cancelable: true, key: 'Unidentified' })); } catch (_) {}
    }
  }

  async function waitForStableReadback(getTargetFn, expectedText, maxWaitMs = 1500) {
    const deadlineAtMs = Date.now() + maxWaitMs;
    let matchStartTimeMs = null, lastMatchedSurface = null, lastActualRaw = '', lastActualCanonical = '', resolvedTarget = null;
    while (Date.now() < deadlineAtMs) {
      if (isStopped || activeExecution?.cancelled) return { ok: false, reason: 'cancelled' };
      let currentTarget = getTargetFn(); if (!currentTarget || !currentTarget.isConnected) currentTarget = editor();
      if (!currentTarget) { await new Promise(r => setTimeout(r,100)); continue; }
      resolvedTarget = currentTarget; let matchedThisTick = false;
      for (const s of getEditorSurfaces(currentTarget)) {
        const canonicalActual = strictNormalizePrompt(s.text); lastActualRaw = s.text; lastActualCanonical = canonicalActual;
        if (isExactPromptMatch(s.text, expectedText)) { matchedThisTick = true; lastMatchedSurface = s.surface; break; }
      }
      if (matchedThisTick) {
        if (!matchStartTimeMs) matchStartTimeMs = Date.now();
        else if (Date.now() - matchStartTimeMs >= 200) return { ok: true, target: resolvedTarget, surface: lastMatchedSurface, actualRaw: lastActualRaw, actualCanonical: lastActualCanonical };
      } else matchStartTimeMs = null;
      await new Promise(r => setTimeout(r,100));
    }
    return { ok: false, target: resolvedTarget, reason: 'readback_mismatch', surface: lastMatchedSurface || 'none', actualRaw: lastActualRaw, actualCanonical: lastActualCanonical };
  }

  function getUserTurnContainer(el) {
    if (!el || !el.isConnected || typeof el.closest !== 'function') return null;
    const primary = el.closest(PRIMARY_USER_TURN_ROOT_SELECTORS);
    if (primary) {
      let outermost = primary;
      let parent = primary.parentElement;
      while (parent && typeof parent.closest === 'function') {
        const higher = parent.closest(PRIMARY_USER_TURN_ROOT_SELECTORS);
        if (higher) {
          outermost = higher;
          parent = higher.parentElement;
        } else {
          break;
        }
      }
      return outermost;
    }
    const fallback = el.closest(FALLBACK_USER_TURN_ROOT_SELECTOR);
    if (fallback) {
      let outermost = fallback;
      let parent = fallback.parentElement;
      while (parent && typeof parent.closest === 'function') {
        const higher = parent.closest(FALLBACK_USER_TURN_ROOT_SELECTOR);
        if (higher) {
          outermost = higher;
          parent = higher.parentElement;
        } else {
          break;
        }
      }
      return outermost;
    }
    return null;
  }
  function getTurnContainer(el) { return !el || !el.isConnected ? null : (el.closest('model-response, div[data-message-author-role="model"], div.model-response, [data-test-id="model-response"]') || el); }

  const EXCLUDED_STATUS_SELECTORS = [
    'button','[role="button"]','[role="status"]','[role="progressbar"]','[role="status"][aria-live]','.thinking','.thought-container',
    '.thinking-container','.status-container','.status-indicator','header','.response-header','.model-response-header','.toolbar','.actions',
    '.response-actions','mat-progress-bar','[data-test-id*="status"]','[data-test-id*="thought"]','[data-test-id*="thinking"]',
    '[data-test-id*="header"]','.loading-dots','.streaming'
  ].join(', ');
  const CONTAMINATION_PATTERNS = [
    /Initiating the Analysis/i,/Gemini의\s*응답/i,/Gemini\s*response/i,/Thinking\.\.\./i,/Analyzing\.\.\./i,
    /생각\s*중/i,/분석\s*중/i,/Show\s*thinking/i,/Hide\s*thinking/i,/생각\s*과정/i,/View\s*other\s*drafts/i,/다른\s*답안\s*보기/i
  ];
  function isExcludedNode(el) {
    if (!el || typeof el.matches !== 'function') return false;
    try { if (el.matches(EXCLUDED_STATUS_SELECTORS)) return true; if (typeof el.closest === 'function' && el.closest(EXCLUDED_STATUS_SELECTORS)) return true; } catch (_) {}
    return false;
  }

  function extractCleanText(node) {
    if (!node || !node.isConnected) return '';
    if (node.nodeType === 1 && isExcludedNode(node)) return '';
    let text = '';
    if (typeof document !== 'undefined' && typeof document.createTreeWalker === 'function' && typeof NodeFilter !== 'undefined') {
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
        acceptNode(n) {
          if (n.nodeType === 1) { if (isExcludedNode(n)) return NodeFilter.FILTER_REJECT; return NodeFilter.FILTER_SKIP; }
          if (n.nodeType === 3) { if (n.parentElement && isExcludedNode(n.parentElement)) return NodeFilter.FILTER_REJECT; return NodeFilter.FILTER_ACCEPT; }
          return NodeFilter.FILTER_SKIP;
        }
      });
      let curr; while ((curr = walker.nextNode())) text += curr.textContent || '';
    } else {
      try {
        if (typeof node.cloneNode === 'function') {
          const clone = node.cloneNode(true); const excludedElements = clone.querySelectorAll ? clone.querySelectorAll(EXCLUDED_STATUS_SELECTORS) : [];
          for (const el of excludedElements) el.remove(); text = clone.innerText || clone.textContent || '';
        } else text = node.innerText || node.textContent || '';
      } catch (_) { text = node.innerText || node.textContent || ''; }
    }
    let remainder = text.trim(); if (!remainder) return '';
    let hadContamination = false;
    for (const pat of CONTAMINATION_PATTERNS) if (pat.test(remainder)) {
      hadContamination = true; const globalPat = new RegExp(pat.source, pat.flags.includes('g') ? pat.flags : pat.flags + 'g'); remainder = remainder.replace(globalPat, '');
    }
    if (hadContamination) {
      remainder = remainder.split('\n').map(line => line.trim()).filter(Boolean).join('\n').trim();
      for (const pat of CONTAMINATION_PATTERNS) if (pat.test(remainder)) return '';
      if (remainder.length < 10) return '';
    }
    return remainder;
  }

  function resolveTurnCandidate(turnNode) {
    if (!turnNode || !turnNode.isConnected) return null;
    const bodySelectors = ['message-content','div.markdown','div.model-response-text','.response-body-inner','.response-container-content'];
    const bodyCandidates = [];
    for (const sel of bodySelectors) {
      for (const el of turnNode.querySelectorAll(sel)) {
        if (!el || !el.isConnected || isExcludedNode(el)) continue;
        const st = window.getComputedStyle(el); if (st.visibility === 'hidden' || st.display === 'none') continue;
        const cleanText = extractCleanText(el);
        bodyCandidates.push({ el, cleanText, score: (cleanText ? cleanText.length : 0) });
      }
    }
    bodyCandidates.sort((a,b) => a.score - b.score);
    const bestBody = bodyCandidates.filter(x => x.cleanText).pop() || bodyCandidates.pop() || null;
    const answerBodyNode = bestBody?.el || null;
    const statusNodes = [...turnNode.querySelectorAll(EXCLUDED_STATUS_SELECTORS)];
    let streamingNode = null;
    for (const el of turnNode.querySelectorAll('.loading-dots, .streaming, [aria-busy="true"], mat-progress-bar, [data-is-generating="true"]')) {
      if (visibleAndActive(el)) { streamingNode = el; break; }
    }
    const targetTextNode = answerBodyNode || turnNode, cleanText = bestBody?.cleanText || extractCleanText(targetTextNode);
    let isCandidateVisible = false;
    const tStyle = window.getComputedStyle(turnNode);
    if (tStyle.visibility !== 'hidden' && tStyle.display !== 'none') {
      const rect = turnNode.getBoundingClientRect();
      if (rect.width > 5 && rect.height > 5) isCandidateVisible = true;
      else try { const range = document.createRange(); range.selectNodeContents(turnNode); const r = range.getBoundingClientRect(); if (r.width > 5 && r.height > 5) isCandidateVisible = true; } catch (_) {}
    }
    if (!isCandidateVisible && (streamingNode || answerBodyNode || cleanText.length > 0)) isCandidateVisible = true;
    return { turnNode, answerBodyNode, statusNodes, streamingNode, textNode: answerBodyNode || turnNode, text: cleanText, isVisible: isCandidateVisible };
  }

  function extractResponseText(node) { if (!node) return ''; const cand = resolveTurnCandidate(node); return cand ? (cand.text || '') : extractCleanText(node); }
  const USER_QUERY_BODY_SELECTORS = [
    '.query-text', '.user-query-text', '.user-query-content', '[data-test-id*="user-query"]', '.user-query-body'
  ].join(', ');

  function extractUserQueryText(node) {
    if (!node) return '';
    const textContainer = (typeof node.querySelector === 'function' ? node.querySelector(USER_QUERY_BODY_SELECTORS) : null) || node;
    const pElements = typeof textContainer.querySelectorAll === 'function' ? [...textContainer.querySelectorAll('p')] : [];
    if (pElements.length > 0) {
      return pElements.map(p => (p.innerText || p.textContent || '').trim()).filter(Boolean).join('\n');
    }
    return (textContainer.innerText || textContainer.textContent || '').trim();
  }

  function userTurnMatchesExpected(root, expectedStrict) {
    if (!root || !expectedStrict) return false;
    const textContainer = (typeof root.querySelector === 'function' ? root.querySelector(USER_QUERY_BODY_SELECTORS) : null) || root;
    const candidates = [];
    try {
      const pElements = typeof textContainer.querySelectorAll === 'function' ? [...textContainer.querySelectorAll('p')] : [];
      if (pElements.length > 0) {
        candidates.push(pElements.map(p => (p.innerText || p.textContent || '').trim()).filter(Boolean).join('\n'));
        candidates.push(pElements.map(p => (p.innerText || p.textContent || '').trim()).filter(Boolean).join(' '));
      }
      if (textContainer.innerText) candidates.push(textContainer.innerText);
      if (textContainer.textContent) candidates.push(textContainer.textContent);
      if (root !== textContainer) {
        if (root.innerText) candidates.push(root.innerText);
        if (root.textContent) candidates.push(root.textContent);
      }
    } catch (_) {}

    for (const text of candidates) {
      if (strictNormalizePrompt(text) === expectedStrict) return true;
    }
    return false;
  }

  function getCandidateInventory(initialResponseSet = new Set(), baselineResponseFingerprints = new Set(), currentUserTurn = null, options = {}) {
    const allMatches = [...document.querySelectorAll(RESPONSE_SELECTORS)], responseSelectorMatches = allMatches.length;
    const uniqueTurnNodes = [], seenTurnSet = new Set();
    for (const el of allMatches) { const t = getTurnContainer(el); if (t && !seenTurnSet.has(t)) { seenTurnSet.add(t); uniqueTurnNodes.push(t); } }
    const inventory = [];
    const excluded = {
      disconnected: 0,
      not_visible: 0,
      initial_baseline_node: 0,
      baseline_text_match: 0,
      precedes_user_turn: 0,
      relaxed_fresh_chat_precedes: 0
    };

    const isFreshChatEligible = Boolean(
      options.freshChatVerified &&
      (options.conversationEpoch === undefined || options.conversationEpoch === conversationEpoch) &&
      initialResponseSet.size === 0 &&
      baselineResponseFingerprints.size === 0 &&
      uniqueTurnNodes.length === 1
    );

    let turnIndex = 0;
    for (const turnNode of uniqueTurnNodes) {
      const cand = resolveTurnCandidate(turnNode);
      if (!cand) continue;
      cand.turnIndex = turnIndex++;
      cand.tag = turnNode.tagName || 'DIV';
      cand.textLen = (cand.text || '').length;

      const textCanonical = canonicalPromptText(cand.text);
      const isBaselineNode = initialResponseSet.has(turnNode);
      const baselineFingerprintMatch = Boolean(textCanonical && baselineResponseFingerprints.has(textCanonical));
      const isAfterUserTurn = currentUserTurn ? ((currentUserTurn.compareDocumentPosition(turnNode) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0) : true;

      cand.isBaselineNode = isBaselineNode;
      cand.baselineFingerprintMatch = baselineFingerprintMatch;
      cand.isAfterUserTurn = isAfterUserTurn;

      let excludeReason = null;
      if (!turnNode.isConnected) {
        excludeReason = 'disconnected';
        excluded.disconnected++;
      } else if (!cand.isVisible) {
        excludeReason = 'not_visible';
        excluded.not_visible++;
      } else if (isBaselineNode) {
        excludeReason = 'initial_baseline_node';
        excluded.initial_baseline_node++;
      } else if (baselineFingerprintMatch) {
        excludeReason = 'baseline_text_match';
        excluded.baseline_text_match++;
      } else if (currentUserTurn && !isAfterUserTurn) {
        const isStatusOnly = cand.textLen === 0 && !cand.answerBodyNode;
        if (isFreshChatEligible && cand.isVisible && !isStatusOnly) {
          cand.relaxedFreshChatPrecedes = true;
          excluded.relaxed_fresh_chat_precedes++;
          excludeReason = null;
        } else {
          excludeReason = 'precedes_user_turn';
          excluded.precedes_user_turn++;
        }
      }

      cand.excludeReason = excludeReason;
      cand.isCandidate = !excludeReason;
      inventory.push(cand);
    }
    const validCandidates = inventory.filter(c => c.isCandidate);
    return {
      responseSelectorMatches,
      responseUniqueTurns: uniqueTurnNodes.length,
      visibleTurnCandidates: validCandidates.length,
      visibleTextCandidates: validCandidates.filter(c => (c.text || '').trim().length > 0).length,
      inventory,
      validCandidates,
      excluded
    };
  }

  function canonicalizeRoots(roots) {
    if (!Array.isArray(roots) || roots.length <= 1) return roots || [];
    const unique = [...new Set(roots)];
    const nodeSet = new Set(unique);
    return unique.filter(node => {
      let curr = node.parentElement;
      while (curr) {
        if (nodeSet.has(curr)) return false;
        curr = curr.parentElement;
      }
      return true;
    });
  }

  function getUserInventory() {
    let roots = [...document.querySelectorAll(PRIMARY_USER_TURN_ROOT_SELECTORS)];
    if (!roots.length) {
      roots = [...document.querySelectorAll(FALLBACK_USER_TURN_ROOT_SELECTOR)];
    }
    roots = roots.filter(el => {
      if (!el || !el.isConnected) return false;
      if (typeof el.closest === 'function' && el.closest('template, [aria-hidden="true"], nav, mat-sidenav, .sidebar')) return false;
      return true;
    });
    const uniqueRoots = canonicalizeRoots(roots);
    return {
      userSelectorMatches: roots.length,
      userUniqueTurns: uniqueRoots.length,
      visibleUserNodes: uniqueRoots.filter(visible),
      allMatches: roots
    };
  }
  function responseNodes() { return getCandidateInventory().inventory.filter(c => c.isVisible).map(c => c.turnNode); }
  function userQueryNodes() { return getUserInventory().visibleUserNodes; }

  function findActiveStreamingIndicator(boundNode) {
    if (!boundNode || !boundNode.isConnected) return null;
    const indicators = boundNode.querySelectorAll('.loading-dots, .streaming, [aria-busy="true"], mat-progress-bar, [data-is-generating="true"]');
    for (const el of indicators) {
      if (visibleAndActive(el)) return el;
    }
    return null;
  }

  function findActiveStopButton() {
    const comp = findComposer(editor());
    if (comp && typeof comp.querySelectorAll === 'function') {
      const stopButtons = comp.querySelectorAll('button[aria-label*="중지"], button[aria-label*="Stop"], button[aria-label*="생성 중지"], [role="button"][aria-label*="중지"], [role="button"][aria-label*="Stop"], [role="button"][aria-label*="생성 중지"]');
      for (const btn of stopButtons) {
        const label = (btn.getAttribute?.('aria-label') || btn.innerText || btn.textContent || '').toLowerCase();
        if (/중지|stop/i.test(label) && visibleAndActive(btn)) return btn;
      }
    }
    return null;
  }

  const ACTION_TOOLBAR_SELECTORS = [
    '.actions', '.response-actions', '.model-response-actions',
    '[data-test-id*="action"]', '[data-test-id*="copy"]',
    'button[aria-label*="복사"]', 'button[aria-label*="Copy"]',
    'button[aria-label*="좋아요"]', 'button[aria-label*="Good response"]',
    'button[aria-label*="Bad response"]'
  ].join(', ');

  function findVisibleActionToolbar(boundNode) {
    if (!boundNode || !boundNode.isConnected) return null;
    const candidates = [
      ...boundNode.querySelectorAll(ACTION_TOOLBAR_SELECTORS),
      ...(boundNode.parentElement ? boundNode.parentElement.querySelectorAll(ACTION_TOOLBAR_SELECTORS) : [])
    ];
    for (const el of candidates) {
      if (visibleAndActive(el)) return el;
    }
    return null;
  }

  function inspectGenerationState(boundNode) {
    const activeStreamingIndicator = findActiveStreamingIndicator(boundNode);
    const activeStopButton = findActiveStopButton();
    const visibleActionToolbar = findVisibleActionToolbar(boundNode);
    return {
      activeStreamingIndicator,
      activeStopButton,
      visibleActionToolbar,
      hasActiveStreamingIndicator: Boolean(activeStreamingIndicator),
      hasActiveStopButton: Boolean(activeStopButton),
      hasVisibleActionToolbar: Boolean(visibleActionToolbar)
    };
  }

  function detectGenerationEvidence(boundNode) {
    if (!boundNode || !boundNode.isConnected) return 'none';
    if (findActiveStreamingIndicator(boundNode)) return 'local_streaming';
    if (findActiveStopButton()) return 'composer_stop_button';
    return 'idle';
  }
  async function waitForSendReady(input, timeoutMs = 2500) {
    const deadlineAtMs = Date.now() + timeoutMs;
    while (Date.now() < deadlineAtMs) {
      if (isStopped || activeExecution?.cancelled) return null;
      const ctrl = findSendControl(input);
      if (ctrl?.button && !ctrl.button.disabled && ctrl.button.getAttribute('aria-disabled') !== 'true') return ctrl;
      await new Promise(r => setTimeout(r, 150));
    }
    return null;
  }

  function triggerSendViaClick(btn) {
    if (!btn || !btn.isConnected || btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
    try {
      if (!visible(btn)) return false;
      if (typeof btn.focus === 'function') btn.focus();
      btn.click();
      return true;
    } catch (_) {
      return false;
    }
  }

  function triggerSubmission(target, btn) {
    return { clicked: triggerSendViaClick(btn), keyed: false };
  }

  function logSendDiag(diag) {
    try {
      const btn = diag.button;
      const payload = {
        rid: activeExecution?.requestId || null,
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
      console.log('[GEMINI][SEND_DIAG]', JSON.stringify(payload));
      try {
        chrome.runtime.sendMessage({
          type: 'NFA_EVENT',
          event: { type: 'SEND_DIAG', ...payload }
        });
      } catch (_) {}
    } catch (_) {}
  }

  function setupResponseObserver(execState, targetRoot, checkOutput) {
    let checkTimer = null;
    let cleanupDone = false;

    let observer = null;
    try {
      observer = new MutationObserver(() => checkOutput());
      if (targetRoot) {
        observer.observe(targetRoot, { childList: true, subtree: true, characterData: true });
      }
    } catch (_) {}

    const cleanupObserver = () => {
      if (cleanupDone) return;
      cleanupDone = true;

      try {
        observer?.disconnect();
      } catch (_) {}

      const timerToClear = checkTimer;
      checkTimer = null;

      if (timerToClear !== null) {
        clearInterval(timerToClear);
      }

      if (execState.observer === observer) {
        execState.observer = null;
      }

      if (execState.timer === timerToClear) {
        execState.timer = null;
      }
    };

    checkTimer = setInterval(checkOutput, 200);
    execState.observer = observer;
    execState.timer = checkTimer;

    return cleanupObserver;
  }

  function createConversationRouteGuard(initialPath) {
    let acceptedPath = initialPath;
    let pendingPath = null;
    let pendingSince = null;
    let allocationAllowed = initialPath === '/app' || initialPath === '/app/';
    return (path, userCorrelated, nowMs) => {
      if (pendingPath !== null && path !== pendingPath) return 'lost';
      if (path === acceptedPath) return 'ok';
      if (!allocationAllowed || !/^\/app\/[a-zA-Z0-9_-]+$/.test(path)) return 'lost';
      // Pin the first allocated route, even while its user turn is rendering.
      if (pendingPath === null) { pendingPath = path; pendingSince = nowMs; }
      if (nowMs - pendingSince >= 5000) return 'lost';
      if (!userCorrelated) return 'pending';
      acceptedPath = path;
      pendingPath = null;
      allocationAllowed = false;
      return 'ok';
    };
  }

  async function executeCore(command, execState) {
    const isExecutionCancelled = () => isStopped || execState.cancelled || cancelledRequestIds.has(command.requestId);
    if (isExecutionCancelled()) return { status:'failed', text:'', error:'cancelled' };
    const initialPath = location.pathname || '';
    const routeGuard = createConversationRouteGuard(initialPath);
    const executionEpoch = conversationEpoch;
    const executionInstanceId = INSTANCE_ID;
    const initialResponseList = responseNodes(), initialResponseSet = new Set(initialResponseList);
    const baselineResponseFingerprints = new Set(initialResponseList.map(n => canonicalPromptText(extractResponseText(n))).filter(Boolean));
    const initialUserQueries = userQueryNodes(), initialUserQuerySet = new Set(initialUserQueries);
    const baselineUserQueryFingerprints = new Set(initialUserQueries.map(q => canonicalPromptText(extractUserQueryText(q))).filter(Boolean));
    function emitEvent(type,payload={}) { if (isExecutionCancelled()) return; try { chrome.runtime.sendMessage({ type:'NFA_EVENT', event:{ type, rid:command.requestId, ...payload } }); } catch (_) {} }
    const initialUserInv = getUserInventory();
    const initialCandInv = getCandidateInventory();
    const freshChatVerified = initialCandInv.responseUniqueTurns === 0 && initialUserInv.userUniqueTurns === 0
      && composerEmpty(editor()) && !findActiveStopButton();
    if (!freshChatVerified) return {status:'failed',text:'',error:'fresh_chat_not_verified'};
    if (freshChatVerified) {
      console.log('[GEMINI][FRESH_CHAT_EXEC_READY]', JSON.stringify({ tab:command.tabId||null, instance:INSTANCE_ID, epoch:conversationEpoch }));
      emitEvent('FRESH_CHAT_EXEC_READY', { tab:command.tabId||null, instance:INSTANCE_ID, epoch:conversationEpoch });
    }
    let target = editor(); if (!target) return { status:'dom_unsupported', text:'', error:'Gemini 입력창을 찾지 못했습니다.' };
    let readbackResult = null;
    for (let attempt=1;attempt<=2;attempt++) {
      if (isStopped || execState.cancelled) return { status:'failed',text:'',error:'cancelled' };
      if (!target || !target.isConnected) target = editor(); if (!target) break;
      setEditorText(target,command.prompt); readbackResult = await waitForStableReadback(() => target,command.prompt,1500);
      if (readbackResult.ok) { if (readbackResult.target) target=readbackResult.target; break; }
      if (attempt===1) { await new Promise(r=>setTimeout(r,200)); target=editor(); }
    }
    const expectedCanonical=canonicalPromptText(command.prompt), actualCanonical=readbackResult?.actualCanonical||'', actualRaw=readbackResult?.actualRaw||'';
    const expectedStrict=strictNormalizePrompt(command.prompt);
    console.log('[GEMINI][PROMPT_READBACK_DIAG]', JSON.stringify({ expectedRawLen:(command.prompt||'').length,actualRawLen:actualRaw.length,expectedCanonicalLen:expectedCanonical.length,
      actualCanonicalLen:actualCanonical.length,expectedHash:simpleHash(expectedCanonical),actualHash:simpleHash(actualCanonical),firstMismatchIndex:findFirstMismatchIndex(actualCanonical,expectedCanonical),
      actualSurface:readbackResult?.surface||'unknown',zeroWidthCount:countOccurrences(actualRaw,/[\u200B-\u200D\u2060\uFEFF\uFE0E\uFE0F]/g),nbspCount:countOccurrences(actualRaw,/[\u00A0\u2007\u202F]/g),
      newlineCount:countOccurrences(actualRaw,/\n/g),editorConnected:Boolean(target?.isConnected),readbackOk:Boolean(readbackResult?.ok) }));
    if (!readbackResult?.ok) {
      return { status:'dom_unsupported',text:'',error:'prompt_exact_readback_failed' };
    }
    if (!target || !target.isConnected) target=editor();
    if (!getEditorSurfaces(target).some(s => isExactPromptMatch(s.text, command.prompt))) { logSendDiag({button:null,confirmed:false,boundNode:false}); return {status:'dom_unsupported',text:'',error:'prompt_editor_changed_before_send'}; }
    await new Promise(r=>setTimeout(r,300)); if (isStopped||execState.cancelled) return {status:'failed',text:'',error:'cancelled'};
    target = editor();
    if (!target || !target.isConnected) return {status:'failed',text:'',error:'send_not_ready'};
    if (!getEditorSurfaces(target).some(s => isExactPromptMatch(s.text, command.prompt))) return {status:'failed',text:'',error:'prompt_editor_changed_before_send'};

    const sendCtrl=await waitForSendReady(target,2500);
    target = editor();
    if (!target?.isConnected || !getEditorSurfaces(target).some(s => isExactPromptMatch(s.text, command.prompt))) {
      return {status:'failed',text:'',error:'prompt_editor_changed_before_send'};
    }
    const finalSendCtrl = findSendControl(target);
    const finalBtn = finalSendCtrl?.button;
    logSendDiag({button:finalBtn,totalCandidates:finalSendCtrl?.totalCandidates||sendCtrl?.totalCandidates||0,confirmed:false,boundNode:false});
    if (isExecutionCancelled()) return {status:'failed',text:'',error:'cancelled'};
    if (conversationEpoch !== executionEpoch || (location.pathname || '') !== initialPath) return {status:'failed',text:'',error:'send_state_lost'};
    if (Date.now() >= (execState.generationDeadlineAtMs || execState.deadlineAtMs)) return {status:'timeout',text:'',error:'generation_deadline_before_send'};
    if (!finalBtn || !finalBtn.isConnected || finalBtn.disabled || finalBtn.getAttribute('aria-disabled') === 'true') {
      return {status:'failed',text:'',error:'send_not_ready'};
    }
    const dispatchAttempted = true;
    const clickCount = 1;
    const clickAttemptedAtMs = Date.now();
    const submission = triggerSubmission(target, finalBtn);
    emitEvent('SEND_DISPATCHED', { ...submission, clickCount, dispatchAttempted, clickAttemptedAtMs });
    if (!submission.clicked) return {status:'failed',text:'',error:'send_dispatch_unconfirmed'};

    let currentUserTurn=null,targetResponseNode=null,boundAtMs=0,reResolveAttempted=false,textNonEmptyLogged=false,textStableLogged=false,staleStreamingLogged=false;
    function bindResponseNode(node,evidence) {
      if (!node) return null; if (targetResponseNode?.isConnected && targetResponseNode===node) return targetResponseNode;
      const inv=getCandidateInventory(initialResponseSet,baselineResponseFingerprints,currentUserTurn,{freshChatVerified,conversationEpoch}), cand=inv.inventory.find(c=>c.turnNode===node);
      if (!cand||!cand.isVisible||inv.visibleTurnCandidates===0) return null;
      targetResponseNode=node; boundAtMs=Date.now(); reResolveAttempted=false;
      const rInv=getCandidateInventory(initialResponseSet,baselineResponseFingerprints,currentUserTurn,{freshChatVerified,conversationEpoch});
      console.log('[GEMINI][RESPONSE_TURN_BOUND]',JSON.stringify({rid:command.requestId,evidence,responseUniqueTurns:rInv.responseUniqueTurns,visibleTurnCandidates:rInv.visibleTurnCandidates,visibleTextCandidates:rInv.visibleTextCandidates,hasUserTurnAnchor:Boolean(currentUserTurn),nodeTag:node.tagName}));
      emitEvent('RESPONSE_TURN_BOUND',{responseUniqueTurns:rInv.responseUniqueTurns,visibleTurnCandidates:rInv.visibleTurnCandidates,visibleTextCandidates:rInv.visibleTextCandidates,evidence}); return targetResponseNode;
    }
    function findNewUserQuery() {
      const currentQueries=getUserInventory().visibleUserNodes;
      const exact=currentQueries.find(q=>userTurnMatchesExpected(q, expectedStrict)&&!initialUserQuerySet.has(q)); if (exact) return exact;
      return null;
    }
    function findTurnResponseCandidate() {
      const valid=getCandidateInventory(initialResponseSet,baselineResponseFingerprints,currentUserTurn,{freshChatVerified,conversationEpoch}).validCandidates;
      if (freshChatVerified) return valid.length ? valid[valid.length-1].turnNode : null;
      if (!currentUserTurn?.isConnected) { const q=findNewUserQuery(); if(q){currentUserTurn=q;console.log('[GEMINI][USER_TURN_BOUND]',JSON.stringify({rid:command.requestId}));} }
      if (currentUserTurn?.isConnected) for (const cand of valid) if ((currentUserTurn.compareDocumentPosition(cand.turnNode)&Node.DOCUMENT_POSITION_FOLLOWING)!==0) return cand.turnNode;
      return valid.length ? valid[valid.length-1].turnNode : null;
    }
    let confirmed=false, stableUserSince=null;
    const checkDeadline=Math.min(Date.now()+12000,execState.generationDeadlineAtMs||execState.deadlineAtMs);
    while(Date.now()<checkDeadline){
      if(isExecutionCancelled())return{status:'failed',text:'',error:'cancelled'};
      if(conversationEpoch!==executionEpoch||INSTANCE_ID!==executionInstanceId)return{status:'failed',text:'',error:'send_state_lost'};
      const currentPath=(typeof location!=='undefined'&&location.pathname)?location.pathname:'';
      const routeState = routeGuard(currentPath, Boolean(findNewUserQuery()), Date.now());
      if(routeState==='lost')return{status:'failed',text:'',error:'send_state_lost'};
      if(routeState==='pending'){stableUserSince=null;await new Promise(r=>setTimeout(r,150));continue;}
      const newQuery=findNewUserQuery();
      if(newQuery){
        currentUserTurn=newQuery;
        if(stableUserSince===null)stableUserSince=Date.now();
        const c=findTurnResponseCandidate();
        const cleared=composerEmpty(editor()), generating=Boolean(findActiveStopButton());
        if(Date.now()-stableUserSince>=650&&(cleared||generating||c)){
          confirmed=true;
          emitEvent('USER_TURN_CONFIRMED',{userUniqueTurns:getUserInventory().userUniqueTurns,stableMs:Date.now()-stableUserSince,composerCleared:cleared});
          emitEvent('SEND_COMMITTED',{stableMs:Date.now()-stableUserSince,composerCleared:cleared,generationStarted:generating,uiEvidenceOnly:true});
          if(c)bindResponseNode(c,'persistent_user_correlated');
          break;
        }
      }else{stableUserSince=null;currentUserTurn=null;}
      await new Promise(r=>setTimeout(r,150));
    }
    logSendDiag({button:finalBtn,totalCandidates:finalSendCtrl?.totalCandidates||0,confirmed,boundNode:Boolean(targetResponseNode)});
    if(!confirmed){emitEvent('SEND_COMMIT_UNKNOWN');return{status:'failed',text:'',error:'send_commit_unknown'};}
    const generationDeadlineAtMs=execState.generationDeadlineAtMs||execState.deadlineAtMs;let lastMutationAtMs=Date.now(),previous='',lastDiagReportAtMs=0;
    function reportWaitDiag(reason='periodic'){
      if(isExecutionCancelled())return;const nowMs=Date.now();if(reason==='periodic'&&nowMs-lastDiagReportAtMs<4800)return;lastDiagReportAtMs=nowMs;
      const curText=targetResponseNode?extractResponseText(targetResponseNode):'',candDiag=getCandidateInventory(initialResponseSet,baselineResponseFingerprints,currentUserTurn,{freshChatVerified,conversationEpoch}),userDiag=getUserInventory();
      const diag={rid:command.requestId,elapsedMs:nowMs-execState.startedAtMs,freshChatVerified:Boolean(freshChatVerified),tabId:command.tabId||null,contentInstanceId:INSTANCE_ID,conversationEpoch,sendConfirmed:Boolean(confirmed),userSelectorMatches:userDiag.userSelectorMatches,userUniqueTurns:userDiag.userUniqueTurns,responseSelectorMatches:candDiag.responseSelectorMatches,responseUniqueTurns:candDiag.responseUniqueTurns,visibleTurnCandidates:candDiag.visibleTurnCandidates,visibleTextCandidates:candDiag.visibleTextCandidates,responseBound:Boolean(targetResponseNode),responseTextLength:curText.length,lastMutationAgeMs:targetResponseNode?(nowMs-lastMutationAtMs):0,generationEvidence:detectGenerationEvidence(targetResponseNode),candidateExcludeReasons:JSON.stringify(candDiag.excluded),runtimeBuild:runtimeContract.runtimeBuild};
      Object.assign(diag,{postKey:command.postKey,navigationVersion:command.navigationVersion,expectedEpoch:command.conversationEpoch,route:location.pathname,documentReadyState:document.readyState,userTurnConnected:Boolean(currentUserTurn?.isConnected),responseConnected:Boolean(targetResponseNode?.isConnected)});
      console.log('[GEMINI][WAIT_DIAG]',JSON.stringify(diag));try{chrome.runtime.sendMessage({type:'NFA_WAIT_DIAG',diag});}catch(_){}
    }
    return new Promise(resolve=>{
      let resolved=false;
      let lostEvidenceSince=null;
      let cleanupObserver=null;
      const finish=res=>{
        if(resolved)return;
        resolved=true;
        if(typeof cleanupObserver==='function')cleanupObserver();
        if(isExecutionCancelled())resolve({status:'failed',text:'',error:'cancelled'});
        else resolve(res);
      };
      execState.finish=finish;execState.resolve=resolve;
      function checkOutput(){
        if(isStopped)return finish({status:'failed',text:'',error:'runtime_stopped'});if(isExecutionCancelled())return finish({status:'failed',text:'',error:'cancelled'});const nowMs=Date.now();
        if(conversationEpoch!==executionEpoch||INSTANCE_ID!==executionInstanceId){return finish({status:'failed',text:'',error:'send_state_lost'});}
        const currentPath=(typeof location!=='undefined'&&location.pathname)?location.pathname:'';
        const routeState = routeGuard(currentPath, Boolean(findNewUserQuery()), nowMs);
        if(routeState==='lost')return finish({status:'failed',text:'',error:'send_state_lost'});
        if(nowMs>generationDeadlineAtMs){reportWaitDiag('timeout');const hasText=targetResponseNode&&extractResponseText(targetResponseNode).length>0;return finish({status:'timeout',text:'',error:hasText?'response_stalled':(targetResponseNode?'response_stream_no_text':'response_turn_not_found')});}
        if(routeState==='pending')return;
        reportWaitDiag('periodic');
        if(!targetResponseNode||!targetResponseNode.isConnected){const c=findTurnResponseCandidate();if(c)bindResponseNode(c,'observer_phase');}
        const liveUser=findNewUserQuery();
        if(liveUser)currentUserTurn=liveUser;
        const evidenceLost=!liveUser&&!targetResponseNode?.isConnected&&!findActiveStopButton();
        if(evidenceLost){
          if(lostEvidenceSince===null)lostEvidenceSince=nowMs;
          if(nowMs-lostEvidenceSince>=5000){
            confirmed=false;const composerHasPrompt=getEditorSurfaces(editor()).some(s=>isExactPromptMatch(s.text,command.prompt));
            emitEvent('SEND_STATE_LOST',{composerContainsPrompt:composerHasPrompt});
            const errCode=composerHasPrompt?'post_dispatch_uncommitted_suspected':'send_state_lost';
            return finish({status:'failed',text:'',error:errCode});
          }
        }else{lostEvidenceSince=null;}
        if(!targetResponseNode||!targetResponseNode.isConnected)return;const current=extractResponseText(targetResponseNode);
        if(!current){if(!boundAtMs)boundAtMs=Date.now();const zeroLenDuration=Date.now()-boundAtMs,localEvidence=detectGenerationEvidence(targetResponseNode),hasStreamingEvidence=localEvidence==='local_streaming'||localEvidence==='composer_stop_button';if(hasStreamingEvidence){if(zeroLenDuration>=15000)return finish({status:'failed',text:'',error:'response_stream_no_text'});}else if(zeroLenDuration>=3500){if(!reResolveAttempted){reResolveAttempted=true;targetResponseNode=null;boundAtMs=0;const c=findTurnResponseCandidate();if(c)bindResponseNode(c,'re_resolve_afterstalled_zero_len');else return finish({status:'failed',text:'',error:'response_text_target_not_found'});return;}return finish({status:'failed',text:'',error:'response_stream_no_text'});}return;}
        const currentCanonical=canonicalPromptText(current);if(baselineResponseFingerprints.has(currentCanonical)){targetResponseNode=null;return;}
        if(!textNonEmptyLogged){textNonEmptyLogged=true;console.log('[GEMINI][TEXT_NONEMPTY]',JSON.stringify({rid:command.requestId,chars:current.length}));emitEvent('TEXT_NONEMPTY',{chars:current.length});}
        if(current!==previous){previous=current;lastMutationAtMs=Date.now();return;}
        const mutationAge=Date.now()-lastMutationAtMs;
        const genState=inspectGenerationState(targetResponseNode);
        let localEvidence=genState.hasActiveStreamingIndicator?'local_streaming':(genState.hasActiveStopButton?'composer_stop_button':'idle');
        if(mutationAge>=8000&&localEvidence==='local_streaming'){
          if(!staleStreamingLogged){
            staleStreamingLogged=true;
            console.log('[GEMINI][STREAMING_STALE_SUSPECTED]',JSON.stringify({rid:command.requestId,mutationAge,chars:current.length}));
            try{chrome.runtime.sendMessage({type:'NFA_EVENT',event:{type:'STREAMING_STALE_SUSPECTED',rid:command.requestId,stableMs:mutationAge,chars:current.length}});}catch(_){}
          }
          if(!genState.hasActiveStopButton&&(!genState.hasActiveStreamingIndicator||genState.hasVisibleActionToolbar)){
            localEvidence='idle';
            console.log('[GEMINI][STREAMING_STALE_CLEARED]',JSON.stringify({
              rid:command.requestId,
              mutationAge,
              chars:current.length,
              reason:!genState.hasActiveStreamingIndicator?'indicators_inactive':'action_toolbar_present_without_stop_button'
            }));
          } else if(genState.hasActiveStopButton){
            console.log('[GEMINI][STREAMING_STALE_SUPPRESSED_BY_STOP_BUTTON]',JSON.stringify({rid:command.requestId,mutationAge,chars:current.length}));
          }
        }
        const isGenerating=localEvidence==='local_streaming'||localEvidence==='composer_stop_button'||genState.hasActiveStopButton;
        if(mutationAge>=1800&&!isGenerating){
          if(!textStableLogged){
            textStableLogged=true;
            console.log('[GEMINI][TEXT_STABLE]',JSON.stringify({rid:command.requestId,chars:current.length,stableMs:mutationAge}));
            emitEvent('TEXT_STABLE',{chars:current.length,stableMs:mutationAge});
          }
          return finish({status:'completed',text:current,error:''});
        }
      }
      const targetRoot=document.querySelector('chat-history, main, body')||document.body;
      cleanupObserver=setupResponseObserver(execState,targetRoot,checkOutput);
      execState.cleanupObserver=cleanupObserver;
    });
  }

  async function execute(command){
    if(isStopped)return{status:'failed',text:'',error:'runtime_stopped'};
    const currentReqId=command?.requestId||Math.random().toString(36).slice(2,10);
    if(cancelledRequestIds.has(currentReqId))return{status:'failed',text:'',error:'cancelled'};
    if(command?.contentInstanceId&&command.contentInstanceId!==INSTANCE_ID){
      console.warn('[GEMINI][EXEC_REJECTED]',JSON.stringify({rid:currentReqId,reason:'content_instance_mismatch',expected:INSTANCE_ID,actual:command.contentInstanceId}));
      return{status:'failed',text:'',error:'content_instance_mismatch'};
    }
    if(Number.isInteger(command?.conversationEpoch)&&command.conversationEpoch!==conversationEpoch){
      console.warn('[GEMINI][EXEC_REJECTED]',JSON.stringify({rid:currentReqId,reason:'conversation_epoch_mismatch',expected:conversationEpoch,actual:command.conversationEpoch}));
      return{status:'failed',text:'',error:'conversation_epoch_mismatch'};
    }
    if(activeExecution){
      if(Date.now()>(activeExecution.overallDeadlineAtMs||activeExecution.deadlineAtMs))cancelExecution(activeExecution.requestId,'command_deadline_exceeded');
      else return{status:'busy',text:'',error:'runtime_busy'};
    }
    const startedAtMs=Date.now();
    let generationDeadlineAtMs=0;
    if(typeof command?.generationDeadlineAtMs==='number'&&command.generationDeadlineAtMs>0) generationDeadlineAtMs=command.generationDeadlineAtMs;
    else if(typeof command?.generation_deadline_at_ms==='number'&&command.generation_deadline_at_ms>0) generationDeadlineAtMs=command.generation_deadline_at_ms;
    else if(typeof command?.generation_deadline_at==='number'&&command.generation_deadline_at>0) generationDeadlineAtMs=command.generation_deadline_at<1e11?Math.round(command.generation_deadline_at*1000):Math.round(command.generation_deadline_at);
    else if(typeof command?.timeout_seconds==='number'&&command.timeout_seconds>0) generationDeadlineAtMs=startedAtMs+Math.round(command.timeout_seconds*1000);
    else if(typeof command?.deadlineAtMs==='number'&&command.deadlineAtMs>0) generationDeadlineAtMs=command.deadlineAtMs;
    else if(typeof command?.deadlineAt==='number'&&command.deadlineAt>0) generationDeadlineAtMs=command.deadlineAt<1e11?Math.round(command.deadlineAt*1000):Math.round(command.deadlineAt);
    else generationDeadlineAtMs=startedAtMs+55000;

    let overallDeadlineAtMs=0;
    if(typeof command?.overallDeadlineAtMs==='number'&&command.overallDeadlineAtMs>0) overallDeadlineAtMs=command.overallDeadlineAtMs;
    else if(typeof command?.acceptance_deadline_at_ms==='number'&&command.acceptance_deadline_at_ms>0) overallDeadlineAtMs=command.acceptance_deadline_at_ms;
    else if(typeof command?.acceptance_deadline_at==='number'&&command.acceptance_deadline_at>0) overallDeadlineAtMs=command.acceptance_deadline_at<1e11?Math.round(command.acceptance_deadline_at*1000):Math.round(command.acceptance_deadline_at);
    else overallDeadlineAtMs=generationDeadlineAtMs+RESULT_DELIVERY_BUDGET_MS+1500;

    const execState={
      requestId:currentReqId,
      startedAtMs,
      generationDeadlineAtMs,
      overallDeadlineAtMs,
      deadlineAtMs:generationDeadlineAtMs,
      cancelled:false,
      observer:null,
      timer:null,
      cleanupObserver:null,
      finish:null,
      resolve:null
    };
    activeExecution=execState;
    try{
      return await executeCore(command,execState);
    }finally{
      if(activeExecution?.requestId===currentReqId){
        if(typeof activeExecution.cleanupObserver==='function'){
          try{activeExecution.cleanupObserver();}catch(_){}
        }
        try{activeExecution.observer?.disconnect();}catch(_){}
        if(activeExecution.timer){
          clearInterval(activeExecution.timer);
          activeExecution.timer=null;
        }
        activeExecution=null;
      }
    }
  }

  const RESULT_ACK_TIMEOUT_MS=1400,RESULT_DIRECT_TIMEOUT_MS=2500,RESULT_DELIVERY_BUDGET_MS=7500;
  function authoritativeDeliveryRejection(reason){return ['request_cancelled','missing_request_id','missing_post_key','invalid_navigation_version','request_id_mismatch','post_key_mismatch','navigation_version_mismatch','duplicate_result_conflict','late_result','active_command_identity_invalid'].includes(String(reason||''));}
  function sendRuntimeMessageWithTimeout(payload,timeoutMs){
    return new Promise(resolve=>{let settled=false;const done=v=>{if(settled)return;settled=true;clearTimeout(timer);resolve(v);};const timer=setTimeout(()=>done({ok:false,received:false,accepted:false,reason:'ack_timeout'}),Math.max(100,timeoutMs));
      try{chrome.runtime.sendMessage(payload,ack=>{const err=chrome.runtime.lastError;if(err)return done({ok:false,received:false,accepted:false,reason:err.message||'runtime_error'});done(ack||{ok:false,received:false,accepted:false,reason:'no_ack'});});}catch(e){done({ok:false,received:false,accepted:false,reason:String(e?.message||e)});}
    });
  }
  async function directResultPost(body,timeoutMs){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeoutMs);try{const res=await fetch('http://127.0.0.1:43127/v1/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});let data={};try{data=await res.json();}catch(_){}const accepted=Boolean(res.ok&&(data.accepted===true||data.reason==='already_accepted'));return{ok:accepted,received:res.ok,accepted,reason:data.reason||(accepted?'accepted':`http_${res.status}`),httpStatus:res.status};}catch(e){return{ok:false,received:false,accepted:false,reason:e?.name==='AbortError'?'direct_timeout':String(e?.message||e)};}finally{clearTimeout(timer);}}

  async function deliverExecutionResult(command,result){
    const rid=String(command?.requestId||'').trim(),postKey=String(command?.postKey||'').trim(),navigationVersion=Number(command?.navigationVersion||0);
    const status=result?.status||'failed',text=result?.text||'',error=result?.error||'';
    const deliveryId=`${rid}:${status}:${text.length}:${simpleHash(text)}`;
    console.log('[GEMINI][DELIVER_RESULT_START]',JSON.stringify({rid,status,textLen:text.length,deliveryId}));
    if(!rid||!postKey||!Number.isInteger(navigationVersion)||navigationVersion<=0){const reason='invalid_result_identity';console.warn('[GEMINI][DELIVER_RESULT_REJECTED]',JSON.stringify({rid,reason}));return{accepted:false,received:false,reason};}
    if(cancelledRequestIds.has(rid))return{accepted:false,received:false,reason:'request_cancelled'};

    const payload={type:'NFA_EXECUTION_RESULT',requestId:rid,postKey,navigationVersion,deliveryId,result:{status,text,error,deliveryId}};
    const directBody={requestId:rid,postKey,navigationVersion,deliveryId,status,text,error};

    let overallDeadlineMs=command?.overallDeadlineAtMs||command?.acceptance_deadline_at_ms;
    if(!overallDeadlineMs&&command?.acceptance_deadline_at){
      overallDeadlineMs=command.acceptance_deadline_at<1e11?Math.round(command.acceptance_deadline_at*1000):Math.round(command.acceptance_deadline_at);
    }
    const deliveryDeadline=overallDeadlineMs?Math.min(Date.now()+RESULT_DELIVERY_BUDGET_MS,overallDeadlineMs-500):(Date.now()+RESULT_DELIVERY_BUDGET_MS);

    let lastReason='delivery_not_accepted';
    for(let attempt=1;attempt<=3&&Date.now()<deliveryDeadline;attempt++){
      const remaining=Math.max(100,deliveryDeadline-Date.now());
      const resp=await sendRuntimeMessageWithTimeout(payload,Math.min(RESULT_ACK_TIMEOUT_MS,remaining));
      lastReason=resp?.reason||resp?.error||lastReason;
      if(resp?.accepted===true||resp?.reason==='already_accepted'){
        console.log('[GEMINI][DELIVER_RESULT_ACCEPTED]',JSON.stringify({rid,channel:'background',attempt,reason:resp.reason||'accepted'}));
        return{accepted:true,received:true,channel:'background',reason:resp.reason||'accepted'};
      }
      if(resp?.received||resp?.forwarded){
        console.log('[GEMINI][DELIVER_RESULT_BACKGROUND_RECEIVED]',JSON.stringify({rid,attempt,reason:lastReason}));
      }
      if(authoritativeDeliveryRejection(lastReason)){
        console.warn('[GEMINI][DELIVER_RESULT_REJECTED]',JSON.stringify({rid,channel:'background',reason:lastReason}));
        return{accepted:false,received:Boolean(resp?.received),channel:'background',reason:lastReason};
      }
      if(attempt<3)await new Promise(r=>setTimeout(r,200));
    }
    const remaining=deliveryDeadline-Date.now();
    if(remaining>100){
      console.log('[GEMINI][DELIVER_RESULT_FAILOVER_DIRECT]',JSON.stringify({rid,remainingMs:remaining}));
      const direct=await directResultPost(directBody,Math.min(RESULT_DIRECT_TIMEOUT_MS,remaining));
      lastReason=direct.reason||lastReason;
      if(direct.accepted){
        console.log('[GEMINI][DELIVER_RESULT_ACCEPTED]',JSON.stringify({rid,channel:'direct',reason:direct.reason}));
        return{...direct,channel:'direct'};
      }
      console.warn('[GEMINI][DELIVER_RESULT_DIRECT_REJECTED]',JSON.stringify({rid,reason:lastReason,httpStatus:direct.httpStatus||0}));
      return{...direct,channel:'direct'};
    }
    console.warn('[GEMINI][DELIVER_RESULT_UNCONFIRMED]',JSON.stringify({rid,reason:lastReason}));
    return{accepted:false,received:false,reason:lastReason};
  }

  const messageListener=(message,_sender,sendResponse)=>{
    if(isStopped)return false;
    if(message.type==='NFA_RUNTIME_PING'){sendResponse({ok:true,alive:true,build:runtimeContract.runtimeBuild,version:runtimeContract.extensionVersion,instanceId:INSTANCE_ID,status:pageStatus(),url:location.href,title:document.title,busyRequestId:activeExecution?.requestId||null,busySince:activeExecution?.startedAtMs||null,busyDeadlineAt:activeExecution?.overallDeadlineAtMs||activeExecution?.deadlineAtMs||null,busySinceMs:activeExecution?.startedAtMs||null,busyDeadlineAtMs:activeExecution?.overallDeadlineAtMs||activeExecution?.deadlineAtMs||null});return true;}
    if(message.type==='NFA_CANCEL_COMMAND'){const cancelled=cancelExecution(message.requestId,'cancelled_by_bridge');sendResponse({ok:true,cancelled,status:pageStatus()});return true;}
    if(message.type==='NFA_CHECK_FRESH_CHAT'){const userDiag=getUserInventory(),candDiag=getCandidateInventory(),ed=editor(),isFresh=freshConversationState();sendResponse({ok:true,fresh:isFresh,contentInstanceId:INSTANCE_ID,conversationEpoch,userQueryCount:userDiag.visibleUserNodes.length,userSelectorMatches:userDiag.userSelectorMatches,userUniqueTurns:userDiag.userUniqueTurns,selector_matches:candDiag.responseSelectorMatches,visible_candidates:candDiag.visibleTurnCandidates,responseSelectorMatches:candDiag.responseSelectorMatches,responseUniqueTurns:candDiag.responseUniqueTurns,visibleTurnCandidates:candDiag.visibleTurnCandidates,visibleTextCandidates:candDiag.visibleTextCandidates,bound_response:Boolean(activeExecution&&activeExecution.targetResponseNode),responseCount:candDiag.visibleTurnCandidates,composerAvailable:Boolean(ed),composerEmpty:composerEmpty(ed)});return true;}
    if(message.type==='NFA_RESET_FRESH_CHAT'){
      if(activeExecution){sendResponse({ok:false,error:'runtime_busy'});return true;}
      const sels=['a[href="/app"]','button[aria-label*="새 대화"]','button[aria-label*="New chat"]','[data-test-id="new-chat-button"]','.new-chat-button'];
      let clicked=false;
      for(const sel of sels){const btn=document.querySelector(sel);if(btn&&visible(btn)&&!btn.disabled&&btn.getAttribute('aria-disabled')!=='true'){btn.click();clicked=true;conversationEpoch++;break;}}
      sendResponse({ok:clicked,clicked,conversationEpoch,contentInstanceId:INSTANCE_ID});return true;
    }
    if(message.type==='NFA_EXECUTE_COMMAND'){sendResponse({ok:true,started:true,requestId:message.command?.requestId,protocol:'two-message-v3'});execute(message.command).then(result=>deliverExecutionResult(message.command,result)).catch(err=>deliverExecutionResult(message.command,{status:'failed',text:'',error:String(err?.message||err)}));return false;}
    return false;
  };
  chrome.runtime.onMessage.addListener(messageListener);eventCleanups.push(()=>{try{chrome.runtime.onMessage.removeListener(messageListener);}catch(_){}});
  globalThis.__NFA_GEMINI_RUNTIME__={build:runtimeContract.runtimeBuild,instanceId:INSTANCE_ID,getConversationEpoch:()=>conversationEpoch,stop:stopRuntime,cancel:cancelExecution,ping:()=>({alive:!isStopped,build:runtimeContract.runtimeBuild,instanceId:INSTANCE_ID,busyRequestId:activeExecution?.requestId||null}),resolveTurnCandidate,extractCleanText,extractResponseText,deliverExecutionResult,execute,executeCore,getActiveExecution:()=>activeExecution,setupResponseObserver,visible,visibleAndActive,getUserInventory,getUserTurnContainer,getCandidateInventory,detectGenerationEvidence,inspectGenerationState,findActiveStreamingIndicator,findActiveStopButton,findVisibleActionToolbar,canonicalizeRoots,PRIMARY_USER_TURN_ROOT_SELECTORS,FALLBACK_USER_TURN_ROOT_SELECTOR,canonicalPromptText,strictNormalizePrompt,isPromptMatch,isExactPromptMatch,isStrictPromptMatch,userTurnMatchesExpected,scoreSendCandidate,findSendControl,triggerSendViaClick,triggerSubmission,setEditorText,waitForStableReadback,freshConversationState,composerEmpty};
  globalThis.__NFA_GEMINI_RUNTIME__.createConversationRouteGuard = createConversationRouteGuard;
})();
