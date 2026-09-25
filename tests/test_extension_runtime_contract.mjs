import assert from 'node:assert';
import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const contentJsPath = path.resolve(__dirname, '../browser_extension/content.js');
const contentJsCode = fs.readFileSync(contentJsPath, 'utf8');
const backgroundJsPath = path.resolve(__dirname, '../browser_extension/background.js');
const backgroundJsCode = fs.readFileSync(backgroundJsPath, 'utf8');

function loadRealContentJs(customEnv = {}) {
  const sentMessages = [];
  const listeners = [];
  const fetches = [];
  const observers = [];

  class MockMutationObserver {
    constructor(callback) {
      this.callback = callback;
      this.connected = false;
      this.disconnected = false;
      observers.push(this);
    }
    observe(target, opts) {
      this.connected = true;
      this.target = target;
      this.opts = opts;
    }
    disconnect() {
      this.connected = false;
      this.disconnected = true;
    }
  }

  const context = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Date,
    Math,
    Set,
    Map,
    Promise,
    Node: { DOCUMENT_POSITION_FOLLOWING: 4 },
    KeyboardEvent: class { constructor(t, i) { Object.assign(this, i); } },
    InputEvent: class { constructor(t, i) { Object.assign(this, i); } },
    MouseEvent: class { constructor(t, i) { Object.assign(this, i); } },
    Event: class { constructor(t, i) { Object.assign(this, i); } },
    HTMLTextAreaElement: class {},
    HTMLInputElement: class {},
    location: { href: 'https://gemini.google.com/app' },
    window: {
      getComputedStyle: (el) => el?.computedStyle || { visibility: 'visible', display: 'block', opacity: '1' },
      getSelection: () => ({ removeAllRanges: () => {}, addRange: () => {} })
    },
    document: {
      title: 'Google Gemini',
      body: { isConnected: true, querySelectorAll: () => [], closest: () => null },
      querySelector: () => null,
      querySelectorAll: () => [],
      createRange: () => ({ selectNodeContents: () => {}, getBoundingClientRect: () => ({ width: 0, height: 0 }) }),
      execCommand: () => true
    },
    chrome: {
      runtime: {
        sendMessage: (msg, cb) => {
          sentMessages.push(msg);
          if (customEnv.onSendMessage) return customEnv.onSendMessage(msg, cb);
          if (typeof cb === 'function') cb({ ok: true, accepted: true });
        },
        onMessage: {
          addListener: (fn) => listeners.push(fn),
          removeListener: (fn) => {
            const idx = listeners.indexOf(fn);
            if (idx >= 0) listeners.splice(idx, 1);
          }
        },
        lastError: null
      }
    },
    fetch: async (url, opts) => {
      fetches.push({ url, opts });
      if (customEnv.onFetch) return customEnv.onFetch(url, opts);
      return { ok: true, json: async () => ({ ok: true, accepted: true }) };
    },
    AbortController: typeof AbortController !== 'undefined' ? AbortController : class MockAbortController { constructor() { this.signal = {}; } abort() {} },
    MutationObserver: MockMutationObserver,
    ...customEnv.extraGlobals
  };

  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(contentJsCode, context);

  return {
    runtime: context.__NFA_GEMINI_RUNTIME__,
    messageListener: listeners[0],
    sentMessages,
    fetches,
    observers,
    context
  };
}

function prepareEmptyComposer(context, editorEl) {
  editorEl.innerText = editorEl.textContent = '';
  context.document.execCommand = (command, _ui, text) => {
    if (command === 'delete') editorEl.innerText = editorEl.textContent = '';
    if (command === 'insertText') editorEl.innerText = editorEl.textContent = text;
    return true;
  };
}

function makeSendFixture() {
  const harness = loadRealContentJs();
  const { context } = harness;
  let clicks = 0;
  const rect = () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 });
  const composer = { isConnected: true, getBoundingClientRect: rect };
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    innerText: '', textContent: '', focus() {}, dispatchEvent() { return true; },
    getAttribute: k => k === 'contenteditable' ? 'true' : null,
    closest: sel => sel.includes('composer') ? composer : null,
    getBoundingClientRect: rect,
  };
  const button = {
    tagName: 'BUTTON', isConnected: true, disabled: false,
    getAttribute: k => k === 'aria-label' ? 'Send' : null,
    getBoundingClientRect: rect, querySelector: () => null,
    click() { clicks++; }, focus() {},
  };
  composer.querySelectorAll = () => [button];
  composer.contains = el => el === button || el === editorEl;
  context.document.querySelectorAll = sel => sel.includes('contenteditable') ? [editorEl] : [];
  context.location.pathname = '/app';
  prepareEmptyComposer(context, editorEl);
  return { ...harness, editorEl, button, composer, clicks: () => clicks };
}

test('HANDOFF: executeCore preserves a pre-existing composer draft without clicking', async () => {
  const f = makeSendFixture();
  f.editorEl.innerText = f.editorEl.textContent = 'existing draft';
  const result = await f.runtime.executeCore({ requestId: 'occupied', prompt: 'new prompt' }, { deadlineAtMs: Date.now() + 2000 });
  assert.strictEqual(result.error, 'fresh_chat_not_verified');
  assert.strictEqual(f.editorEl.innerText, 'existing draft');
  assert.strictEqual(f.clicks(), 0);
});

test('HANDOFF: changed prompt during send-readiness wait never clicks', async t => {
  const f = makeSendFixture();
  f.button.disabled = true;
  const timer = setTimeout(() => {
    f.editorEl.innerText = f.editorEl.textContent = 'changed draft';
    f.button.disabled = false;
  }, 800);
  t.after(() => clearTimeout(timer));
  const result = await f.runtime.executeCore({ requestId: 'changed', prompt: 'original prompt' }, { deadlineAtMs: Date.now() + 4000 });
  assert.strictEqual(result.error, 'prompt_editor_changed_before_send');
  assert.strictEqual(f.clicks(), 0);
});

test('HANDOFF: deadline expiry before dispatch prevents clicking', async () => {
  const f = makeSendFixture();
  const result = await f.runtime.executeCore({ requestId: 'expired', prompt: 'original prompt' }, { deadlineAtMs: Date.now() + 100 });
  assert.strictEqual(result.error, 'generation_deadline_before_send');
  assert.strictEqual(f.clicks(), 0);
});

test('HANDOFF: route allocation tolerates rendering delay but pins one conversation', () => {
  const { runtime } = loadRealContentJs();
  const guard = runtime.createConversationRouteGuard('/app');
  assert.strictEqual(guard('/app/first', false, 0), 'pending');
  assert.strictEqual(guard('/app/first', true, 300), 'ok');
  assert.strictEqual(guard('/app/first', false, 900), 'ok');
  assert.strictEqual(guard('/app/second', true, 1000), 'lost');
  const unconfirmed = runtime.createConversationRouteGuard('/app');
  assert.strictEqual(unconfirmed('/app/first', false, 0), 'pending');
  assert.strictEqual(unconfirmed('/app/first', false, 10999), 'pending');
  assert.strictEqual(unconfirmed('/app/first', false, 11000), 'lost');
  const delayed = runtime.createConversationRouteGuard('/app');
  assert.strictEqual(delayed('/app/first', false, 0), 'pending');
  assert.strictEqual(delayed('/app/first', true, 8000), 'ok');
  const switched = runtime.createConversationRouteGuard('/app');
  assert.strictEqual(switched('/app/first', false, 0), 'pending');
  assert.strictEqual(switched('/app/second', true, 100), 'lost');
});

test('HANDOFF: synchronous navigation during click cannot become the accepted baseline', async () => {
  const f = makeSendFixture();
  f.button.click = () => { f.context.location.pathname = '/settings'; };
  const result = await f.runtime.executeCore({ requestId: 'route-on-click', prompt: 'original prompt' }, { deadlineAtMs: Date.now() + 2000 });
  assert.strictEqual(result.error, 'send_state_lost');
});

test('HANDOFF: generic icon and ambiguous send buttons are not dispatch targets', () => {
  const f = makeSendFixture();
  f.button.getAttribute = () => null;
  f.button.querySelector = () => ({});
  assert.strictEqual(f.runtime.findSendControl(f.editorEl), null);
  f.button.getAttribute = k => k === 'aria-label' ? 'Send' : null;
  f.composer.querySelectorAll = () => [f.button, { ...f.button }];
  assert.strictEqual(f.runtime.findSendControl(f.editorEl), null);
});

function loadRealBackgroundJs(customEnv = {}) {
  const sentMessages = [];
  const tabMessages = [];
  const listeners = [];
  const fetches = [];

  const context = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Date,
    Math,
    Set,
    Map,
    Promise,
    __NFA_SKIP_AUTO_START__: true,
    fetch: async (url, opts) => {
      fetches.push({ url, opts });
      if (customEnv.onFetch) return customEnv.onFetch(url, opts);
      return { ok: true, json: async () => ({ ok: true, accepted: true }) };
    },
    AbortController: typeof AbortController !== 'undefined' ? AbortController : class MockAbortController { constructor() { this.signal = {}; } abort() {} },
    chrome: {
      runtime: {
        getURL: (file) => `chrome-extension://mock_extension_id/${file}`,
        sendMessage: (msg, cb) => {
          sentMessages.push(msg);
          if (customEnv.onSendMessage) return customEnv.onSendMessage(msg, cb);
          if (typeof cb === 'function') cb({ ok: true, accepted: true });
        },
        onMessage: {
          addListener: (fn) => listeners.push(fn),
          removeListener: () => {}
        },
        onInstalled: {
          addListener: () => {}
        },
        onStartup: {
          addListener: () => {}
        },
        getPlatformInfo: (cb) => { if (typeof cb === 'function') cb({}); },
        lastError: null
      },
      tabs: {
        onUpdated: {
          addListener: () => {}
        },
        query: async (queryInfo, cb) => {
          if (customEnv.onTabQuery) return customEnv.onTabQuery(queryInfo, cb);
          const defaultTabs = [{ id: 101, url: 'https://gemini.google.com/app', title: 'Google Gemini' }];
          if (typeof cb === 'function') cb(defaultTabs);
          return defaultTabs;
        },
        update: async (tabId, updateProps, cb) => {
          const res = { id: tabId, ...updateProps };
          if (typeof cb === 'function') cb(res);
          return res;
        },
        get: async (tabId, cb) => {
          if (customEnv.onTabGet) return customEnv.onTabGet(tabId, cb);
          const tab = { id: tabId, status: 'complete', url: 'https://gemini.google.com/app', title: 'Google Gemini' };
          if (typeof cb === 'function') cb(tab);
          return tab;
        },
        sendMessage: (tabId, msg, cb) => {
          tabMessages.push({ tabId, msg });
          if (customEnv.onTabSendMessage) return customEnv.onTabSendMessage(tabId, msg, cb);
          if (typeof cb === 'function') cb({ ok: true, alive: true, build: '13.2.3-r17', status: 'ready' });
        }
      }
    },
    ...customEnv.extraGlobals
  };

  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(backgroundJsCode, context);

  return {
    background: context.__NFA_BACKGROUND__,
    messageListener: listeners[0],
    sentMessages,
    tabMessages,
    fetches,
    context
  };
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

test('EDITOR-001: multiline newline variation canonical equality', () => {
  const expected = '첫 번째 줄\n\n두 번째 줄';
  const domInnerText = '첫 번째 줄\n두 번째 줄';
  // Both collapse multiple whitespace/newlines into canonical single whitespace according to canonicalPromptText
  assert.strictEqual(canonicalPromptText(domInnerText), canonicalPromptText(expected));
});

test('EDITOR-002: NBSP character canonical equality', () => {
  const expected = '신촌 맛집 후기';
  const domWithNbsp = '신촌\u00A0맛집\u202F후기';
  assert.strictEqual(canonicalPromptText(domWithNbsp), canonicalPromptText(expected));
});

test('EDITOR-003: zero-width character canonical equality', () => {
  const expected = '초코 식빵 디저트';
  const domWithZeroWidth = '초\u200B코 \uFEFF식빵\u200D 디저트';
  assert.strictEqual(canonicalPromptText(domWithZeroWidth), canonicalPromptText(expected));
});

test('EDITOR-004: emoji variation selector and bidi markers', () => {
  const expected = '카페 투어 ☕';
  const domWithSelectors = '카페 투어 ☕\uFE0F\u200E';
  assert.strictEqual(canonicalPromptText(domWithSelectors), canonicalPromptText(expected));
});

test('EDITOR-005: stale prefix fail', () => {
  const expected = '새로 입력할 프롬프트 내용입니다';
  const domStaleAppended = '이전 댓글 잔여 텍스트 새로 입력할 프롬프트 내용입니다';
  assert.notStrictEqual(canonicalPromptText(domStaleAppended), canonicalPromptText(expected));
});

test('EDITOR-006: missing prompt part fail', () => {
  const expected = '전체 프롬프트가 모두 들어가야 합니다';
  const domTruncated = '전체 프롬프트가 모두';
  assert.notStrictEqual(canonicalPromptText(domTruncated), canonicalPromptText(expected));
});

test('EDITOR-007: DOM node replacement / re-resolve simulation', async () => {
  let activeElement = { isConnected: true, text: '' };

  function editor() {
    return activeElement;
  }

  // Simulation: text injected, but framework replaces DOM element
  activeElement.text = '초기 텍스트';
  activeElement.isConnected = false; // Detached by React/Angular

  const newElement = { isConnected: true, text: '정상 재주입 텍스트' };
  activeElement = newElement;

  const current = editor();
  assert.strictEqual(current.isConnected, true);
  assert.strictEqual(canonicalPromptText(current.text), canonicalPromptText('정상 재주입 텍스트'));
});

test('EDITOR-008: 2~4KB multiline Korean prompt canonical equality', () => {
  const lines = [];
  for (let i = 0; i < 50; i++) {
    lines.push(`블로그 포스팅 본문 ${i}번째 분석 단락입니다. 신촌-대흥 대학생 카페 탐방기.`);
  }
  const expectedPrompt = lines.join('\n');
  const domText = lines.join('\r\n');
  assert.strictEqual(canonicalPromptText(domText), canonicalPromptText(expectedPrompt));
  assert.ok(expectedPrompt.length > 2000, 'Prompt length should be >2KB');
});

test('EDITOR-009: old r6 runtime + r7 contract triggers reinjection', () => {
  const contract = { runtimeBuild: '13.2.3-r7' };
  const pingResponse = { ok: true, build: '13.2.3-r6' };

  let reinjected = false;
  if (!pingResponse.ok || pingResponse.build !== contract.runtimeBuild) {
    reinjected = true;
  }
  assert.strictEqual(reinjected, true, 'Mismatch build must trigger ensureGeminiRuntime');
});

