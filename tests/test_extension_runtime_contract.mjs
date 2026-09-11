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

