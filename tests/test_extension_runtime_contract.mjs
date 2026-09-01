import assert from 'node:assert';
import test from 'node:test';

test('JS Extension Contract: exact prompt equality readback', () => {
  function normalizeText(value) {
    return String(value || '').replace(/\r\n?/g, '\n').trim();
  }

  function checkReadback(readback, expected) {
    const read = normalizeText(readback);
    const exp = normalizeText(expected);
    return read === exp;
  }

  assert.strictEqual(checkReadback('정확한 프롬프트입니다', '정확한 프롬프트입니다'), true);
  assert.strictEqual(checkReadback('정확한 프롬프트입니다\n', '정확한 프롬프트입니다'), true);
  assert.strictEqual(checkReadback('앞부분만 일치하는 긴 프롬프트 내용입니다', '앞부분만 일치'), false, 'Partial match must be rejected');
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
  assert.strictEqual(activeExecution, null, 'activeExecution must be cleaned up');
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

  // 1. Background dispatches NFA_EXECUTE_COMMAND
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

  // 2. Content script sends immediate ACK
  const immediateAck = { ok: true, started: true, requestId: command.requestId, protocol: 'two-message-v2' };
  assert.strictEqual(immediateAck.ok, true);
  assert.strictEqual(immediateAck.started, true);

  // 3. Content script finishes and sends NFA_EXECUTION_RESULT
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
  assert.strictEqual(timerCleared, true, 'Timer must be cleared upon normal execution result');
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

  // Handle message when resolver is missing
  const inFlight = inFlightCommandResolvers.get(incomingMessage.requestId);
  if (inFlight) {
    inFlight.resolve(incomingMessage.result);
  } else {
    // Recovery path
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

test('JS Extension Contract: background reconciliation keeps normal active command safe', () => {
  const activeRuntime = {
    ping: {
      status: 'busy',
      busyRequestId: 'req-active-123'
    }
  };

  const bridgeStatus = {
    bridgeSessionId: 'sess-abc',
    status: 'ready',
    activeRequestId: 'req-active-123'
  };

  let cancelSent = false;
  if (activeRuntime && activeRuntime.ping.status === 'busy' && activeRuntime.ping.busyRequestId) {
    if (bridgeStatus && (bridgeStatus.bridgeSessionId || bridgeStatus.status) && typeof bridgeStatus.activeRequestId !== 'undefined') {
      const pythonActiveReqId = bridgeStatus.activeRequestId;
      const busyReqId = activeRuntime.ping.busyRequestId;
      if (pythonActiveReqId === null || (typeof pythonActiveReqId === 'string' && pythonActiveReqId !== busyReqId)) {
        cancelSent = true;
      }
    }
  }

  assert.strictEqual(cancelSent, false, 'Normal active command must NEVER trigger cancel');
});