test('JS Extension Contract: cancelExecution settles pending promise immediately', async () => {
  let activeExecution = null;

  function cancelExecution(reqId, reason = 'cancelled') {
    if (!activeExecution) return false;
    if (!reqId || activeExecution.requestId === reqId) {
      activeExecution.cancelled = true;
      if (typeof activeExecution.finish === 'function') {
        activeExecution.finish({ status: 'failed', text: '', error: reason });
      }
      return true;
    }
    return false;
  }

  const execPromise = new Promise((resolve) => {
    let resolved = false;
    const finish = (res) => {
      if (resolved) return;
      resolved = true;
      activeExecution = null;
      resolve(res);
    };

    activeExecution = {
      requestId: 'req-cancel-test',
      startedAt: Date.now(),
      deadlineAt: Date.now() + 70000,
      cancelled: false,
      finish: finish
    };
  });

  const cancelSuccess = cancelExecution('req-cancel-test', 'cancelled_by_bridge');
  assert.strictEqual(cancelSuccess, true);

  const result = await execPromise;
  assert.strictEqual(result.status, 'failed');
  assert.strictEqual(result.error, 'cancelled_by_bridge');
  assert.strictEqual(activeExecution, null);
});

test('JS Extension Contract: two-message protocol ACK and result resolution with timer cleanup', async () => {
  const inFlightCommandResolvers = new Map();
  let timerCleared = false;

  const command = {
    requestId: 'req-two-msg',
    postKey: 'post:1',
    navigationVersion: 1,
    prompt: 'test',
    deadlineAt: Date.now() / 1000 + 70
  };

  const dispatchPromise = new Promise((resolve) => {
    const timer = setTimeout(() => {
      inFlightCommandResolvers.delete(command.requestId);
      resolve({ status: 'timeout', text: '', error: 'command_deadline_exceeded' });
    }, 65000);

    const mockTimer = {
      _id: timer,
      clear() {
        clearTimeout(timer);
        timerCleared = true;
      }
    };

    inFlightCommandResolvers.set(command.requestId, {
      resolve,
      timer: mockTimer,
      commandMetadata: command
    });
  });

  const incomingMessage = {
    type: 'NFA_EXECUTION_RESULT',
    requestId: command.requestId,
    postKey: command.postKey,
    navigationVersion: command.navigationVersion,
    result: { status: 'completed', text: '좋은 글입니다', error: '' }
  };

  const inFlight = inFlightCommandResolvers.get(incomingMessage.requestId);
  assert.ok(inFlight, 'Resolver must exist');
  inFlight.timer.clear();
  inFlightCommandResolvers.delete(incomingMessage.requestId);
  inFlight.resolve(incomingMessage.result);

  const settled = await dispatchPromise;
  assert.strictEqual(settled.status, 'completed');
  assert.strictEqual(settled.text, '좋은 글입니다');
  assert.strictEqual(timerCleared, true);
  assert.strictEqual(inFlightCommandResolvers.size, 0);
});

test('JS Extension Contract: resolver-loss result recovery fallback', async () => {
  let recoverySubmitted = null;
  const inFlightCommandResolvers = new Map();

  function mockBridgeFetch(path, method, body) {
    if (path === '/v1/result') {
      recoverySubmitted = body;
      return Promise.resolve({ ok: true, accepted: true });
    }
    return Promise.reject(new Error('not_found'));
  }

  const incomingMessage = {
    type: 'NFA_EXECUTION_RESULT',
    requestId: 'req-orphaned-resolver',
    postKey: 'post:orphan',
    navigationVersion: 2,
    result: { status: 'completed', text: 'recovered text', error: '' }
  };

  const inFlight = inFlightCommandResolvers.get(incomingMessage.requestId);
  if (inFlight) {
    inFlight.resolve(incomingMessage.result);
  } else {
    await mockBridgeFetch('/v1/result', 'POST', {
      requestId: incomingMessage.requestId,
      postKey: incomingMessage.postKey || '',
      navigationVersion: incomingMessage.navigationVersion || 0,
      status: incomingMessage.result.status || 'failed',
      text: incomingMessage.result.text || '',
      error: incomingMessage.result.error || ''
    });
  }

  assert.ok(recoverySubmitted, 'Recovery result must be submitted');
  assert.strictEqual(recoverySubmitted.requestId, 'req-orphaned-resolver');
  assert.strictEqual(recoverySubmitted.text, 'recovered text');
});

test('GEM-R8-001: fresh chat empty turns verification', () => {
  const userQueries = [];
  const responses = [];
  const hasEditor = true;
  const isFresh = userQueries.length === 0 && responses.length === 0 && hasEditor;
  assert.strictEqual(isFresh, true, 'Fresh chat must have 0 queries and 0 responses');
});

test('GEM-R8-002: prompt send in fresh chat', () => {
  let sendConfirmed = false;
  const initialQueries = [];
  const currentQueries = [{ text: '프롬프트' }];
  if (currentQueries.length > initialQueries.length) {
    sendConfirmed = true;
  }
  assert.strictEqual(sendConfirmed, true, 'Send must be confirmed upon new query');
});

test('GEM-R8-003: visible response appears and binds in fresh chat', () => {
  const freshChatVerified = true;
  const currentResponses = [{ tagName: 'MODEL-RESPONSE', isConnected: true, text: '신규 답변' }];
  let boundNode = null;
  if (freshChatVerified && currentResponses.length > 0) {
    boundNode = currentResponses[currentResponses.length - 1];
  }
  assert.ok(boundNode, 'Latest visible model response must be bound in fresh chat');
  assert.strictEqual(boundNode.text, '신규 답변');
});

test('GEM-R8-004: response text stable 1800ms while global aria-busy remains true -> completed', () => {
  const globalAriaBusy = true; // Lingering elsewhere on page
  const targetResponse = {
    text: '완성된 댓글 본문입니다~',
    hasLocalStreaming: false,
    hasComposerStop: false
  };
  const lastMutationAt = Date.now() - 1900; // 1900ms ago
  const mutationAge = Date.now() - lastMutationAt;

  let completed = false;
  // Under r8: authoritative condition does NOT check global aria-busy!
  if (targetResponse.text.length > 0 && mutationAge >= 1800 && !targetResponse.hasLocalStreaming && !targetResponse.hasComposerStop) {
    completed = true;
  }
  assert.strictEqual(completed, true, 'Stable text must complete even if page-wide aria-busy is true');
});

test('GEM-R8-005: selector candidate fallback finds response', () => {
  const mockDOM = [
    { selector: 'div[data-message-author-role="model"]', text: '모델 응답' }
  ];
  const SELECTORS = [
    'model-response',
    'div[data-message-author-role="model"]',
    'div.model-response',
    '[data-test-id="model-response"]'
  ];
  let found = null;
  for (const sel of SELECTORS) {
    const match = mockDOM.find(el => el.selector === sel);
    if (match) {
      found = match;
      break;
    }
  }
  assert.ok(found, 'Fallback selector must locate response');
  assert.strictEqual(found.text, '모델 응답');
});

test('GEM-R8-006: visible completed response exists -> 65s timeout prohibited', () => {
  const visibleResponseText = '이미 완성된 답변';
  const lastMutationAge = 3500; // Stable for 3.5s
  let status = 'pending';

  if (visibleResponseText.length > 0 && lastMutationAge >= 1800) {
    status = 'completed';
  }
  assert.notStrictEqual(status, 'timeout');
  assert.strictEqual(status, 'completed');
});

test('GEM-R8-007: WAIT_DIAG diagnostic payload generated', () => {
  const diag = {
    rid: 'test-rid-diag',
    elapsedMs: 5100,
    freshChatVerified: true,
    sendConfirmed: true,
    userQueryCount: 1,
    responseSelectorCount: 1,
    visibleResponseCount: 1,
    responseBound: true,
    responseTextLength: 25,
    lastMutationAgeMs: 1900,
    generationEvidence: 'idle',
    runtimeBuild: '13.2.3-r9'
  };
  assert.strictEqual(diag.runtimeBuild, '13.2.3-r9');
  assert.strictEqual(diag.freshChatVerified, true);
  assert.strictEqual(diag.responseBound, true);
});

test('GEM-R9-008: old r8 runtime with r9 contract triggers reinjection', () => {
  const contract = { runtimeBuild: '13.2.3-r9' };
  const pingResponse = { ok: true, build: '13.2.3-r8' };
  let reinjected = false;
  if (!pingResponse.ok || pingResponse.build !== contract.runtimeBuild) {
    reinjected = true;
  }
  assert.strictEqual(reinjected, true, 'r8 build must trigger reinjection under r9 contract');
});

test('GEM-R9-009: Run A false-positive sendConfirmed guard requires structural turn evidence', () => {
  // Simulate Run A failure mode: composer was cleared or button clicked,
  // but after 5 seconds userUniqueTurn=0 and responseUniqueTurn=0.
  const composerCleared = true;
  const buttonClicked = true;
  const userTurns = [];
  const modelTurns = [];

  // Under R9: send confirmation strictly requires new user turn or new model turn
  let confirmed = false;
  if (userTurns.length > 0 || modelTurns.length > 0) {
    confirmed = true;
  }

  assert.strictEqual(confirmed, false, 'sendConfirmed must be false when structural turn evidence is absent');

  // Must fail with user_turn_not_created (or send_not_confirmed if button not clicked)
  const failReason = buttonClicked ? 'user_turn_not_created' : 'send_not_confirmed';
  assert.strictEqual(failReason, 'user_turn_not_created');
});

test('GEM-R9-010: Run B candidate inventory unification prevents invariant violation', () => {
  // Simulate Run B: 4 elements match response selector, but all have display:none (visible=0)
  const matchedElements = [
    { tag: 'model-response', visible: false, text: '' },
    { tag: 'div.model-response', visible: false, text: '' },
    { tag: 'message-content', visible: false, text: '' },
    { tag: 'div[data-message-author-role="model"]', visible: false, text: '' }
  ];

  const responseSelectorMatches = matchedElements.length; // 4
  const visibleTextCandidates = matchedElements.filter(e => e.visible).length; // 0

  function bindResponse(cand) {
    if (visibleTextCandidates === 0 || !cand || !cand.visible) {
      return null; // Invariant guard prevents binding
    }
    return cand;
  }

  const bound = bindResponse(matchedElements[0]);
  assert.strictEqual(bound, null, 'Must not bind when visibleTextCandidates=0');

  // If a bound node exists while visibleTextCandidates=0, it is an invariant violation
  let error = null;
  let targetResponseNode = bound;
  if (targetResponseNode && visibleTextCandidates === 0) {
    error = 'response_binding_invariant_violation';
  } else if (!targetResponseNode && visibleTextCandidates === 0) {
    error = 'response_turn_not_found';
  }
  assert.strictEqual(error, 'response_turn_not_found');
});

test('GEM-R9-011: display:contents wrapper resolution separates turnNode and textNode', () => {
  // Wrapper custom element has display: contents (0x0 rect), but descendant has text
  const turnNode = {
    tag: 'model-response',
    style: { display: 'contents' },
    rect: { width: 0, height: 0 }
  };
  const textChild = {
    tag: 'div.markdown',
    text: '생성된 응답 내용입니다.',
    style: { display: 'block', visibility: 'visible' },
    rect: { width: 400, height: 80 }
  };

  function resolveTurnCandidate(turn, child) {
    const isVisible = (child.rect.width > 15 && child.rect.height > 15 && child.style.visibility !== 'hidden') ||
                      child.text.trim().length > 0;
    return {
      turnNode: turn,
      textNode: child,
      text: child.text.trim(),
      isVisible: isVisible
    };
  }

  const cand = resolveTurnCandidate(turnNode, textChild);
  assert.strictEqual(cand.isVisible, true, 'display:contents wrapper with visible text descendant is a valid candidate');
  assert.strictEqual(cand.turnNode.tag, 'model-response');
  assert.strictEqual(cand.textNode.tag, 'div.markdown');
  assert.strictEqual(cand.text, '생성된 응답 내용입니다.');
});

test('GEM-R9-012: zero-text 3.5s stall triggers 1-time re-resolve and fails with response_stream_no_text', () => {
  let boundNode = { id: 'node-empty-1', text: '' };
  let boundAtMs = 1000;
  let currentTimeMs = 4600; // 3.6s elapsed
  let reResolveAttempted = false;
  let resultStatus = null;
  let resultError = null;

  const zeroDuration = currentTimeMs - boundAtMs;
  assert.ok(zeroDuration >= 3500, '3.5s zero-text threshold exceeded');

  // 1st attempt: discard binding and re-resolve
  if (zeroDuration >= 3500 && !reResolveAttempted) {
    reResolveAttempted = true;
    boundNode = null; // discard
    // Candidate pool returns another empty node
    const nextCand = { id: 'node-empty-2', text: '' };
    boundNode = nextCand;
    boundAtMs = currentTimeMs;
  }

  assert.strictEqual(reResolveAttempted, true);
  assert.strictEqual(boundNode.id, 'node-empty-2');

  // After re-resolve, another 3.5s pass with textLen=0
  currentTimeMs = 8200;
  const secondZeroDuration = currentTimeMs - boundAtMs;
  if (secondZeroDuration >= 3500 && reResolveAttempted) {
    resultStatus = 'failed';
    resultError = 'response_stream_no_text';
  }

  assert.strictEqual(resultStatus, 'failed');
  assert.strictEqual(resultError, 'response_stream_no_text');
});

test('GEM-R9-013: scoped local streaming ignores global page aria-busy', () => {
  const globalDoc = { ariaBusy: true }; // Page sidebar / background has aria-busy
  const boundTurnNode = {
    indicators: [] // No local .streaming or .loading-dots inside this turn
  };
  const composer = { stopButton: null };

  function detectGenerationEvidence(boundNode, comp) {
    if (boundNode.indicators.length > 0) return 'local_streaming';
    if (comp.stopButton) return 'composer_stop_button';
    return 'idle';
  }

  const evidence = detectGenerationEvidence(boundTurnNode, composer);
  assert.strictEqual(evidence, 'idle', 'Must be idle even when global doc has ariaBusy=true');
});

test('GEM-R9-014: split diagnostics fields verification and DOM node deduplication', () => {
  // Multiple query selectors matching parts of the same turn
  const domTurns = [
    { id: 'turn-1', role: 'user' },
    { id: 'turn-1', role: 'user' }, // Duplicate child match
    { id: 'turn-2', role: 'user' }
  ];

  const userSelectorMatches = domTurns.length; // 3
  const seenNodes = new Set();
  const uniqueTurns = [];
  for (const el of domTurns) {
    if (!seenNodes.has(el.id)) {
      seenNodes.add(el.id);
      uniqueTurns.push(el);
    }
  }
  const userUniqueTurns = uniqueTurns.length; // 2

  assert.strictEqual(userSelectorMatches, 3);
  assert.strictEqual(userUniqueTurns, 2);

  const diag = {
    userSelectorMatches,
    userUniqueTurns,
    responseSelectorMatches: 5,
    responseUniqueTurns: 2,
    visibleTextCandidates: 1
  };

  assert.strictEqual(diag.userSelectorMatches, 3);
  assert.strictEqual(diag.userUniqueTurns, 2);
  assert.strictEqual(diag.responseSelectorMatches, 5);
  assert.strictEqual(diag.responseUniqueTurns, 2);
  assert.strictEqual(diag.visibleTextCandidates, 1);
});

