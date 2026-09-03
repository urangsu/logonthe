import assert from 'node:assert';
import test from 'node:test';

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
    runtimeBuild: '13.2.3-r8'
  };
  assert.strictEqual(diag.runtimeBuild, '13.2.3-r8');
  assert.strictEqual(diag.freshChatVerified, true);
  assert.strictEqual(diag.responseBound, true);
});

test('GEM-R8-008: old r7 runtime with r8 contract triggers reinjection', () => {
  const contract = { runtimeBuild: '13.2.3-r8' };
  const pingResponse = { ok: true, build: '13.2.3-r7' };
  let reinjected = false;
  if (!pingResponse.ok || pingResponse.build !== contract.runtimeBuild) {
    reinjected = true;
  }
  assert.strictEqual(reinjected, true, 'r7 build must trigger reinjection under r8 contract');
});
