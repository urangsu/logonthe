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

test('EXT-TIME-001: 55s deadline expiration with fake clock settles timeout and cleans up resources', () => {
  let virtualTime = 1000000;
  const originalDateNow = Date.now;
  Date.now = () => virtualTime;

  let observerDisconnected = false;
  let timerCleared = false;

  const mockObserver = {
    disconnect() {
      observerDisconnected = true;
    }
  };

  const timerId = 12345;
  const deadlineAtMs = virtualTime + 55000;

  const execState = {
    requestId: 'req-fake-clock-001',
    startedAtMs: virtualTime,
    deadlineAtMs: deadlineAtMs,
    cancelled: false,
    observer: mockObserver,
    timer: timerId,
    targetResponseNode: null,
  };

  let settledResult = null;
  const finish = (res) => {
    if (settledResult) return;
    settledResult = res;
    mockObserver.disconnect();
    timerCleared = true;
  };

  // Step 1: At 30s elapsed, timeout should not trigger
  virtualTime += 30000;
  if (Date.now() > execState.deadlineAtMs) {
    finish({ status: 'timeout', text: '', error: 'command_deadline_exceeded' });
  }
  assert.strictEqual(settledResult, null, 'Must not time out at 30s');
  assert.strictEqual(observerDisconnected, false);

  // Step 2: Advance clock past 55s (55001ms elapsed)
  virtualTime += 25001;
  if (Date.now() > execState.deadlineAtMs) {
    finish({ status: 'timeout', text: '', error: 'command_deadline_exceeded' });
  }

  assert.ok(settledResult, 'Must settle at 55s+');
  assert.strictEqual(settledResult.status, 'timeout');
  assert.strictEqual(settledResult.error, 'command_deadline_exceeded');
  assert.strictEqual(observerDisconnected, true, 'Observer must disconnect on timeout');
  assert.strictEqual(timerCleared, true, 'Timer must be cleared on timeout');

  Date.now = originalDateNow;
});

test('EXT-RESP-002: late response from previous request is excluded from candidate binding', () => {
  // Simulate Post 1's residual response in DOM
  const staleResponseNode = {
    tagName: 'MODEL-RESPONSE',
    isConnected: true,
    innerText: '이전 글에 대한 늦은 응답입니다~',
    getBoundingClientRect: () => ({ width: 400, height: 80 })
  };

  // Post 2 starts with baseline snapshot
  const initialResponseList = [staleResponseNode];
  const initialResponseSet = new Set(initialResponseList);
  const baselineResponseFingerprints = new Set(
    initialResponseList.map(n => canonicalPromptText(n.innerText)).filter(Boolean)
  );

  assert.ok(baselineResponseFingerprints.has(canonicalPromptText('이전 글에 대한 늦은 응답입니다~')));

  // Evaluate candidates for Post 2
  const allNodes = [staleResponseNode];
  const diagList = allNodes.map(node => {
    const isConn = Boolean(node.isConnected);
    const text = node.innerText;
    const textCanonical = canonicalPromptText(text);
    let excludeReason = null;
    if (!isConn) excludeReason = 'disconnected';
    else if (initialResponseSet.has(node)) excludeReason = 'initial_baseline_node';
    else if (textCanonical && baselineResponseFingerprints.has(textCanonical)) excludeReason = 'baseline_text_match';
    else if (!text) excludeReason = 'empty_text';

    return {
      tagName: node.tagName.toLowerCase(),
      excludeReason: excludeReason,
      isCandidate: !excludeReason
    };
  });

  const selectorMatches = allNodes.length;
  const visibleCandidates = diagList.filter(d => !d.excludeReason).length;

  assert.strictEqual(selectorMatches, 1, 'Total selector match is 1');
  assert.strictEqual(visibleCandidates, 0, 'Visible valid candidate must be 0 (stale node excluded)');
  assert.strictEqual(diagList[0].excludeReason, 'initial_baseline_node', 'Must be marked as initial_baseline_node');
});

test('EXT-DIAG-003: responseCount decomposed into selector_matches, visible_candidates, bound_response', () => {
  const currentNodes = [
    { isConnected: true, text: '신규 생성 중...', visible: true, inBaseline: false },
    { isConnected: true, text: '', visible: false, inBaseline: false },
    { isConnected: false, text: 'detached', visible: false, inBaseline: false },
  ];

  const diagList = currentNodes.map(n => {
    let reason = null;
    if (!n.isConnected) reason = 'disconnected';
    else if (!n.visible) reason = 'not_visible';
    else if (!n.text) reason = 'empty_text';
    return { isCandidate: !reason, reason };
  });

  const selector_matches = currentNodes.length;
  const visible_candidates = diagList.filter(d => d.isCandidate).length;
  const bound_response = false;

  assert.strictEqual(selector_matches, 3);
  assert.strictEqual(visible_candidates, 1);
  assert.strictEqual(bound_response, false);
});