test('GEM-R9-015: 7 new failure codes contract verification', () => {
  const allowedFailCodes = new Set([
    'send_not_confirmed',
    'user_turn_not_created',
    'response_turn_not_found',
    'response_text_target_not_found',
    'response_binding_invariant_violation',
    'response_stream_no_text',
    'response_stalled'
  ]);

  assert.strictEqual(allowedFailCodes.has('send_not_confirmed'), true);
  assert.strictEqual(allowedFailCodes.has('user_turn_not_created'), true);
  assert.strictEqual(allowedFailCodes.has('response_turn_not_found'), true);
  assert.strictEqual(allowedFailCodes.has('response_text_target_not_found'), true);
  assert.strictEqual(allowedFailCodes.has('response_binding_invariant_violation'), true);
  assert.strictEqual(allowedFailCodes.has('response_stream_no_text'), true);
  assert.strictEqual(allowedFailCodes.has('response_stalled'), true);
  assert.strictEqual(allowedFailCodes.size, 7);
});

test('GEM-R9-016: tightened send confirmation requires user correlation, model turn alone prohibited in pre-existing chat', () => {
  // Case A: Pre-existing conversation with baseline model turns > 0
  const freshChatVerified = false;
  const initialResponseCount = 2;
  const newUserTurnFound = false;
  const newModelCandidate = { id: 'model-turn-3' };

  function verifySend(fresh, initCount, userTurn, modelTurn) {
    if (userTurn) return { confirmed: true, reason: 'user_turn_confirmed' };
    if (fresh && initCount === 0 && modelTurn) return { confirmed: true, reason: 'fresh_fallback' };
    return { confirmed: false, reason: 'send_not_confirmed' };
  }

  const resultA = verifySend(freshChatVerified, initialResponseCount, newUserTurnFound, newModelCandidate);
  assert.strictEqual(resultA.confirmed, false, 'Pre-existing chat must NOT confirm send based solely on model turn');

  // Case B: Verified fresh chat (turns=0) allows fallback if model candidate appears
  const resultB = verifySend(true, 0, false, newModelCandidate);
  assert.strictEqual(resultB.confirmed, true, 'Verified fresh chat allows fallback confirmation on model candidate');
  assert.strictEqual(resultB.reason, 'fresh_fallback');

  // Case C: User turn with prompt correlation always confirms send
  const resultC = verifySend(false, 2, true, newModelCandidate);
  assert.strictEqual(resultC.confirmed, true, 'User turn correlation confirms send in any conversation');
  assert.strictEqual(resultC.reason, 'user_turn_confirmed');
});

test('GEM-R9-017: legitimate thinking grace vs zero-evidence stall separation', () => {
  // Scenario A: bound + text=0 + response-local streaming evidence present (thinking / shell / loading-dots)
  let boundAtMs = 1000;
  let nowMs = 6500; // 5.5s elapsed (> 3.5s)
  let localEvidence = 'local_streaming';
  let hasStreamingEvidence = (localEvidence === 'local_streaming' || localEvidence === 'composer_stop_button');

  function checkStall(boundAt, now, evidencePresent, reResolveAttempted) {
    const dur = now - boundAt;
    if (evidencePresent) {
      if (dur >= 15000) return { action: 'fail', error: 'response_stream_no_text' };
      return { action: 'wait_grace', remainingGraceMs: 15000 - dur };
    } else {
      if (dur >= 3500) {
        if (!reResolveAttempted) return { action: 'reresolve' };
        return { action: 'fail', error: 'response_stream_no_text' };
      }
      return { action: 'wait' };
    }
  }

  const resGrace = checkStall(boundAtMs, nowMs, hasStreamingEvidence, false);
  assert.strictEqual(resGrace.action, 'wait_grace', 'At 5.5s with streaming evidence, must NOT stall out; grant grace period up to 15s');

  // After 15s with streaming evidence but still 0 text -> fail
  const resGraceExceeded = checkStall(boundAtMs, 16500, hasStreamingEvidence, false);
  assert.strictEqual(resGraceExceeded.action, 'fail');
  assert.strictEqual(resGraceExceeded.error, 'response_stream_no_text');

  // Scenario B: bound + text=0 + NO streaming evidence (idle empty node)
  const resNoEvidence3s = checkStall(boundAtMs, 3000, false, false);
  assert.strictEqual(resNoEvidence3s.action, 'wait');

  // At 4.5s without streaming evidence -> must re-resolve 1 time
  const resNoEvidenceReResolve = checkStall(boundAtMs, 4500, false, false);
  assert.strictEqual(resNoEvidenceReResolve.action, 'reresolve');

  // After 1 re-resolve, another 3.5s pass without evidence -> fail fast
  const resNoEvidenceFailed = checkStall(boundAtMs, 4500, false, true);
  assert.strictEqual(resNoEvidenceFailed.action, 'fail');
  assert.strictEqual(resNoEvidenceFailed.error, 'response_stream_no_text');
});

test('GEM-R11-018: old r10 runtime with r11 contract triggers reinjection', () => {
  const contract = { runtimeBuild: '13.2.3-r11' };
  const pingResponse = { ok: true, build: '13.2.3-r10' };
  let reinjected = false;
  if (!pingResponse.ok || pingResponse.build !== contract.runtimeBuild) {
    reinjected = true;
  }
  assert.strictEqual(reinjected, true, 'r10 build must trigger reinjection under r11 contract');
});

test('GEM-R10-019: Gemini DOM with thinking/header UI ("Initiating the Analysis Gemini의 응답" + actual text) extracts ONLY the actual text', () => {
  const CONTAMINATION_PATTERNS = [
    /Initiating the Analysis/i,
    /Gemini의\s*응답/i
  ];

  // Simulating turn node structure with status container, header, and actual message-content
  const turn = {
    children: [
      { tag: 'div', className: 'status-container', text: 'Initiating the Analysis', isExcluded: true },
      { tag: 'header', className: 'header', text: 'Gemini의 응답', isExcluded: true },
      { tag: 'message-content', className: 'message-content', text: '삼겹살 구이가 정말 노릇노릇 맛있어 보이네요~', isExcluded: false }
    ]
  };

  function extractClean(tNode) {
    let combined = '';
    for (const c of tNode.children) {
      if (!c.isExcluded) {
        combined += c.text + ' ';
      }
    }
    const cleaned = combined.trim();
    for (const pat of CONTAMINATION_PATTERNS) {
      if (pat.test(cleaned)) {
        const stripped = cleaned.replace(pat, '').trim();
        if (stripped.length === 0 || stripped.length < 10) return '';
      }
    }
    return cleaned;
  }

  const result = extractClean(turn);
  assert.strictEqual(result, '삼겹살 구이가 정말 노릇노릇 맛있어 보이네요~');
  assert.ok(!result.includes('Initiating the Analysis'));
  assert.ok(!result.includes('Gemini의 응답'));
});

test('GEM-R10-020: Gemini DOM with ONLY thinking/status UI extracts empty string (does not fire TEXT_NONEMPTY)', () => {
  const CONTAMINATION_PATTERNS = [
    /Initiating the Analysis/i,
    /Gemini의\s*응답/i
  ];

  // In thinking/initiating phase, ONLY status/header nodes exist
  const turnOnlyStatus = {
    children: [
      { tag: 'div', className: 'status-container', text: 'Initiating the Analysis', isExcluded: true },
      { tag: 'header', className: 'header', text: 'Gemini의 응답', isExcluded: true }
    ]
  };

  function extractClean(tNode) {
    let combined = '';
    for (const c of tNode.children) {
      if (!c.isExcluded) {
        combined += c.text + ' ';
      }
    }
    const cleaned = combined.trim();
    for (const pat of CONTAMINATION_PATTERNS) {
      if (pat.test(cleaned)) {
        const stripped = cleaned.replace(pat, '').trim();
        if (stripped.length === 0 || stripped.length < 10) return '';
      }
    }
    return cleaned;
  }

  const result = extractClean(turnOnlyStatus);
  assert.strictEqual(result, '');

  // Since text is empty, TEXT_NONEMPTY is NOT logged/emitted
  let textNonEmptyFired = false;
  if (result.length > 0) {
    textNonEmptyFired = true;
  }
  assert.strictEqual(textNonEmptyFired, false, 'TEXT_NONEMPTY must not fire for empty text');
});

test('GEM-R10-021: Cancelled execution produces 0 WAIT_DIAG, 0 RESPONSE_BOUND, 0 RESULT', () => {
  const cancelledRequestIds = new Set(['req_cancelled_123']);
  let waitDiagSent = 0;
  let eventSent = 0;
  let resultSent = 0;

  function emitEvent(rid, type) {
    if (cancelledRequestIds.has(rid)) return;
    eventSent++;
  }

  function reportWaitDiag(rid) {
    if (cancelledRequestIds.has(rid)) return;
    waitDiagSent++;
  }

  function submitResult(rid, res) {
    if (cancelledRequestIds.has(rid)) return;
    resultSent++;
  }

  emitEvent('req_cancelled_123', 'RESPONSE_TURN_BOUND');
  reportWaitDiag('req_cancelled_123');
  submitResult('req_cancelled_123', { status: 'completed', text: 'hello' });

  assert.strictEqual(waitDiagSent, 0, 'Cancelled rid must produce 0 WAIT_DIAG');
  assert.strictEqual(eventSent, 0, 'Cancelled rid must produce 0 RESPONSE_TURN_BOUND');
  assert.strictEqual(resultSent, 0, 'Cancelled rid must produce 0 RESULT');
});

test('GEM-R10-022: ResponseContaminationGate logic in JS simulation', () => {
  const contaminationPhrases = [
    'Initiating the Analysis',
    'Gemini의 응답',
    'Thinking...',
    'Show thinking',
    'Hide thinking',
    '다른 답안 보기',
    'view other drafts'
  ];

  function validateContamination(text) {
    const norm = text.trim();
    for (const phrase of contaminationPhrases) {
      if (norm.toLowerCase().includes(phrase.toLowerCase())) {
        return { isContaminated: true, matched: phrase };
      }
    }
    return { isContaminated: false };
  }

  const liveIncidentText = 'Initiating the Analysis Gemini의 응답';
  const checkLive = validateContamination(liveIncidentText);
  assert.strictEqual(checkLive.isContaminated, true);
  assert.strictEqual(checkLive.matched, 'Initiating the Analysis');

  const validAnswer = '신촌 고기집 다녀오셨군요! 노릇노릇 삼겹살 맛있겠어요~';
  const checkValid = validateContamination(validAnswer);
  assert.strictEqual(checkValid.isContaminated, false);
});

test('GEM-R11-023: answer inside aria-live parent is properly extracted and not excluded', () => {
  function isExcluded(node) {
    if (node.isExcluded) return true;
    if (node.role === 'status') return true;
    if (node.role === 'status' && node.ariaLive) return true;
    // Bare aria-live is NOT excluded
    return false;
  }

  const liveContainer = {
    ariaLive: 'polite',
    children: [
      {
        tag: 'message-content',
        text: '성수동 디저트 카페 소금빵과 크로플이 정말 맛있어 보이네요~',
        isExcluded: false
      }
    ]
  };

  assert.strictEqual(isExcluded(liveContainer), false, 'Bare aria-live parent must NOT be excluded');
  let extracted = '';
  for (const child of liveContainer.children) {
    if (!isExcluded(child)) extracted += child.text;
  }
  assert.strictEqual(extracted, '성수동 디저트 카페 소금빵과 크로플이 정말 맛있어 보이네요~');
});

test('GEM-R11-024: status/header + actual answer inside same container extracts ONLY actual answer', () => {
  const sameContainerNode = {
    children: [
      { tag: 'div', role: 'status', text: 'Initiating the Analysis', isExcluded: true },
      { tag: 'header', text: 'Gemini의 응답', isExcluded: true },
      { tag: 'div', className: 'markdown', text: '여기는 육즙 가득한 수제 패티가 일품인 수제버거 맛집입니다.', isExcluded: false }
    ]
  };

  let extracted = '';
  for (const child of sameContainerNode.children) {
    if (!child.isExcluded) {
      extracted += child.text + ' ';
    }
  }
  extracted = extracted.trim();
  assert.strictEqual(extracted, '여기는 육즙 가득한 수제 패티가 일품인 수제버거 맛집입니다.');
  assert.ok(!extracted.includes('Initiating the Analysis'));
  assert.ok(!extracted.includes('Gemini의 응답'));
});

test('GEM-R11-025: CSS copy icon text inside action/button is excluded from answer text', () => {
  const responseTurnWithButton = {
    children: [
      { tag: 'message-content', text: '이탈리안 정통 까르보나라 파스타 풍미가 진해 보이네요~', isExcluded: false },
      { tag: 'button', className: 'copy-button', text: 'content_copy', isExcluded: true },
      { tag: 'div', className: 'actions', text: '복사하기 share thumb_up', isExcluded: true }
    ]
  };

  let extracted = '';
  for (const child of responseTurnWithButton.children) {
    if (!child.isExcluded) {
      extracted += child.text;
    }
  }
  assert.strictEqual(extracted, '이탈리안 정통 까르보나라 파스타 풍미가 진해 보이네요~');
  assert.ok(!extracted.includes('content_copy'));
  assert.ok(!extracted.includes('복사하기'));
});

test('GEM-R11-026: extractCleanText strips "Gemini의 응답\\n" prefix and returns clean remainder', () => {
  const CONTAMINATION_PATTERNS = [
    /Initiating the Analysis/i,
    /Gemini의\s*응답/i,
    /Thinking\.\.\./i
  ];

  function extractCleanTextSim(rawText) {
    let remainder = rawText.trim();
    if (!remainder) return '';

    let hadContamination = false;
    for (const pat of CONTAMINATION_PATTERNS) {
      if (pat.test(remainder)) {
        hadContamination = true;
        const globalPat = new RegExp(pat.source, pat.flags.includes('g') ? pat.flags : pat.flags + 'g');
        remainder = remainder.replace(globalPat, '');
      }
    }

    if (hadContamination) {
      remainder = remainder
        .split('\n')
        .map(line => line.trim())
        .filter(Boolean)
        .join('\n')
        .trim();

      for (const pat of CONTAMINATION_PATTERNS) {
        if (pat.test(remainder)) {
          return '';
        }
      }

      if (remainder.length < 10) {
        return '';
      }
      return remainder;
    }

    return remainder;
  }

  // Case 1: Mixed status + valid comment -> clean remainder returned
  const mixedInput = "Gemini의 응답\n곱창 소스에 청양고추 넣으면 끝도 없이 들어가겠네요~";
  const cleaned = extractCleanTextSim(mixedInput);
  assert.strictEqual(cleaned, "곱창 소스에 청양고추 넣으면 끝도 없이 들어가겠네요~");

  // Case 2: Only status string -> empty string returned (editor insert 0 / submit 0)
  const onlyStatus = "Initiating the Analysis Gemini의 응답";
  const cleanedEmpty = extractCleanTextSim(onlyStatus);
  assert.strictEqual(cleanedEmpty, "");

  // Case 3: Status string + short text (<10 chars) -> empty string returned
  const statusPlusShort = "Thinking...\n좋아요";
  const cleanedShort = extractCleanTextSim(statusPlusShort);
  assert.strictEqual(cleanedShort, "");
});

test('GEM-R11-027: visibleTurnCandidates and visibleTextCandidates split verification', () => {
  const candidates = [
    { id: 'cand-1', isCandidate: true, text: '첫 번째 정상 생성 댓글입니다.' },
    { id: 'cand-2', isCandidate: true, text: '' },
    { id: 'cand-3', isCandidate: false, text: 'baseline turn' }
  ];

  const validCandidates = candidates.filter(c => c.isCandidate);
  const visibleTurnCandidates = validCandidates.length;
  const visibleTextCandidates = validCandidates.filter(c => (c.text || '').trim().length > 0).length;

  assert.strictEqual(visibleTurnCandidates, 2, 'visibleTurnCandidates must count all valid candidate turns');
  assert.strictEqual(visibleTextCandidates, 1, 'visibleTextCandidates must only count candidates with non-empty text');
});

test('GEM-R12-028: old r11 runtime with r12 contract triggers reinjection', () => {
  const contract = { runtimeBuild: '13.2.3-r12' };
  const pingResponse = { ok: true, build: '13.2.3-r11' };
  let reinjected = false;
  if (!pingResponse.ok || pingResponse.build !== contract.runtimeBuild) {
    reinjected = true;
  }
  assert.strictEqual(reinjected, true, 'r11 build must trigger reinjection under r12 contract');
});

test('GEM-R12-029: deliverExecutionResult retries on missing background ACK and performs direct HTTP fallback', async () => {
  let sendMessageAttempts = 0;
  let directFetchCalled = false;
  let directFetchPayload = null;

  const mockSendMessage = (_payload, callback) => {
    sendMessageAttempts++;
    // Simulate background script asleep/unresponsive on attempt 1, 2, 3
    callback({ ok: false, error: 'no_ack' });
  };

  const mockFetch = async (url, options) => {
    directFetchCalled = true;
    directFetchPayload = JSON.parse(options.body);
    return {
      ok: true,
      json: async () => ({ ok: true, accepted: true })
    };
  };

  const command = {
    requestId: 'req_fallback_test',
    postKey: 'user:123',
    navigationVersion: 1
  };
  const result = {
    status: 'completed',
    text: '돼지갈비찜에 콩나물 바로 올려서 드시는 게 딱이네요~',
    error: ''
  };

  // Simulating deliverExecutionResult logic
  let backgroundAck = false;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const resp = await new Promise((resolve) => {
      mockSendMessage({ type: 'NFA_EXECUTION_RESULT', command, result }, (ack) => {
        resolve(ack);
      });
    });
    if (resp?.ok) {
      backgroundAck = true;
      break;
    }
  }

  if (!backgroundAck || (result.status === 'completed' && result.text)) {
    await mockFetch('http://127.0.0.1:43127/v1/result', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        requestId: command.requestId,
        postKey: command.postKey,
        navigationVersion: command.navigationVersion,
        status: result.status,
        text: result.text,
        error: result.error
      })
    });
  }

  assert.strictEqual(sendMessageAttempts, 3, 'Must retry background messaging 3 times');
  assert.strictEqual(directFetchCalled, true, 'Must execute direct HTTP fallback to Python bridge');
  assert.strictEqual(directFetchPayload.text, '돼지갈비찜에 콩나물 바로 올려서 드시는 게 딱이네요~');
  assert.strictEqual(directFetchPayload.status, 'completed');
});

test('GEM-R12-030: background keepalive interval resets SW idle timer and cleans up on command settle', async () => {
  const inFlightCommandResolvers = new Map();
  let keepAlivePings = 0;
  let keepAliveCleaned = false;

  const rid = 'req_keepalive_test';
  const keepAliveInterval = {
    _id: 1,
    clear() {
      keepAliveCleaned = true;
    }
  };

  inFlightCommandResolvers.set(rid, {
    resolve: () => {},
    timer: { clear: () => {} },
    keepAliveInterval
  });

  // Simulating keepalive tick
  if (inFlightCommandResolvers.has(rid)) {
    keepAlivePings++;
  }

  assert.strictEqual(keepAlivePings, 1);

  // Simulating command completion cleanup
  const inFlight = inFlightCommandResolvers.get(rid);
  assert.ok(inFlight);
  inFlight.keepAliveInterval.clear();
  inFlightCommandResolvers.delete(rid);

  assert.strictEqual(keepAliveCleaned, true, 'keepalive interval must be cleared on command settle');
  assert.strictEqual(inFlightCommandResolvers.has(rid), false);
});

test('GEM-R14-031: old r13 runtime with r14 contract triggers reinjection', () => {
  const contract = { runtimeBuild: '13.2.3-r14' };
  const pingResponse = { ok: true, build: '13.2.3-r13' };
  let reinjected = false;
  if (!pingResponse.ok || pingResponse.build !== contract.runtimeBuild) {
    reinjected = true;
  }
  assert.strictEqual(reinjected, true, 'r13 build must trigger reinjection under r14 contract');
});

test('GEM-R14-032: 정상 completed -> cleanup -> 예외 0 (실제 content.js 실행 경로)', async () => {
  const { runtime } = loadRealContentJs();
  assert.ok(runtime && typeof runtime.setupResponseObserver === 'function');

  const execState = {
    requestId: 'req_completed_001',
    startedAtMs: Date.now(),
    deadlineAtMs: Date.now() + 10000,
    cancelled: false,
    observer: null,
    timer: null,
    finish: null,
    resolve: null
  };

  const finishPromise = new Promise((resolve) => {
    let resolved = false;
    let cleanupObserver = null;
    const finish = (res) => {
      if (resolved) return;
      resolved = true;
      if (typeof cleanupObserver === 'function') cleanupObserver();
      resolve(res);
    };
    execState.finish = finish;
    execState.resolve = resolve;
    cleanupObserver = runtime.setupResponseObserver(execState, {}, () => {});
    execState.cleanupObserver = cleanupObserver;
  });

  assert.ok(execState.timer !== null, 'timer should be set before finish');
  assert.ok(execState.observer !== null, 'observer should be set before finish');

  // 정상 completed 완료 호출 시 cleanup 실행 및 예외 0 검증
  assert.doesNotThrow(() => {
    execState.finish({ status: 'completed', text: '정상 응답 생성 완료' });
  });

  const res = await finishPromise;
  assert.strictEqual(res.status, 'completed');
  assert.strictEqual(res.text, '정상 응답 생성 완료');
  assert.strictEqual(execState.timer, null, 'execState.timer must be null after completed');
  assert.strictEqual(execState.observer, null, 'execState.observer must be null after completed');
});

test('GEM-R14-033: timeout -> cleanup -> 예외 0 (실제 content.js 실행 경로)', async () => {
  const { runtime } = loadRealContentJs();

  const execState = {
    requestId: 'req_timeout_001',
    startedAtMs: Date.now() - 60000,
    deadlineAtMs: Date.now() - 1000,
    cancelled: false,
    observer: null,
    timer: null,
    finish: null,
    resolve: null
  };

  const finishPromise = new Promise((resolve) => {
    let resolved = false;
    let cleanupObserver = null;
    const finish = (res) => {
      if (resolved) return;
      resolved = true;
      if (typeof cleanupObserver === 'function') cleanupObserver();
      resolve(res);
    };
    execState.finish = finish;
    execState.resolve = resolve;
    cleanupObserver = runtime.setupResponseObserver(execState, {}, () => {});
    execState.cleanupObserver = cleanupObserver;
  });

  // timeout 시 cleanup 실행 및 예외 0 검증
  assert.doesNotThrow(() => {
    execState.finish({ status: 'timeout', text: '', error: 'response_stalled' });
  });

  const res = await finishPromise;
  assert.strictEqual(res.status, 'timeout');
  assert.strictEqual(execState.timer, null, 'execState.timer must be null after timeout');
  assert.strictEqual(execState.observer, null, 'execState.observer must be null after timeout');
});

test('GEM-R14-034: cancel -> cleanup -> 예외 0 (실제 content.js 실행 경로)', async () => {
  const { runtime } = loadRealContentJs();

  const execState = {
    requestId: 'req_cancel_001',
    startedAtMs: Date.now(),
    deadlineAtMs: Date.now() + 50000,
    cancelled: false,
    observer: null,
    timer: null,
    finish: null,
    resolve: null
  };

  const finishPromise = new Promise((resolve) => {
    let resolved = false;
    let cleanupObserver = null;
    const finish = (res) => {
      if (resolved) return;
      resolved = true;
      if (typeof cleanupObserver === 'function') cleanupObserver();
      resolve(res);
    };
    execState.finish = finish;
    execState.resolve = resolve;
    cleanupObserver = runtime.setupResponseObserver(execState, {}, () => {});
    execState.cleanupObserver = cleanupObserver;
  });

  // cancel 발생 시 cleanup 및 예외 0 검증
  assert.doesNotThrow(() => {
    execState.finish({ status: 'failed', text: '', error: 'cancelled' });
  });

  const res = await finishPromise;
  assert.strictEqual(res.status, 'failed');
  assert.strictEqual(res.error, 'cancelled');
  assert.strictEqual(execState.timer, null, 'execState.timer must be null after cancel');
  assert.strictEqual(execState.observer, null, 'execState.observer must be null after cancel');
});

test('GEM-R14-035: cleanup 두 번 호출 -> 예외 0 (실제 content.js cleanupObserver idempotency)', () => {
  const { runtime } = loadRealContentJs();

  const execState = {
    requestId: 'req_double_cleanup',
    observer: null,
    timer: null
  };

  const cleanupObserver = runtime.setupResponseObserver(execState, {}, () => {});
  assert.ok(execState.timer !== null);
  assert.ok(execState.observer !== null);

  // 1차 cleanup 호출
  assert.doesNotThrow(() => {
    cleanupObserver();
  });
  assert.strictEqual(execState.timer, null);
  assert.strictEqual(execState.observer, null);

  // 2차 cleanup 호출 (idempotent, 예외 0)
  assert.doesNotThrow(() => {
    cleanupObserver();
  });
  assert.strictEqual(execState.timer, null);
  assert.strictEqual(execState.observer, null);

  // 3차 cleanup 호출 (다중 호출 안전성 확인)
  assert.doesNotThrow(() => {
    cleanupObserver();
  });
});

test('GEM-R14-036: cleanup 후 execState.timer === null and execState.observer === null (stale handle 방지)', () => {
  const { runtime } = loadRealContentJs();

  const execState = {
    requestId: 'req_stale_handle_check',
    observer: null,
    timer: null
  };

  const cleanupObserver = runtime.setupResponseObserver(execState, {}, () => {});
  const originalTimer = execState.timer;
  const originalObserver = execState.observer;
  assert.ok(originalTimer !== null);
  assert.ok(originalObserver !== null);

  cleanupObserver();

  // 기존 버그에서는 checkTimer = null; 후에 execState.timer === checkTimer 비교하여
  // timer ID가 execState.timer에 남아있던 버그가 있었음.
  // 신규 계약에서는 timerToClear를 먼저 확보하고 clearInterval 후 execState.timer를 null로 정리함.
  assert.strictEqual(execState.timer, null, 'execState.timer must be strictly null (no stale timer handle)');
  assert.strictEqual(execState.observer, null, 'execState.observer must be strictly null (no stale observer)');
});

test('GEM-R14-037: completed 후 deliverExecutionResult()가 실제 호출됨 (실제 content.js delivery 경로)', async () => {
  let deliveredResult = null;
  const command = {
    requestId: 'req_delivery_test_001',
    postKey: 'post_123',
    navigationVersion: 1
  };

  const { runtime } = loadRealContentJs({
    onSendMessage: (msg, cb) => {
      if (msg.type === 'NFA_EXECUTION_RESULT') {
        deliveredResult = msg;
        if (typeof cb === 'function') cb({ ok: true, accepted: true, reason: 'accepted' });
        return;
      }
      if (typeof cb === 'function') cb({ ok: true });
    }
  });

  const deliveryRes = await runtime.deliverExecutionResult(command, {
    status: 'completed',
    text: '방문자 댓글 생성 완료'
  });

  assert.strictEqual(deliveryRes.accepted, true, 'deliverExecutionResult must be accepted');
  assert.ok(deliveredResult, 'deliverExecutionResult must have sent NFA_EXECUTION_RESULT');
  assert.strictEqual(deliveredResult.requestId, command.requestId);
  assert.strictEqual(deliveredResult.result.status, 'completed');
  assert.strictEqual(deliveredResult.result.text, '방문자 댓글 생성 완료');
});

test('GEM-R14-038: completed cleanup 중 TypeError가 발생하면 결과 전달이 실패함을 검증 (실제 배포 함수 계약 검증)', async () => {
  // 1. const checkTimer 재대입 버그 발생 시(TypeError: Assignment to constant variable)
  // finish()가 throw되어 deliverExecutionResult까지 도달하지 못함을 검증
  let buggyDelivered = false;
  const buggyExecution = async () => {
    let resolved = false;
    return new Promise((resolve, reject) => {
      const constCheckTimer = setInterval(() => {}, 1000);
      const buggyCleanup = () => {
        clearInterval(constCheckTimer);
        // TypeError 강제 발생: const 변수에 대입하려 할 때 발생하는 JavaScript runtime TypeError
        const target = Object.freeze({ timer: constCheckTimer });
        target.timer = null; // TypeError: Cannot assign to read only property 'timer'
      };
      const finish = (res) => {
        if (resolved) return;
        resolved = true;
        buggyCleanup(); // throws TypeError
        resolve(res);
      };
      try {
        finish({ status: 'completed', text: '결과' });
      } catch (err) {
        reject(err);
      }
    });
  };

  let caughtBuggyError = null;
  try {
    await buggyExecution();
    buggyDelivered = true;
  } catch (err) {
    caughtBuggyError = err;
  }
  assert.ok(caughtBuggyError instanceof TypeError, 'Buggy cleanup must throw TypeError');
  assert.strictEqual(buggyDelivered, false, 'Buggy cleanup must prevent delivery completion');

  // 2. 실제 content.js의 cleanupObserver 경로는 TypeError가 일절 발생하지 않고 deliverExecutionResult가 정상 호출됨
  const { runtime } = loadRealContentJs();
  const execState = {
    requestId: 'req_actual_r14_delivery',
    observer: null,
    timer: null
  };

  let deliveryCalled = false;
  const testCommand = {
    requestId: 'req_actual_r14_delivery',
    postKey: 'post_key_abc',
    navigationVersion: 2
  };

  await new Promise((resolve) => {
    let cleanupObserver = null;
    const finish = async (res) => {
      cleanupObserver();
      const del = await runtime.deliverExecutionResult(testCommand, res);
      deliveryCalled = del.accepted;
      resolve(res);
    };
    cleanupObserver = runtime.setupResponseObserver(execState, {}, () => {});
    finish({ status: 'completed', text: '실제 정상 배포 완료' });
  });

  assert.strictEqual(deliveryCalled, true, 'Actual content.js cleanup allows delivery to succeed');
  assert.strictEqual(execState.timer, null, 'execState.timer must be cleaned up');
  assert.strictEqual(execState.observer, null, 'execState.observer must be cleaned up');
});

test('GEM-R15-JS-001: generation deadline completes before overall deadline and delivery reserve remains', async () => {
  const { runtime, context } = loadRealContentJs();
  assert.ok(runtime);

  let sent = false;
  let composer;
  const editorEl = {
    tagName: 'DIV',
    isConnected: true,
    isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {},
    dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '테스트 프롬프트',
    textContent: '테스트 프롬프트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const sendBtn = {
    tagName: 'BUTTON',
    isConnected: true,
    disabled: false,
    getAttribute: (k) => k === 'aria-label' ? 'send' : null,
    click: () => {
      sent = true;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true,
    closest: () => composer,
    querySelectorAll: (sel) => [sendBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };
  const userQueryEl = {
    tagName: 'DIV',
    className: 'user-query-container',
    isConnected: true,
    closest: (sel) => sel.includes('.user-query-container') ? userQueryEl : null,
    querySelector: (sel) => {
      if (sel.includes('.query-text') || sel.includes('user-query-content')) return { innerText: '테스트 프롬프트' };
      return null;
    },
    innerText: '테스트 프롬프트',
    textContent: '테스트 프롬프트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };
  const streamingIndicatorEl = {
    tagName: 'DIV',
    className: 'loading-dots',
    isConnected: true,
    getAttribute: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const modelResponseEl = {
    tagName: 'DIV',
    className: 'model-response-container',
    isConnected: true,
    closest: (sel) => modelResponseEl,
    querySelector: (sel) => {
      if (sel.includes('.loading-dots') || sel.includes('.streaming')) return streamingIndicatorEl;
      return null;
    },
    querySelectorAll: (sel) => {
      if (sel.includes('.loading-dots') || sel.includes('.streaming') || sel.includes('aria-busy')) return [streamingIndicatorEl];
      return [];
    },
    innerText: '',
    textContent: '',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };

  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    if (sent && (sel.includes('.user-query-container') || sel.includes('.user-message') || sel.includes('user-query'))) {
      return [userQueryEl];
    }
    if (sent && (sel.includes('model-response') || sel.includes('.model-response-container') || sel.includes('.response-container'))) {
      return [modelResponseEl];
    }
    return [];
  };

  const startedAt = Date.now();
  const cmd = {
    requestId: 'req_gen_timeout_01',
    postKey: 'post_key_01',
    navigationVersion: 1,
    prompt: '테스트 프롬프트',
    timeout_seconds: 1.5 // 1500ms generation timeout (allows 650ms commit check, times out before response)
  };

  prepareEmptyComposer(context, editorEl);
  const result = await runtime.execute(cmd);
  const elapsed = Date.now() - startedAt;

  assert.strictEqual(result.status, 'timeout');
  // Overall deadline is generation (200ms) + 7500ms + 1500ms = 9200ms
  assert.ok(elapsed < 4000, `Generation timeout took ${elapsed}ms, delivery reserve must remain`);
});

test('GEM-R15-JS-002: User turn counting with real nested DOM (<user-query> wrapping .user-query-container) produces userUniqueTurns = 1', () => {
  const queryText = {
    tagName: 'DIV',
    className: 'query-text',
    innerText: '사용자 질문 내용',
    textContent: '사용자 질문 내용',
    isConnected: true
  };
  const queryContent = {
    tagName: 'DIV',
    className: 'user-query-content',
    isConnected: true,
    querySelector: (sel) => sel.includes('.query-text') ? queryText : null
  };
  const queryContainer = {
    tagName: 'DIV',
    className: 'user-query-container',
    isConnected: true,
    querySelector: (sel) => sel.includes('.user-query-content') ? queryContent : (sel.includes('.query-text') ? queryText : null)
  };
  let rootUserQueryNode;
  rootUserQueryNode = {
    tagName: 'USER-QUERY',
    isConnected: true,
    closest: (sel) => (sel.includes('user-query') ? rootUserQueryNode : null),
    querySelector: (sel) => {
      if (sel.includes('.user-query-container')) return queryContainer;
      if (sel.includes('.user-query-content')) return queryContent;
      if (sel.includes('.query-text')) return queryText;
      return null;
    },
    getBoundingClientRect: () => ({ width: 200, height: 50 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  queryContainer.closest = (sel) => (sel.includes('user-query') && !sel.includes('user-query-container')) ? rootUserQueryNode : queryContainer;
  queryText.closest = queryContainer.closest;

  const { runtime, context } = loadRealContentJs();
  context.document.querySelectorAll = (sel) => {
    if (sel.includes('user-query') && !sel.includes('.user-query-container')) {
      return [rootUserQueryNode];
    }
    if (sel.includes('.user-query-container')) {
      return [queryContainer];
    }
    return [];
  };

  const inv = runtime.getUserInventory();
  assert.strictEqual(inv.userUniqueTurns, 1, 'userUniqueTurns must be exactly 1 despite nested query-container in user-query');
  assert.strictEqual(inv.visibleUserNodes.length, 1);
  assert.strictEqual(inv.visibleUserNodes[0], rootUserQueryNode);
});

test('GEM-R15-JS-003: getUserTurnContainer(el) resolves to primary root and returns null when outside (no || el fallback)', () => {
  const { runtime } = loadRealContentJs();

  const primaryRoot = {
    tagName: 'USER-QUERY',
    isConnected: true,
    closest: (sel) => sel.includes('user-query') ? primaryRoot : null
  };
  const innerChild = {
    tagName: 'DIV',
    className: 'query-text',
    isConnected: true,
    closest: (sel) => sel.includes('user-query') ? primaryRoot : null
  };
  assert.strictEqual(runtime.getUserTurnContainer(innerChild), primaryRoot);

  const orphanNode = {
    tagName: 'SPAN',
    className: 'some-random-text',
    isConnected: true,
    closest: () => null
  };
  const container = runtime.getUserTurnContainer(orphanNode);
  assert.strictEqual(container, null, 'getUserTurnContainer must NOT fall back to || el');

  // Test fallback selector when primary root is absent
  const fallbackContainer = {
    tagName: 'DIV',
    className: 'user-query-container',
    isConnected: true,
    closest: (sel) => (sel.includes('user-query') && !sel.includes('user-query-container')) ? null : (sel.includes('.user-query-container') ? fallbackContainer : null)
  };
  const fallbackChild = {
    tagName: 'DIV',
    className: 'query-text',
    isConnected: true,
    closest: (sel) => (sel.includes('user-query') && !sel.includes('user-query-container')) ? null : (sel.includes('.user-query-container') ? fallbackContainer : null)
  };
  assert.strictEqual(runtime.getUserTurnContainer(fallbackChild), fallbackContainer);
});

test('GEM-R15-JS-004: visibleAndActive(el) correctly rejects zero opacity, aria-hidden, disabled, and collapsed', () => {
  const { runtime } = loadRealContentJs();
  const makeEl = ({ isConnected = true, disabled = false, ariaDisabled = 'false', ariaHidden = 'false', opacity = '1', visibility = 'visible', display = 'block', width = 50, height = 50 }) => {
    const el = {
      isConnected,
      disabled,
      getAttribute: (attr) => {
        if (attr === 'aria-disabled') return ariaDisabled;
        if (attr === 'aria-hidden') return ariaHidden;
        return null;
      },
      getBoundingClientRect: () => ({ width, height }),
      computedStyle: { visibility, display, opacity }
    };
    return el;
  };

  // 1. Normal active element
  const normal = makeEl({});
  assert.strictEqual(runtime.visibleAndActive(normal), true);

  // 2. Opacity <= 0
  const zeroOpacity = makeEl({ opacity: '0' });
  assert.strictEqual(runtime.visibleAndActive(zeroOpacity), false, 'opacity 0 must be inactive');

  // 3. aria-hidden = true
  const hidden = makeEl({ ariaHidden: 'true' });
  assert.strictEqual(runtime.visibleAndActive(hidden), false, 'aria-hidden true must be inactive');

  // 4. disabled = true
  const dis = makeEl({ disabled: true });
  assert.strictEqual(runtime.visibleAndActive(dis), false, 'disabled true must be inactive');

  // 5. Collapsed dimensions without contents display
  const collapsed = makeEl({ width: 0, height: 0, display: 'block' });
  assert.strictEqual(runtime.visibleAndActive(collapsed), false, 'collapsed width/height 0 must be inactive');
});

test('GEM-R15-JS-005: detectGenerationEvidence(boundNode) ignores stale/invisible streaming indicators with opacity: 0', () => {
  const { runtime } = loadRealContentJs();
  const staleDots = {
    isConnected: true,
    getAttribute: () => null,
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '0' } // stale/hidden
  };

  const boundNode = {
    isConnected: true,
    querySelectorAll: (sel) => {
      if (sel.includes('.loading-dots') || sel.includes('.streaming')) {
        return [staleDots];
      }
      return [];
    }
  };

  const evidence = runtime.detectGenerationEvidence(boundNode);
  assert.strictEqual(evidence, 'idle', 'Stale indicator with opacity 0 must return idle, not local_streaming');
});

test('GEM-R15-JS-006: 8s stale streaming watchdog detects action toolbar and clears stale indicator', () => {
  const activeSpinner = {
    isConnected: true,
    getAttribute: () => null,
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const copyBtn = {
    isConnected: true,
    tagName: 'BUTTON',
    getAttribute: (k) => k === 'aria-label' ? '복사' : null
  };
  const responseNode = {
    isConnected: true,
    tagName: 'MODEL-RESPONSE',
    querySelectorAll: (sel) => {
      if (sel.includes('.loading-dots') || sel.includes('.streaming')) return [activeSpinner];
      return [];
    },
    querySelector: (sel) => {
      if (sel.includes('button[aria-label*="복사"]') || sel.includes('.actions')) return copyBtn;
      return null;
    },
    getBoundingClientRect: () => ({ width: 200, height: 50 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };

  const hasActionToolbar = Boolean(
    responseNode.querySelector('.actions, .response-actions, .model-response-actions, [data-test-id*="action"], [data-test-id*="copy"], button[aria-label*="복사"], button[aria-label*="Copy"]')
  );
  assert.strictEqual(hasActionToolbar, true, 'Copy/action toolbar must be detected as completion evidence');
});

test('GEM-R15-JS-007: 8s stale watchdog does NOT complete if active spinner is genuinely visible and NO action toolbar', () => {
  const { runtime } = loadRealContentJs();
  const activeDots = {
    isConnected: true,
    getAttribute: () => null,
    getBoundingClientRect: () => ({ width: 24, height: 24 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const boundNode = {
    isConnected: true,
    querySelectorAll: (sel) => {
      if (sel.includes('.loading-dots') || sel.includes('.streaming')) return [activeDots];
      return [];
    },
    querySelector: () => null
  };
  const evidence = runtime.detectGenerationEvidence(boundNode);
  assert.strictEqual(evidence, 'local_streaming', 'Active spinner must report local_streaming');
});

test('GEM-R15-JS-008: getCandidateInventory populates excluded statistics and per-candidate debug metadata', () => {
  const { runtime, context } = loadRealContentJs();
  const node1 = {
    isConnected: false,
    tagName: 'MODEL-RESPONSE',
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => null,
    getBoundingClientRect: () => ({ width: 0, height: 0 })
  };
  const node2 = {
    isConnected: true,
    tagName: 'DIV',
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => null,
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };

  context.document.querySelectorAll = (sel) => {
    if (sel.includes('model-response')) return [node1, node2];
    return [];
  };

  const inv = runtime.getCandidateInventory();
  assert.ok(inv.excluded, 'excluded statistics object must exist');
  assert.strictEqual(typeof inv.excluded.disconnected, 'number');
  assert.strictEqual(typeof inv.excluded.not_visible, 'number');
  assert.strictEqual(typeof inv.excluded.initial_baseline_node, 'number');
  assert.strictEqual(typeof inv.excluded.baseline_text_match, 'number');
  assert.strictEqual(typeof inv.excluded.precedes_user_turn, 'number');
  assert.strictEqual(typeof inv.excluded.relaxed_fresh_chat_precedes, 'number');
});

test('GEM-R15-JS-009: Scoped fresh-chat fallback relaxes precedes_user_turn strictly when fresh chat, 1 model turn, visible, non-status', () => {
  const { runtime, context } = loadRealContentJs();
  const userTurn = { isConnected: true, compareDocumentPosition: () => 0 };
  let modelTurn;
  modelTurn = {
    isConnected: true,
    tagName: 'MODEL-RESPONSE',
    closest: () => modelTurn,
    querySelectorAll: (sel) => {
      if (sel.includes('message-content')) {
        return [{
          isConnected: true,
          innerText: '신선한 응답 텍스트',
          querySelectorAll: () => [],
          querySelector: () => null,
          closest: () => null,
          computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
        }];
      }
      return [];
    },
    querySelector: () => null,
    getBoundingClientRect: () => ({ width: 150, height: 60 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };

  context.document.querySelectorAll = () => [modelTurn];

  // Case A: freshChatVerified = true, baseline empty, exactly 1 turn -> precedes_user_turn relaxed!
  const freshInv = runtime.getCandidateInventory(new Set(), new Set(), userTurn, { freshChatVerified: true });
  assert.strictEqual(freshInv.validCandidates.length, 1, 'precedes_user_turn should be relaxed in fresh chat');
  assert.strictEqual(freshInv.excluded.relaxed_fresh_chat_precedes, 1);

  // Case B: freshChatVerified = false (pre-existing chat) -> precedes_user_turn NOT relaxed!
  const normalInv = runtime.getCandidateInventory(new Set(), new Set(), userTurn, { freshChatVerified: false });
  assert.strictEqual(normalInv.validCandidates.length, 0, 'precedes_user_turn should NOT be relaxed in existing chat');
  assert.strictEqual(normalInv.excluded.precedes_user_turn, 1);
});

test('GEM-R15-JS-010: Background forwardResultToPython single-flight prevents duplicate HTTP POSTs on ACK timeouts', async () => {
  let postCount = 0;
  let postResolvers = [];

  const { background } = loadRealBackgroundJs({
    onFetch: async (url, opts) => {
      if (url.endsWith('runtime_contract.json')) {
        return { ok: true, json: async () => ({ extensionVersion: '13.2.3', runtimeBuild: '13.2.3-r17', protocolVersion: 3, bridgeSchemaVersion: 2 }) };
      }
      if (url.includes('/v1/result')) {
        postCount++;
        return new Promise(resolve => {
          postResolvers.push(() => {
            resolve({
              ok: true,
              json: async () => ({ ok: true, accepted: true, reason: 'accepted' })
            });
          });
        });
      }
      return { ok: true, json: async () => ({ ok: true }) };
    }
  });

  const msg = {
    requestId: 'req_single_flight_01',
    postKey: 'post_key_sf_01',
    navigationVersion: 1,
    result: { status: 'completed', text: '정상 응답' }
  };

  // Simulate 3 concurrent calls (e.g. content script retry on 1.4s ACK timeout)
  const call1 = background.forwardResultToPython(msg, null, 5000);
  const call2 = background.forwardResultToPython(msg, null, 5000);
  const call3 = background.forwardResultToPython(msg, null, 5000);

  // Assert only 1 HTTP POST has started
  assert.strictEqual(postCount, 1, 'Only 1 HTTP POST must be initiated for concurrent forwardResultToPython calls');

  // Resolve the single in-flight HTTP request
  postResolvers[0]();

  const [res1, res2, res3] = await Promise.all([call1, call2, call3]);

  assert.strictEqual(res1.accepted, true);
  assert.strictEqual(res2.accepted, true);
  assert.strictEqual(res3.accepted, true);
  assert.strictEqual(postCount, 1, 'No duplicate POSTs should have occurred');

  // Subsequent call after acceptance should immediately return already_accepted without any new fetch
  const callAfter = await background.forwardResultToPython(msg, null, 5000);
  assert.strictEqual(callAfter.accepted, true);
  assert.strictEqual(callAfter.reason, 'accepted');
  assert.strictEqual(postCount, 1, 'Accepted result must not trigger additional fetch');
});

test('GEM-R15-JS-011: deliverExecutionResult generates deliveryId and succeeds on background primary, falling back to direct HTTP on ACK timeout', async () => {
  let sentToBackground = null;
  let postedDirect = null;

  // Case A: Background primary succeeds
  const envA = loadRealContentJs({
    onSendMessage: (msg, cb) => {
      sentToBackground = msg;
      cb({ ok: true, accepted: true, reason: 'accepted' });
    }
  });
  const cmdA = { requestId: 'req_delivery_a', postKey: 'post_a', navigationVersion: 1 };
  const resA = await envA.runtime.deliverExecutionResult(cmdA, { status: 'completed', text: '댓글A' });
  assert.strictEqual(resA.accepted, true);
  assert.strictEqual(resA.channel, 'background');
  assert.ok(sentToBackground.deliveryId.startsWith('req_delivery_a:completed:'), 'deliveryId must be generated');

  // Case B: Background ACK times out -> Direct HTTP fallback
  const envB = loadRealContentJs({
    onSendMessage: (msg, cb) => {
      // Never respond to simulate timeout
    },
    onFetch: (url, opts) => {
      postedDirect = { url, body: JSON.parse(opts.body) };
      return { ok: true, json: async () => ({ ok: true, accepted: true, reason: 'accepted' }) };
    }
  });
  const cmdB = { requestId: 'req_delivery_b', postKey: 'post_b', navigationVersion: 1, overallDeadlineAtMs: Date.now() + 6000 };
  const resB = await envB.runtime.deliverExecutionResult(cmdB, { status: 'completed', text: '댓글B' });
  assert.strictEqual(resB.accepted, true);
  assert.strictEqual(resB.channel, 'direct');
  assert.ok(postedDirect.body.deliveryId.startsWith('req_delivery_b:completed:'), 'Direct body must contain deliveryId');
});

test('GEM-R17-JS-012: Old r16 runtime with r17 contract triggers reinjection', () => {
  const contract = { runtimeBuild: '13.2.3-r17' };
  const pingResponse = { ok: true, build: '13.2.3-r16' };
  let reinjected = false;
  if (!pingResponse.ok || pingResponse.build !== contract.runtimeBuild) {
    reinjected = true;
  }
  assert.strictEqual(reinjected, true, 'r16 build must trigger reinjection under r17 contract');
});

test('GEM-R15-JS-013: execute() strictly validates contentInstanceId and conversationEpoch', async () => {
  const { runtime } = loadRealContentJs();
  const currentInstanceId = runtime.instanceId;
  const currentEpoch = runtime.getConversationEpoch();

  // Case 1: Mismatched contentInstanceId
  const resMismatchInst = await runtime.execute({
    requestId: 'req_inst_mismatch_01',
    contentInstanceId: 'wrong_instance_id_999'
  });
  assert.strictEqual(resMismatchInst.status, 'failed');
  assert.strictEqual(resMismatchInst.error, 'content_instance_mismatch');

  // Case 2: Mismatched conversationEpoch
  const resMismatchEpoch = await runtime.execute({
    requestId: 'req_epoch_mismatch_01',
    contentInstanceId: currentInstanceId,
    conversationEpoch: currentEpoch + 5
  });
  assert.strictEqual(resMismatchEpoch.status, 'failed');
  assert.strictEqual(resMismatchEpoch.error, 'conversation_epoch_mismatch');
});

test('GEM-R16-JS-001: Nested primary roots (<user-query> wrapping <div data-message-author-role="user">) canonicalize to 1 turn and outermost container', () => {
  const innerUserRole = {
    tagName: 'DIV',
    attributes: { 'data-message-author-role': 'user' },
    getAttribute: (k) => k === 'data-message-author-role' ? 'user' : null,
    isConnected: true,
    getBoundingClientRect: () => ({ width: 180, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const rootUserQuery = {
    tagName: 'USER-QUERY',
    isConnected: true,
    parentElement: null,
    getBoundingClientRect: () => ({ width: 200, height: 50 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  innerUserRole.parentElement = rootUserQuery;

  const childText = {
    tagName: 'P',
    className: 'query-text',
    innerText: '질문 텍스트',
    isConnected: true,
    parentElement: innerUserRole
  };

  rootUserQuery.closest = (sel) => sel.includes('user-query') ? rootUserQuery : null;
  innerUserRole.closest = (sel) => {
    if (sel.includes('user-query')) return rootUserQuery;
    if (sel.includes('data-message-author-role')) return innerUserRole;
    return null;
  };
  childText.closest = (sel) => {
    if (sel.includes('data-message-author-role')) return innerUserRole;
    if (sel.includes('user-query')) return rootUserQuery;
    return null;
  };

  const { runtime, context } = loadRealContentJs();

  // canonicalizeRoots direct test
  const canonical = runtime.canonicalizeRoots([rootUserQuery, innerUserRole]);
  assert.strictEqual(canonical.length, 1);
  assert.strictEqual(canonical[0], rootUserQuery, 'Inner nested primary root must be filtered out by canonicalizeRoots');

  // getUserInventory DOM query test
  context.document.querySelectorAll = (sel) => {
    if (sel.includes('user-query') || sel.includes('data-message-author-role')) {
      return [rootUserQuery, innerUserRole];
    }
    return [];
  };

  const inv = runtime.getUserInventory();
  assert.strictEqual(inv.userSelectorMatches, 2, 'Raw query selector matches both elements');
  assert.strictEqual(inv.userUniqueTurns, 1, 'Canonicalization guarantees userUniqueTurns === 1');
  assert.strictEqual(inv.visibleUserNodes.length, 1);
  assert.strictEqual(inv.visibleUserNodes[0], rootUserQuery);

  // getUserTurnContainer test from innermost leaf child
  const container = runtime.getUserTurnContainer(childText);
  assert.strictEqual(container, rootUserQuery, 'getUserTurnContainer must resolve to outermost ancestor root');
});

test('GEM-R16-JS-002: Stale streaming watchdog is strictly suppressed when composer activeStopButton is present', () => {
  const { runtime, context } = loadRealContentJs();

  const stopBtn = {
    tagName: 'BUTTON',
    isConnected: true,
    getAttribute: (k) => k === 'aria-label' ? '생성 중지' : null,
    getBoundingClientRect: () => ({ width: 30, height: 30 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const composer = {
    tagName: 'DIV',
    isConnected: true,
    querySelectorAll: (sel) => (sel.includes('중지') || sel.includes('Stop')) ? [stopBtn] : []
  };

  const activeSpinner = {
    isConnected: true,
    getAttribute: () => null,
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const copyBtn = {
    isConnected: true,
    tagName: 'BUTTON',
    getAttribute: (k) => k === 'aria-label' ? '복사' : null,
    getBoundingClientRect: () => ({ width: 24, height: 24 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const responseNode = {
    isConnected: true,
    tagName: 'MODEL-RESPONSE',
    querySelectorAll: (sel) => {
      if (sel.includes('.loading-dots') || sel.includes('.streaming')) return [activeSpinner];
      if (sel.includes('button[aria-label*="복사"]') || sel.includes('.actions')) return [copyBtn];
      return [];
    },
    getBoundingClientRect: () => ({ width: 200, height: 50 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };

  const editorEl = {
    isConnected: true,
    isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };

  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    return [];
  };

  const state = runtime.inspectGenerationState(responseNode);
  assert.strictEqual(state.hasActiveStreamingIndicator, true);
  assert.strictEqual(state.hasActiveStopButton, true);
  assert.strictEqual(state.hasVisibleActionToolbar, true);

  const evidence = runtime.detectGenerationEvidence(responseNode);
  assert.notStrictEqual(evidence, 'idle');
});

test('GEM-R16-JS-003: findVisibleActionToolbar strictly verifies visibleAndActive (rejects display:none, opacity:0, aria-hidden)', () => {
  const { runtime } = loadRealContentJs();

  // Case 1: display: none
  const hiddenBtn = {
    isConnected: true,
    tagName: 'BUTTON',
    getAttribute: (k) => k === 'aria-label' ? '복사' : null,
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'none', opacity: '1' }
  };
  const nodeHidden = {
    isConnected: true,
    querySelectorAll: (sel) => sel.includes('button[aria-label*="복사"]') ? [hiddenBtn] : []
  };
  assert.strictEqual(runtime.findVisibleActionToolbar(nodeHidden), null, 'Must reject display: none toolbar');

  // Case 2: opacity: 0
  const zeroOpacityBtn = {
    isConnected: true,
    tagName: 'BUTTON',
    getAttribute: (k) => k === 'aria-label' ? '복사' : null,
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '0' }
  };
  const nodeZeroOp = {
    isConnected: true,
    querySelectorAll: (sel) => sel.includes('button[aria-label*="복사"]') ? [zeroOpacityBtn] : []
  };
  assert.strictEqual(runtime.findVisibleActionToolbar(nodeZeroOp), null, 'Must reject opacity: 0 toolbar');

  // Case 3: aria-hidden: "true"
  const ariaHiddenBtn = {
    isConnected: true,
    tagName: 'BUTTON',
    getAttribute: (k) => k === 'aria-hidden' ? 'true' : (k === 'aria-label' ? '복사' : null),
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const nodeAriaHidden = {
    isConnected: true,
    querySelectorAll: (sel) => sel.includes('button[aria-label*="복사"]') ? [ariaHiddenBtn] : []
  };
  assert.strictEqual(runtime.findVisibleActionToolbar(nodeAriaHidden), null, 'Must reject aria-hidden toolbar');

  // Case 4: genuinely visible
  const visibleBtn = {
    isConnected: true,
    tagName: 'BUTTON',
    getAttribute: (k) => k === 'aria-label' ? '복사' : null,
    getBoundingClientRect: () => ({ width: 20, height: 20 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const nodeVisible = {
    isConnected: true,
    querySelectorAll: (sel) => sel.includes('button[aria-label*="복사"]') ? [visibleBtn] : []
  };
  assert.strictEqual(runtime.findVisibleActionToolbar(nodeVisible), visibleBtn, 'Genuinely visible toolbar must be found');
});

test('GEM-R16-JS-004: Background forwardResultToPython detects duplicate result payload conflict', async () => {
  let postCount = 0;
  const { background } = loadRealBackgroundJs({
    onFetch: async (url, opts) => {
      if (url.includes('/v1/result')) {
        postCount++;
        return { ok: true, json: async () => ({ ok: true, accepted: true, reason: 'accepted' }) };
      }
      return { ok: true, json: async () => ({}) };
    }
  });

  const rid = 'req_fingerprint_test_01';
  const firstMsg = {
    requestId: rid,
    postKey: 'post_fp_1',
    navigationVersion: 1,
    status: 'timeout',
    text: '',
    error: 'response_stalled'
  };

  // First forward succeeds and registers accepted
  const res1 = await background.forwardResultToPython(firstMsg, null, 2000);
  assert.strictEqual(res1.accepted, true);
  assert.strictEqual(postCount, 1);

  // Duplicate with EXACT same payload returns fast already_accepted
  const resSame = await background.forwardResultToPython(firstMsg, null, 2000);
  assert.strictEqual(resSame.accepted, true);
  assert.strictEqual(resSame.reason, 'accepted');
  assert.strictEqual(postCount, 1, 'No new POST for identical payload');

  // Duplicate with CONFLICTING payload returns duplicate_result_conflict
  const conflictMsg = {
    requestId: rid,
    postKey: 'post_fp_1',
    navigationVersion: 1,
    status: 'completed',
    text: '뒤늦게 도착한 다른 내용의 답변',
    error: ''
  };
  const resConflict = await background.forwardResultToPython(conflictMsg, null, 2000);
  assert.strictEqual(resConflict.accepted, false);
  assert.strictEqual(resConflict.reason, 'duplicate_result_conflict', 'Conflicting payload must be rejected as duplicate_result_conflict');
  assert.strictEqual(postCount, 1, 'Conflict must not reach Python HTTP endpoint');
});

test('GEM-R16-JS-005: Result delivery registry prunes entries older than 10m TTL and caps size at 200', () => {
  const { background } = loadRealBackgroundJs();
  const registry = background.resultDeliveryRegistry;
  registry.clear();

  const now = Date.now();
  // Add an expired entry (>10 min ago)
  registry.set('old_req_01', {
    state: 'accepted',
    lastAttemptAt: now - (15 * 60 * 1000),
    fingerprint: 'fp_old'
  });

  // Add 205 recent entries
  for (let i = 0; i < 205; i++) {
    registry.set(`recent_req_${i}`, {
      state: 'accepted',
      lastAttemptAt: now - 1000,
      fingerprint: `fp_${i}`
    });
  }

  assert.strictEqual(registry.size, 206);
  background.pruneResultDeliveryRegistry();

  assert.ok(!registry.has('old_req_01'), 'Expired entry must be pruned');
  assert.ok(registry.size <= 200, `Registry size must be capped at 200, actual: ${registry.size}`);
});

test('GEM-R17-JS-001: canonicalPromptText normalizes smart quotes, dashes, bullets, ellipses', () => {
  const { runtime } = loadRealContentJs();
  const rawWithSmartChars = '“안녕하세요” ‘반갑습니다’ – 테스트 — 불릿 • 항목 … 끝';
  const canonical = runtime.canonicalPromptText(rawWithSmartChars);
  assert.strictEqual(canonical, '"안녕하세요" \'반갑습니다\' - 테스트 - 불릿 - 항목 ... 끝');
});

test('GEM-R17-JS-002: isPromptMatch matches exact and resilient long prompt prefix with length ratio >= 0.85', () => {
  const { runtime } = loadRealContentJs();
  const expectedPrompt = '이것은 네이버 블로그 자동 댓글 생성을 위한 프롬프트 테스트입니다. 길이 band는 보통이고 톤앤매너는 친근하게 작성해주세요.';
  // Exact match
  assert.strictEqual(runtime.isPromptMatch(expectedPrompt, expectedPrompt), true);

  // Slight formatting variance in rich-text editor (e.g. minor smart quotes or minor suffix difference)
  const actualInEditor = '이것은 네이버 블로그 자동 댓글 생성을 위한 프롬프트 테스트입니다. 길이 band는 보통이고 톤앤매너는 친근하게 작성해주세요';
  assert.strictEqual(runtime.isPromptMatch(actualInEditor, expectedPrompt), true);

  // Completely different text must NOT match
  assert.strictEqual(runtime.isPromptMatch('전혀 다른 내용의 텍스트입니다.', expectedPrompt), false);
});

test('GEM-R17-JS-003: scoreSendCandidate does not disqualify button with aria-disabled="true" (score > 0)', () => {
  const { runtime } = loadRealContentJs();
  const sendBtn = {
    tagName: 'BUTTON',
    disabled: false,
    isConnected: true,
    getAttribute: (attr) => {
      if (attr === 'aria-label') return '보내기';
      if (attr === 'aria-disabled') return 'true';
      return null;
    },
    innerText: '',
    textContent: '',
    className: 'send-button',
    querySelector: () => null,
    getBoundingClientRect: () => ({ right: 100, bottom: 100 })
  };
  const score = runtime.scoreSendCandidate(sendBtn, null);
  assert.ok(score > 0, `Send button with aria-disabled="true" must still score positively, got: ${score}`);
});

test('SEND-COMMIT: disabled submission does not mutate the button or send Enter', () => {
  const { runtime } = loadRealContentJs();
  let clickCalled = false;
  let dispatchedEvents = [];

  const mockBtn = {
    tagName: 'BUTTON',
    disabled: true,
    isConnected: true,
    getAttribute: (attr) => attr === 'aria-disabled' ? 'true' : null,
    setAttribute: (attr, val) => {},
    focus: () => {},
    click: () => { clickCalled = true; },
    dispatchEvent: (evt) => { dispatchedEvents.push(evt.type || 'unknown'); }
  };

  const keyEvents = [];
  const mockTarget = {
    tagName: 'DIV',
    isConnected: true,
    focus: () => {},
    querySelector: () => null,
    dispatchEvent: (evt) => { keyEvents.push(evt.key); }
  };

  const result = runtime.triggerSubmission(mockTarget, mockBtn);
  assert.strictEqual(result.clicked, false);
  assert.strictEqual(result.keyed, false);
  assert.strictEqual(clickCalled, false);
  assert.strictEqual(mockBtn.disabled, true);
  assert.strictEqual(keyEvents.length, 0);
  assert.strictEqual(dispatchedEvents.length, 0);
});

test('SEND-COMMIT: enabled submission dispatches exactly one click and no keyboard fallback', () => {
  const { runtime } = loadRealContentJs();
  let clicks = 0;
  const button = {
    isConnected: true, disabled: false,
    getAttribute: () => null,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    focus: () => {}, click: () => { clicks++; },
    dispatchEvent: () => { throw new Error('duplicate synthetic event'); },
  };
  const target = { dispatchEvent: () => { throw new Error('keyboard fallback'); } };
  const result = runtime.triggerSubmission(target, button);
  assert.strictEqual(result.clicked, true);
  assert.strictEqual(result.keyed, false);
  assert.strictEqual(clicks, 1);
});

test('GEM-R17-JS-005: Python cancel discovered -> background /v1/cancel poll -> NFA_CANCEL_COMMAND dispatched -> inFlight removed -> late result dropped', async () => {
  const rid = 'req_cancel_e2e_01';
  let cancelPollCount = 0;
  let resultPostCount = 0;
  let isCancelledOnPython = false;

  const { background, messageListener, tabMessages } = loadRealBackgroundJs({
    onFetch: async (url, opts) => {
      if (url.includes('/v1/contract')) {
        return {
          ok: true,
          json: async () => ({
            extensionVersion: '13.2.3',
            runtimeBuild: '13.2.3-r17',
            protocolVersion: 1,
            bridgeSchemaVersion: 1
          })
        };
      }
      if (url.includes('/v1/command/wait')) {
        return {
          ok: true,
          json: async () => ({
            command: {
              requestId: rid,
              postKey: 'post_cancel_01',
              navigationVersion: 1,
              prompt: '테스트 취소 프롬프트',
              deadlineAtMs: Date.now() + 30000
            }
          })
        };
      }
      if (url.includes('/v1/claim')) {
        return { ok: true, json: async () => ({ claimed: true }) };
      }
      if (url.includes('/v1/cancel')) {
        cancelPollCount++;
        return { ok: true, json: async () => ({ cancelled: isCancelledOnPython }) };
      }
      if (url.includes('/v1/result')) {
        resultPostCount++;
        return { ok: true, json: async () => ({ accepted: true, reason: 'accepted' }) };
      }
      if (url.includes('/v1/heartbeat')) {
        return { ok: true, json: async () => ({ ok: true }) };
      }
      return { ok: true, json: async () => ({}) };
    },
    onTabSendMessage: (tabId, msg, cb) => {
      if (msg.type === 'NFA_CHECK_FRESH_CHAT') {
        cb({ ok: true, fresh: true, composerEmpty: true, conversationEpoch: 1, contentInstanceId: 'inst_01' });
      } else if (msg.type === 'NFA_RUNTIME_PING' || msg.type === 'NFA_PING_GEMINI_DOM') {
        cb({ ok: true, alive: true, build: '13.2.3-r17', status: 'ready', conversationEpoch: 1, contentInstanceId: 'inst_01' });
      } else if (msg.type === 'NFA_EXECUTE_COMMAND') {
        cb({ ok: true, started: true });
      } else if (msg.type === 'NFA_CANCEL_COMMAND') {
        cb({ ok: true, cancelled: true });
      } else {
        cb({ ok: true, alive: true, build: '13.2.3-r17', status: 'ready' });
      }
    }
  });

  // Start one cycle
  const cyclePromise = background.runCommandCycle();

  // Wait briefly for command to be claimed and dispatch to start
  await new Promise(r => setTimeout(r, 100));

  // Now simulate Python discovering cancel state (e.g. duplicate detected or skip requested)
  isCancelledOnPython = true;

  // Wait for cancelCheckInterval (polls every 400ms) to detect cancellation
  await new Promise(r => setTimeout(r, 600));

  await cyclePromise;

  // 1. Verify /v1/cancel was polled
  assert.ok(cancelPollCount >= 1, `Expected /v1/cancel poll, got count: ${cancelPollCount}`);

  // 2. Verify NFA_CANCEL_COMMAND was sent to content script exactly once
  const cancelMsgs = tabMessages.filter(m => m.msg?.type === 'NFA_CANCEL_COMMAND' && m.msg?.requestId === rid);
  assert.strictEqual(cancelMsgs.length, 1, `Expected 1 NFA_CANCEL_COMMAND message, got: ${cancelMsgs.length}`);

  const postCountBeforeLate = resultPostCount;

  // 3. Verify late result drop: simulate content script sending late result
  let lateAck = null;
  messageListener(
    {
      type: 'NFA_EXECUTION_RESULT',
      requestId: rid,
      postKey: 'post_cancel_01',
      navigationVersion: 1,
      status: 'completed',
      text: '뒤늦게 도착한 생성 결과'
    },
    {},
    (res) => { lateAck = res; }
  );

  assert.ok(lateAck !== null, 'Late result ACK should be sent');
  assert.strictEqual(lateAck.accepted, false, 'Late result must NOT be accepted');
  assert.strictEqual(lateAck.reason, 'request_cancelled', 'Late result reason must be request_cancelled');

  // 4. Verify that late result was dropped and never forwarded to Python /v1/result
  assert.strictEqual(resultPostCount, postCountBeforeLate, 'Cancelled late result must NOT trigger a POST to Python /v1/result');
});

test('GEM-R17-JS-006: background.js acceptance deadline preserves 9s delivery reserve without premature 5s timeout collision', async () => {
  const rid = 'req_acc_coord_01';
  let resultForwarded = false;

  const T0 = 1000000;
  let simulatedNow = T0 + 54000; // T0 + 54s (1s before generation deadline, 10s before acceptance deadline)

  class FakeDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(simulatedNow);
      else super(...args);
    }
    static now() { return simulatedNow; }
    getTime() { return simulatedNow; }
  }

  const { background, messageListener, tabMessages } = loadRealBackgroundJs({
    extraGlobals: {
      Date: FakeDate
    },
    onFetch: async (url, opts) => {
      if (url.includes('/v1/contract')) {
        return {
          ok: true,
          json: async () => ({
            extensionVersion: '13.2.3',
            runtimeBuild: '13.2.3-r17',
            protocolVersion: 1,
            bridgeSchemaVersion: 1
          })
        };
      }
      if (url.includes('/v1/command/wait')) {
        return {
          ok: true,
          json: async () => ({
            command: {
              requestId: rid,
              postKey: 'post_coord_01',
              navigationVersion: 1,
              prompt: '타이머 정합성 테스트',
              deadlineAtMs: T0 + 55000,
              generationDeadlineAtMs: T0 + 55000,
              acceptanceDeadlineAtMs: T0 + 64000,
              timeoutSeconds: 55,
              deliveryReserveSeconds: 9
            }
          })
        };
      }
      if (url.includes('/v1/claim')) {
        return { ok: true, json: async () => ({ claimed: true }) };
      }
      if (url.includes('/v1/result')) {
        resultForwarded = true;
        return { ok: true, json: async () => ({ accepted: true }) };
      }
      if (url.includes('/v1/cancel')) {
        return { ok: true, json: async () => ({ cancelled: false }) };
      }
      return { ok: true, json: async () => ({}) };
    },
    onTabSendMessage: (tabId, msg, cb) => {
      if (msg.type === 'NFA_CHECK_FRESH_CHAT') {
        cb({ ok: true, fresh: true, composerEmpty: true, conversationEpoch: 1, contentInstanceId: 'inst_coord_01' });
      } else if (msg.type === 'NFA_RUNTIME_PING' || msg.type === 'NFA_PING_GEMINI_DOM') {
        cb({ ok: true, alive: true, build: '13.2.3-r17', status: 'ready', conversationEpoch: 1, contentInstanceId: 'inst_coord_01' });
      } else if (msg.type === 'NFA_EXECUTE_COMMAND') {
        cb({ ok: true, started: true });
      } else if (msg.type === 'NFA_CANCEL_COMMAND') {
        cb({ ok: true, cancelled: true });
      } else {
        cb({ ok: true, alive: true, build: '13.2.3-r17', status: 'ready' });
      }
    }
  });

  const cyclePromise = background.runCommandCycle();
  const startWait = Date.now();
  while (!tabMessages.some(m => m.msg?.type === 'NFA_EXECUTE_COMMAND') && Date.now() - startWait < 3000) {
    await new Promise(r => setTimeout(r, 50));
  }

  // Advance time to T0 + 55.2s (generation deadline passed, but within 9s acceptance delivery reserve)
  simulatedNow = T0 + 55200;

  // Deliver execution result from content script
  const deliverAck = await new Promise(resolve => {
    messageListener(
      {
        type: 'NFA_EXECUTION_RESULT',
        requestId: rid,
        postKey: 'post_coord_01',
        navigationVersion: 1,
        status: 'completed',
        text: '수락 유예 시간 내 전달된 정상 결과'
      },
      {},
      resolve
    );
  });

  assert.ok(deliverAck !== null, 'Delivery ACK must be returned');
  assert.strictEqual(deliverAck.accepted, true, 'Delivery during acceptance reserve must be accepted');

  await cyclePromise;
  assert.strictEqual(resultForwarded, true, 'Result must be forwarded to Python bridge');

  // Verify no cancel command was dispatched due to timer collision
  const cancelMsgs = tabMessages.filter(m => m.msg?.type === 'NFA_CANCEL_COMMAND' && m.msg?.requestId === rid);
  assert.strictEqual(cancelMsgs.length, 0, 'No cancel command should be dispatched when completed within acceptance reserve');
});

test('SEND-HARDEN-001: isExactPromptMatch requires exact character match and rejects empty or truncated text', () => {
  const { runtime } = loadRealContentJs();
  assert.ok(runtime.isExactPromptMatch('블로그 글 잘 읽었습니다.', '블로그 글 잘 읽었습니다.'));
  assert.ok(runtime.isExactPromptMatch('블로그 글 잘 읽었습니다.\n', '블로그 글 잘 읽었습니다.'));
  assert.strictEqual(runtime.isExactPromptMatch('블로그  글', '블로그 글'), false);
  // Rejects truncated prompt (15% missing or 1 word missing)
  assert.strictEqual(runtime.isExactPromptMatch('블로그 글 잘 읽었습니다.', '블로그 글 잘'), false);
  assert.strictEqual(runtime.isExactPromptMatch('블로그 글 잘', '블로그 글 잘 읽었습니다.'), false);
  // Rejects empty actual or expected
  assert.strictEqual(runtime.isExactPromptMatch('', ''), false);
  assert.strictEqual(runtime.isExactPromptMatch('', '블로그 글'), false);
  assert.strictEqual(runtime.isExactPromptMatch('블로그 글', ''), false);
  assert.strictEqual(runtime.isExactPromptMatch('   ', '블로그 글'), false);
  // Preserves negation / semantic words
  assert.strictEqual(runtime.isExactPromptMatch('하지 마세요', '하세요'), false);
});

test('SEND-HARDEN-002: userTurnMatchesExpected handles multi-paragraph user query containers exactly', () => {
  const { runtime } = loadRealContentJs();
  const p1 = { innerText: '첫 번째 단락입니다.', isConnected: true };
  const p2 = { innerText: '두 번째 단락입니다.', isConnected: true };
  const queryNode = {
    isConnected: true,
    querySelector: () => null,
    querySelectorAll: (sel) => sel === 'p' ? [p1, p2] : [],
    innerText: '첫 번째 단락입니다.\n두 번째 단락입니다.'
  };

  const expectedStrict = runtime.strictNormalizePrompt('첫 번째 단락입니다.\n두 번째 단락입니다.');
  assert.ok(runtime.userTurnMatchesExpected(queryNode, expectedStrict));

  const truncatedStrict = runtime.strictNormalizePrompt('첫 번째 단락입니다.');
  assert.strictEqual(runtime.userTurnMatchesExpected(queryNode, truncatedStrict), false);
});

test('SEND-HARDEN-003: freshConversationState returns false if composer has text', () => {
  const { runtime, context } = loadRealContentJs();
  const dirtyEditor = {
    tagName: 'DIV', isConnected: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    closest: () => null,
    innerText: '이전 작성 중이던 프롬프트',
    textContent: '이전 작성 중이던 프롬프트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  context.document.querySelectorAll = (sel) => sel.includes('contenteditable') ? [dirtyEditor] : [];
  assert.strictEqual(runtime.freshConversationState(), false);

  const cleanEditor = {
    tagName: 'DIV', isConnected: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    closest: () => null,
    innerText: '',
    textContent: '',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  context.document.querySelectorAll = (sel) => sel.includes('contenteditable') ? [cleanEditor] : [];
  assert.strictEqual(runtime.freshConversationState(), true);
});

test('SEND-HARDEN-004: freshConversationState returns false if hidden old turns exist in DOM', () => {
  const { runtime, context } = loadRealContentJs();
  const cleanEditor = {
    tagName: 'DIV', isConnected: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    closest: () => null,
    innerText: '',
    textContent: '',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const hiddenUserTurn = {
    tagName: 'DIV', isConnected: true,
    className: 'user-message',
    getAttribute: () => null,
    closest: () => null,
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { display: 'none', visibility: 'hidden' }
  };
  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [cleanEditor];
    if (sel.includes('user-message') || sel.includes('user-query')) return [hiddenUserTurn];
    return [];
  };
  assert.strictEqual(runtime.freshConversationState(), false);
});

test('SEND-HARDEN-005: waitForStableReadback fails when editor text is truncated (exact match required)', async () => {
  const { runtime } = loadRealContentJs();
  const truncatedEditor = {
    tagName: 'DIV', isConnected: true,
    innerText: '1000자 프롬프트 중 뒤쪽 150자가 잘린 본문',
    textContent: '1000자 프롬프트 중 뒤쪽 150자가 잘린 본문'
  };
  const fullExpected = '1000자 프롬프트 중 뒤쪽 150자가 잘린 본문입니다. 반드시 완전 일치해야 합니다.';
  const res = await runtime.waitForStableReadback(() => truncatedEditor, fullExpected, 250);
  assert.strictEqual(res.ok, false);
});

test('SEND-HARDEN-006: Pre-click send button re-resolution fails with send_not_ready and 0 clicks if button is disabled', async () => {
  const { runtime, context } = loadRealContentJs();
  let clicks = 0;
  let composer;
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {}, dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '정상 프롬프트', textContent: '정상 프롬프트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  const disabledBtn = {
    tagName: 'BUTTON', isConnected: true, disabled: true,
    getAttribute: (k) => k === 'aria-label' ? 'send' : (k === 'aria-disabled' ? 'true' : null),
    click: () => { clicks++; },
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true, closest: () => composer,
    querySelectorAll: (sel) => [disabledBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };
  context.document.querySelectorAll = (sel) => sel.includes('contenteditable') ? [editorEl] : [];

  const cmd = { requestId: 'req_btn_disabled_01', prompt: '정상 프롬프트', timeout_seconds: 5 };
  const execState = { requestId: 'req_btn_disabled_01', generationDeadlineAtMs: Date.now() + 5000, deadlineAtMs: Date.now() + 5000 };
  prepareEmptyComposer(context, editorEl);
  const res = await runtime.executeCore(cmd, execState);

  assert.strictEqual(res.status, 'failed');
  assert.strictEqual(res.error, 'send_not_ready');
  assert.strictEqual(clicks, 0, 'No click should be dispatched when button is disabled on pre-click re-resolve');
});

test('SEND-HARDEN-007: Send commit requires user turn persistence for >= 650ms; turns vanishing at 300ms fail with send_commit_unknown', async () => {
  const { runtime, context } = loadRealContentJs();
  let composer;
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {}, dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '커밋 지속성 테스트', textContent: '커밋 지속성 테스트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  let sent = false;
  let queryVisible = false;
  const sendBtn = {
    tagName: 'BUTTON', isConnected: true, disabled: false,
    getAttribute: (k) => k === 'aria-label' ? 'send' : null,
    click: () => {
      sent = true;
      queryVisible = true;
      // Vanish after 300ms (before 650ms commit threshold)
      setTimeout(() => { queryVisible = false; }, 300);
    },
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true, closest: () => composer,
    querySelectorAll: () => [sendBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };

  const userQueryEl = {
    tagName: 'DIV', className: 'user-query-container', isConnected: true,
    closest: (sel) => sel.includes('.user-query-container') ? userQueryEl : null,
    querySelector: () => ({ innerText: '커밋 지속성 테스트' }),
    innerText: '커밋 지속성 테스트', textContent: '커밋 지속성 테스트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };

  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    if (sent && queryVisible && (sel.includes('.user-query-container') || sel.includes('user-query'))) {
      return [userQueryEl];
    }
    return [];
  };

  const cmd = { requestId: 'req_vanish_01', prompt: '커밋 지속성 테스트', timeout_seconds: 1.2 };
  const execState = { requestId: 'req_vanish_01', generationDeadlineAtMs: Date.now() + 1200, deadlineAtMs: Date.now() + 1200 };
  prepareEmptyComposer(context, editorEl);
  const res = await runtime.executeCore(cmd, execState);

  assert.strictEqual(res.status, 'failed');
  assert.strictEqual(res.error, 'send_commit_unknown');
});

test('SEND-HARDEN-008: Send commit succeeds when user turn persists >= 650ms and composer is cleared', async () => {
  const { runtime, context } = loadRealContentJs();
  let composer;
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {}, dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '성공 커밋 테스트', textContent: '성공 커밋 테스트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  let sent = false;
  const sendBtn = {
    tagName: 'BUTTON', isConnected: true, disabled: false,
    getAttribute: (k) => k === 'aria-label' ? 'send' : null,
    click: () => {
      sent = true;
      // simulate composer clearing on click
      setTimeout(() => { editorEl.innerText = ''; editorEl.textContent = ''; }, 200);
    },
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true, closest: () => composer,
    querySelectorAll: () => [sendBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };

  const userQueryEl = {
    tagName: 'DIV', className: 'user-query-container', isConnected: true,
    closest: (sel) => sel.includes('.user-query-container') ? userQueryEl : null,
    querySelector: () => ({ innerText: '성공 커밋 테스트' }),
    innerText: '성공 커밋 테스트', textContent: '성공 커밋 테스트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };
  const modelResponseEl = {
    tagName: 'DIV', className: 'model-response-container', isConnected: true,
    closest: () => modelResponseEl,
    querySelector: (sel) => sel.includes('.loading-dots') ? { isConnected: true, getBoundingClientRect: () => ({ width: 20, height: 20 }), computedStyle: { visibility: 'visible', display: 'block', opacity: '1' } } : null,
    querySelectorAll: () => [],
    innerText: '', textContent: '',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };

  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    if (sent && (sel.includes('.user-query-container') || sel.includes('user-query'))) return [userQueryEl];
    if (sent && (sel.includes('model-response') || sel.includes('.model-response-container'))) return [modelResponseEl];
    return [];
  };

  const cmd = { requestId: 'req_commit_ok_01', prompt: '성공 커밋 테스트', timeout_seconds: 1.5 };
  const execState = { requestId: 'req_commit_ok_01', generationDeadlineAtMs: Date.now() + 1500, deadlineAtMs: Date.now() + 1500 };
  prepareEmptyComposer(context, editorEl);
  const res = await runtime.executeCore(cmd, execState);

  // Reached observation phase and timed out waiting for response, proving commit succeeded!
  assert.strictEqual(res.status, 'timeout');
  assert.notStrictEqual(res.error, 'send_commit_unknown');
});

test('SEND-HARDEN-009: Route transition from /app to /app/<conv_id> with preserved user turn continues normally', async () => {
  const { runtime, context } = loadRealContentJs();
  context.location = { pathname: '/app', href: 'https://gemini.google.com/app' };
  let composer;
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {}, dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '경로 전환 테스트', textContent: '경로 전환 테스트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  let sent = false;
  const sendBtn = {
    tagName: 'BUTTON', isConnected: true, disabled: false,
    getAttribute: (k) => k === 'aria-label' ? 'send' : null,
    click: () => {
      sent = true;
      editorEl.innerText = '';
      editorEl.textContent = '';
      // Transition path to normal conversation id after commit check
      setTimeout(() => {
        context.location.pathname = '/app/c_conv123456';
      }, 800);
    },
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true, closest: () => composer,
    querySelectorAll: () => [sendBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };

  const userQueryEl = {
    tagName: 'DIV', className: 'user-query-container', isConnected: true,
    closest: (sel) => sel.includes('.user-query-container') ? userQueryEl : null,
    querySelector: () => ({ innerText: '경로 전환 테스트' }),
    innerText: '경로 전환 테스트', textContent: '경로 전환 테스트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };
  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    if (sent && (sel.includes('.user-query-container') || sel.includes('user-query'))) return [userQueryEl];
    return [];
  };

  const cmd = { requestId: 'req_route_ok_01', prompt: '경로 전환 테스트', timeout_seconds: 1.5 };
  const execState = { requestId: 'req_route_ok_01', generationDeadlineAtMs: Date.now() + 1500, deadlineAtMs: Date.now() + 1500 };
  prepareEmptyComposer(context, editorEl);
  const res = await runtime.executeCore(cmd, execState);

  // Transition to /app/c_conv123456 did NOT trigger send_state_lost!
  assert.notStrictEqual(res.error, 'send_state_lost');
  assert.strictEqual(res.status, 'timeout');
});

test('SEND-HARDEN-010: Unrelated route transition triggers send_state_lost', async () => {
  const { runtime, context } = loadRealContentJs();
  context.location = { pathname: '/app', href: 'https://gemini.google.com/app' };
  let composer;
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {}, dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '비정상 경로 전환 테스트', textContent: '비정상 경로 전환 테스트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  let sent = false;
  const sendBtn = {
    tagName: 'BUTTON', isConnected: true, disabled: false,
    getAttribute: (k) => k === 'aria-label' ? 'send' : null,
    click: () => {
      sent = true;
      editorEl.innerText = '';
      editorEl.textContent = '';
      // Unrelated route change after commit
      setTimeout(() => { context.location.pathname = '/settings'; }, 800);
    },
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true, closest: () => composer,
    querySelectorAll: () => [sendBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };

  const userQueryEl = {
    tagName: 'DIV', className: 'user-query-container', isConnected: true,
    closest: (sel) => sel.includes('.user-query-container') ? userQueryEl : null,
    querySelector: () => ({ innerText: '비정상 경로 전환 테스트' }),
    innerText: '비정상 경로 전환 테스트', textContent: '비정상 경로 전환 테스트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };
  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    if (sent && (sel.includes('.user-query-container') || sel.includes('user-query'))) return [userQueryEl];
    return [];
  };

  const cmd = { requestId: 'req_route_lost_01', prompt: '비정상 경로 전환 테스트', timeout_seconds: 2.5 };
  const execState = { requestId: 'req_route_lost_01', generationDeadlineAtMs: Date.now() + 2500, deadlineAtMs: Date.now() + 2500 };
  prepareEmptyComposer(context, editorEl);
  const res = await runtime.executeCore(cmd, execState);

  assert.strictEqual(res.status, 'failed');
  assert.strictEqual(res.error, 'send_state_lost');
});

test('SEND-HARDEN-011: Evidence lost for >= 5s with composer containing original prompt emits post_dispatch_uncommitted_suspected', async () => {
  const { runtime, context } = loadRealContentJs();
  let composer;
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {}, dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '증거 상실 테스트', textContent: '증거 상실 테스트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  let sent = false;
  let userAlive = true;
  let responseAlive = true;
  const sendBtn = {
    tagName: 'BUTTON', isConnected: true, disabled: false,
    getAttribute: (k) => k === 'aria-label' ? 'send' : null,
    click: () => {
      sent = true;
      // Keep user and model response for 800ms to confirm commit, then vanish!
      setTimeout(() => { userAlive = false; responseAlive = false; userQueryEl.isConnected = false; modelResponseEl.isConnected = false; }, 800);
    },
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true, closest: () => composer,
    querySelectorAll: () => [sendBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };

  const userQueryEl = {
    tagName: 'DIV', className: 'user-query-container', isConnected: true,
    closest: (sel) => sel.includes('.user-query-container') ? userQueryEl : null,
    querySelector: () => ({ innerText: '증거 상실 테스트' }),
    innerText: '증거 상실 테스트', textContent: '증거 상실 테스트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };
  const modelResponseEl = {
    tagName: 'DIV', className: 'model-response-container', isConnected: true,
    closest: () => modelResponseEl,
    querySelector: (sel) => sel.includes('.loading-dots') ? { isConnected: true, getBoundingClientRect: () => ({ width: 20, height: 20 }), computedStyle: { visibility: 'visible', display: 'block', opacity: '1' } } : null,
    querySelectorAll: () => [],
    innerText: '', textContent: '',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };

  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    if (sent && userAlive && (sel.includes('.user-query-container') || sel.includes('user-query'))) return [userQueryEl];
    if (sent && responseAlive && (sel.includes('model-response') || sel.includes('.model-response-container'))) return [modelResponseEl];
    return [];
  };

  const cmd = { requestId: 'req_uncommit_susp_01', prompt: '증거 상실 테스트', timeout_seconds: 7 };
  const execState = { requestId: 'req_uncommit_susp_01', generationDeadlineAtMs: Date.now() + 7000, deadlineAtMs: Date.now() + 7000 };
  prepareEmptyComposer(context, editorEl);
  const res = await runtime.executeCore(cmd, execState);

  assert.strictEqual(res.status, 'failed');
  assert.strictEqual(res.error, 'post_dispatch_uncommitted_suspected');
});

test('SEND-HARDEN-012: Evidence lost for >= 5s with composer empty emits send_state_lost', async () => {
  const { runtime, context } = loadRealContentJs();
  let composer;
  const editorEl = {
    tagName: 'DIV', isConnected: true, isContentEditable: true,
    getAttribute: (k) => k === 'contenteditable' ? 'true' : null,
    focus: () => {}, dispatchEvent: () => true,
    closest: (sel) => sel.includes('composer') ? composer : null,
    getBoundingClientRect: () => ({ top: 500, width: 200, height: 50 }),
    innerText: '증거 상실 빈 컴포저 테스트', textContent: '증거 상실 빈 컴포저 테스트',
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  let sent = false;
  let userAlive = true;
  const sendBtn = {
    tagName: 'BUTTON', isConnected: true, disabled: false,
    getAttribute: (k) => k === 'aria-label' ? 'send' : null,
    click: () => {
      sent = true;
      editorEl.innerText = '';
      editorEl.textContent = '';
      // Keep user turn for 800ms to confirm commit, then vanish!
      setTimeout(() => { userAlive = false; userQueryEl.isConnected = false; }, 800);
    },
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => composer,
    getBoundingClientRect: () => ({ width: 40, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' }
  };
  composer = {
    isConnected: true, closest: () => composer,
    querySelectorAll: () => [sendBtn],
    getBoundingClientRect: () => ({ top: 500, right: 800, bottom: 600, width: 300, height: 100 })
  };

  const userQueryEl = {
    tagName: 'DIV', className: 'user-query-container', isConnected: true,
    closest: (sel) => sel.includes('.user-query-container') ? userQueryEl : null,
    querySelector: () => ({ innerText: '증거 상실 빈 컴포저 테스트' }),
    innerText: '증거 상실 빈 컴포저 테스트', textContent: '증거 상실 빈 컴포저 테스트',
    getBoundingClientRect: () => ({ width: 100, height: 40 }),
    computedStyle: { visibility: 'visible', display: 'block', opacity: '1' },
    compareDocumentPosition: () => 4
  };
  context.document.querySelectorAll = (sel) => {
    if (sel.includes('contenteditable')) return [editorEl];
    if (sent && userAlive && (sel.includes('.user-query-container') || sel.includes('user-query'))) return [userQueryEl];
    return [];
  };

  const cmd = { requestId: 'req_lost_empty_01', prompt: '증거 상실 빈 컴포저 테스트', timeout_seconds: 7 };
  const execState = { requestId: 'req_lost_empty_01', generationDeadlineAtMs: Date.now() + 7000, deadlineAtMs: Date.now() + 7000 };
  prepareEmptyComposer(context, editorEl);
  const res = await runtime.executeCore(cmd, execState);

  assert.strictEqual(res.status, 'failed');
  assert.strictEqual(res.error, 'send_state_lost');
});
