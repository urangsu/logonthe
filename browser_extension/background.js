'use strict';

const BASE = 'http://127.0.0.1:43127';
let runtimeContractCache = null;
const injectionInFlight = new Map();
let isTransportLoopRunning = false;
let lastHeartbeatSentAt = 0;

async function getRuntimeContract() {
  if (runtimeContractCache) return runtimeContractCache;
  const url = chrome.runtime.getURL('runtime_contract.json');
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`runtime_contract_http_${res.status}`);
  runtimeContractCache = await res.json();
  return runtimeContractCache;
}

async function bridgeFetch(path, method = 'GET', body = null, timeoutMs = 20000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${BASE}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.reason || `http_${response.status}`);
    return data;
  } catch (error) {
    const msg = String(error?.message || error);
    if (/permission|blocked|denied|security/i.test(msg)) throw new Error('local_network_access_blocked');
    if (/fetch|connect|network|econnrefused|aborted/i.test(msg)) throw new Error('loopback_bridge_unreachable');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function pingRuntime(tabId) {
  return new Promise(resolve => {
    try {
      chrome.tabs.sendMessage(tabId, { type: 'NFA_RUNTIME_PING' }, response => {
        if (chrome.runtime.lastError || !response?.alive) {
          resolve({ ok: false, error: chrome.runtime.lastError?.message || 'no_response' });
        } else {
          resolve({
            ok: true,
            build: response.build,
            status: response.status,
            instanceId: response.instanceId,
            url: response.url,
            title: response.title,
            busyRequestId: response.busyRequestId || null,
            busySince: response.busySince || null,
            busyDeadlineAt: response.busyDeadlineAt || null
          });
        }
      });
    } catch (e) {
      resolve({ ok: false, error: String(e?.message || e) });
    }
  });
}

async function ensureGeminiRuntime(tabId) {
  if (typeof tabId !== 'number') return { ok: false, status: 'missing_tab_id' };
  if (injectionInFlight.has(tabId)) return injectionInFlight.get(tabId);
  const task = (async () => {
    const contract = await getRuntimeContract();
    const ping = await pingRuntime(tabId);
    if (ping.ok && ping.build === contract.runtimeBuild) return { ok: true, status: 'already_loaded', build: ping.build };
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
    await new Promise(resolve => setTimeout(resolve, 300));
    const verify = await pingRuntime(tabId);
    return verify.ok
      ? { ok: true, status: 'injected', build: verify.build }
      : { ok: false, status: 'injected_unverified', error: verify.error };
  })().catch(error => ({ ok: false, status: 'inject_failed', error: String(error?.message || error) }))
    .finally(() => injectionInFlight.delete(tabId));
  injectionInFlight.set(tabId, task);
  return task;
}

async function ensureAllGeminiTabs() {
  const tabs = await chrome.tabs.query({ url: '*://gemini.google.com/*' });
  const results = [];
  for (const tab of tabs) {
    if (typeof tab.id !== 'number') continue;
    results.push({ tabId: tab.id, ...(await ensureGeminiRuntime(tab.id)) });
  }
  return { tabsFound: tabs.length, results };
}

async function findActiveGeminiRuntime() {
  const tabs = await chrome.tabs.query({ url: '*://gemini.google.com/*' });
  const contract = await getRuntimeContract();
  for (const tab of tabs) {
    if (typeof tab.id !== 'number') continue;
    let ping = await pingRuntime(tab.id);
    if (!ping.ok || ping.build !== contract.runtimeBuild) {
      await ensureGeminiRuntime(tab.id);
      ping = await pingRuntime(tab.id);
    }
    if (ping.ok) return { tabId: tab.id, ping, contract };
  }
  return null;
}

async function startHeartbeatLoop() {
  while (true) {
    try {
      const contract = await getRuntimeContract();
      const activeRuntime = await findActiveGeminiRuntime();
      const now = Date.now();
      let bridgeStatus = null;
      try { bridgeStatus = await bridgeFetch('/v1/status', 'GET', null, 3000); } catch (_) {}
      if (activeRuntime && activeRuntime.ping.status === 'busy' && activeRuntime.ping.busyRequestId) {
        if (bridgeStatus && (bridgeStatus.bridgeSessionId || bridgeStatus.status) && typeof bridgeStatus.activeRequestId !== 'undefined') {
          const pythonActiveReqId = bridgeStatus.activeRequestId;
          const busyReqId = activeRuntime.ping.busyRequestId;
          if (pythonActiveReqId === null || (typeof pythonActiveReqId === 'string' && pythonActiveReqId !== busyReqId)) {
            try {
              trackCancelledRequestId(busyReqId);
              chrome.tabs.sendMessage(activeRuntime.tabId, { type: 'NFA_CANCEL_COMMAND', requestId: busyReqId }, () => {});
            } catch (_) {}
          }
        }
      }
      await bridgeFetch('/v1/heartbeat', 'POST', {
        status: activeRuntime ? (activeRuntime.ping.status || 'ready') : 'gemini_tab_not_found',
        transportAlive: true,
        runtimeAlive: Boolean(activeRuntime),
        runtimeStatus: activeRuntime ? activeRuntime.ping.status : 'disconnected',
        title: activeRuntime?.ping.title || 'Google Gemini',
        url: activeRuntime?.ping.url || 'https://gemini.google.com/app',
        extensionVersion: contract.extensionVersion,
        contentBuild: contract.runtimeBuild,
        buildId: contract.runtimeBuild,
        protocolVersion: contract.protocolVersion,
        bridgeSchemaVersion: contract.bridgeSchemaVersion,
        consumerId: 'background-r16',
        lastRuntimePingAt: now,
        busyRequestId: activeRuntime?.ping.busyRequestId || null,
        busySince: activeRuntime?.ping.busySince || null,
        busyDeadlineAt: activeRuntime?.ping.busyDeadlineAt || null
      }, 5000);
      lastHeartbeatSentAt = now;
    } catch (_) {}
    await new Promise(r => setTimeout(r, 10000));
  }
}

async function ensureFreshGeminiConversation(tabId) {
  if (typeof tabId !== 'number') return { ok: false, error: 'missing_tab_id' };
  const contract = await getRuntimeContract();
  for (let attempt = 0; attempt < 2; attempt++) {
    const checkRes = await new Promise(resolve => {
      try {
        chrome.tabs.sendMessage(tabId, { type: 'NFA_CHECK_FRESH_CHAT' }, res => {
          if (chrome.runtime.lastError || !res?.ok) resolve(null); else resolve(res);
        });
      } catch (_) { resolve(null); }
    });
    if (checkRes && checkRes.fresh) {
      console.log('[GEMINI][BACKGROUND] FRESH_CHAT_READY (verified empty)', { tabId, contentInstanceId: checkRes.contentInstanceId, conversationEpoch: checkRes.conversationEpoch });
      try { await bridgeFetch('/v1/event', 'POST', { type: 'FRESH_CHAT_RUNTIME_READY', tab: tabId, instance: checkRes.contentInstanceId, epoch: checkRes.conversationEpoch }, 3000); } catch (_) {}
      try {
        await bridgeFetch('/v1/heartbeat', 'POST', {
          status: 'ready', transportAlive: true, runtimeAlive: true, runtimeStatus: 'ready', title: 'Google Gemini',
          url: 'https://gemini.google.com/app', extensionVersion: contract.extensionVersion, contentBuild: contract.runtimeBuild,
          buildId: contract.runtimeBuild, protocolVersion: contract.protocolVersion, bridgeSchemaVersion: contract.bridgeSchemaVersion,
          consumerId: 'background-r16', lastRuntimePingAt: Date.now(), tabId, contentInstanceId: checkRes.contentInstanceId,
          conversationEpoch: checkRes.conversationEpoch
        }, 5000);
      } catch (_) {}
      return { ok: true, reason: 'already_fresh', tabId, contentInstanceId: checkRes.contentInstanceId, conversationEpoch: checkRes.conversationEpoch };
    }
    if (attempt === 0) {
      await new Promise(resolve => {
        try { chrome.tabs.sendMessage(tabId, { type: 'NFA_RESET_FRESH_CHAT' }, () => resolve()); } catch (_) { resolve(); }
      });
      await new Promise(r => setTimeout(r, 1000));
    }
  }
  console.log('[GEMINI][BACKGROUND] Navigating tab to fresh https://gemini.google.com/app');
  try { await chrome.tabs.update(tabId, { url: 'https://gemini.google.com/app' }); } catch (navErr) { return { ok: false, error: String(navErr?.message || navErr) }; }
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000));
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab || tab.status !== 'complete') continue;
    await ensureGeminiRuntime(tabId);
    const ping = await pingRuntime(tabId);
    if (!ping.ok || ping.status !== 'ready') continue;
    const checkRes = await new Promise(resolve => {
      try {
        chrome.tabs.sendMessage(tabId, { type: 'NFA_CHECK_FRESH_CHAT' }, res => {
          if (chrome.runtime.lastError || !res?.ok) resolve(null); else resolve(res);
        });
      } catch (_) { resolve(null); }
    });
    if (checkRes && checkRes.fresh) {
      console.log('[GEMINI][BACKGROUND] FRESH_CHAT_READY (navigated and verified)', { tabId, contentInstanceId: checkRes.contentInstanceId, conversationEpoch: checkRes.conversationEpoch });
      try { await bridgeFetch('/v1/event', 'POST', { type: 'FRESH_CHAT_RUNTIME_READY', tab: tabId, instance: checkRes.contentInstanceId, epoch: checkRes.conversationEpoch }, 3000); } catch (_) {}
      try {
        await bridgeFetch('/v1/heartbeat', 'POST', {
          status: ping.status || 'ready', transportAlive: true, runtimeAlive: true, runtimeStatus: ping.status,
          title: ping.title || 'Google Gemini', url: ping.url || 'https://gemini.google.com/app', extensionVersion: contract.extensionVersion,
          contentBuild: contract.runtimeBuild, buildId: contract.runtimeBuild, protocolVersion: contract.protocolVersion,
          bridgeSchemaVersion: contract.bridgeSchemaVersion, consumerId: 'background-r16', lastRuntimePingAt: Date.now(), tabId,
          contentInstanceId: checkRes.contentInstanceId, conversationEpoch: checkRes.conversationEpoch
        }, 5000);
      } catch (_) {}
      return { ok: true, reason: 'navigated_fresh', tabId, contentInstanceId: checkRes.contentInstanceId, conversationEpoch: checkRes.conversationEpoch };
    }
  }
  return { ok: false, error: 'fresh_chat_not_verified' };
}

const inFlightCommandResolvers = new Map();
const cancelledRequestIds = new Set();
const resultDeliveryRegistry = new Map();

function trackCancelledRequestId(rid) {
  if (!rid) return;
  cancelledRequestIds.add(rid);
  if (cancelledRequestIds.size > 100) cancelledRequestIds.delete(cancelledRequestIds.values().next().value);
}

function resultEnvelope(message, fallbackMeta = null) {
  const meta = fallbackMeta || {};
  const rid = String(message?.requestId || meta.requestId || '').trim();
  const postKey = String(message?.postKey || meta.postKey || '').trim();
  const navigationVersion = Number(message?.navigationVersion || meta.navigationVersion || 0);
  const status = message?.result?.status || message?.status || 'failed';
  const text = message?.result?.text || message?.text || '';
  const error = message?.result?.error || message?.error || '';
  const deliveryId = message?.deliveryId || (rid ? `${rid}:${status}:${text.length}` : '');
  return {
    requestId: rid,
    postKey,
    navigationVersion,
    status,
    text,
    error,
    deliveryId
  };
}

function computeResultFingerprint(body) {
  const status = body?.status || '';
  const postKey = body?.postKey || '';
  const navVer = String(body?.navigationVersion || 0);
  const text = body?.text || '';
  const error = body?.error || '';
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    hash = (Math.imul(31, hash) + text.charCodeAt(i)) | 0;
  }
  const textHash = (hash >>> 0).toString(16);
  return `${body?.requestId || ''}|${postKey}|${navVer}|${status}|${textHash}:${text.length}|${error}`;
}

function pruneResultDeliveryRegistry() {
  const now = Date.now();
  const TTL_MS = 10 * 60 * 1000;
  for (const [rid, entry] of resultDeliveryRegistry.entries()) {
    if (entry?.lastAttemptAt && (now - entry.lastAttemptAt > TTL_MS)) {
      resultDeliveryRegistry.delete(rid);
    }
  }
  while (resultDeliveryRegistry.size > 200) {
    const oldestKey = resultDeliveryRegistry.keys().next().value;
    resultDeliveryRegistry.delete(oldestKey);
  }
}

async function forwardResultToPython(message, fallbackMeta = null, timeoutMs = 3500) {
  const body = resultEnvelope(message, fallbackMeta);
  if (!body.requestId || !body.postKey || !Number.isInteger(Number(body.navigationVersion)) || Number(body.navigationVersion) <= 0) {
    return { ok: false, received: true, accepted: false, reason: 'invalid_result_identity' };
  }
  const fingerprint = computeResultFingerprint(body);
  const existing = resultDeliveryRegistry.get(body.requestId);
  if (existing?.state === 'accepted') {
    if (existing.fingerprint && existing.fingerprint !== fingerprint) {
      console.warn('[GEMINI][BACKGROUND] Duplicate result conflict for accepted RID:', body.requestId);
      return {
        ok: false,
        received: true,
        accepted: false,
        reason: 'duplicate_result_conflict'
      };
    }
    return {
      ok: true,
      received: true,
      accepted: true,
      reason: existing.reason || 'already_accepted'
    };
  }
  if (existing?.state === 'forwarding' && existing.promise) {
    if (existing.fingerprint && existing.fingerprint !== fingerprint) {
      console.warn('[GEMINI][BACKGROUND] Duplicate result conflict for in-flight RID:', body.requestId);
      return {
        ok: false,
        received: true,
        accepted: false,
        reason: 'duplicate_result_conflict'
      };
    }
    return await existing.promise;
  }

  const promise = (async () => {
    try {
      const res = await bridgeFetch('/v1/result', 'POST', body, timeoutMs);
      const accepted = Boolean(res && (res.accepted === true || res.reason === 'already_accepted'));
      if (accepted) {
        resultDeliveryRegistry.set(body.requestId, {
          state: 'accepted',
          fingerprint,
          lastAttemptAt: Date.now(),
          reason: res?.reason || 'accepted'
        });
      } else {
        resultDeliveryRegistry.set(body.requestId, {
          state: 'failed',
          fingerprint,
          lastAttemptAt: Date.now(),
          reason: res?.reason || 'python_rejected'
        });
      }
      return { ok: accepted, received: true, accepted, reason: res?.reason || (accepted ? 'accepted' : 'python_rejected') };
    } catch (error) {
      resultDeliveryRegistry.set(body.requestId, {
        state: 'failed',
        fingerprint,
        lastAttemptAt: Date.now(),
        reason: String(error?.message || error)
      });
      return { ok: false, received: true, accepted: false, reason: String(error?.message || error) };
    }
  })();

  pruneResultDeliveryRegistry();
  resultDeliveryRegistry.set(body.requestId, {
    state: 'forwarding',
    promise,
    fingerprint,
    lastAttemptAt: Date.now()
  });

  return await promise;
}

async function runCommandCycle() {
  const contract = await getRuntimeContract();
  const activeRuntime = await findActiveGeminiRuntime();
  if (!activeRuntime || activeRuntime.ping.status !== 'ready') { await new Promise(r => setTimeout(r, 2000)); return; }
  let cmdData = null;
  try { cmdData = await bridgeFetch('/v1/command/wait?timeout=15', 'GET', null, 20000); } catch (_) { return; }
  const command = cmdData?.command;
  if (!command) return;
  try {
    const claim = await bridgeFetch('/v1/claim', 'POST', { requestId: command.requestId, claimant: `${contract.runtimeBuild}_tab_${activeRuntime.tabId}` }, 5000);
    if (!claim.claimed) return;
  } catch (_) { return; }
  const freshCheck = await ensureFreshGeminiConversation(activeRuntime.tabId);
  if (!freshCheck.ok) {
    console.warn('[GEMINI][BACKGROUND] fresh chat verification failed:', freshCheck.error);
    await bridgeFetch('/v1/result', 'POST', { requestId: command.requestId, postKey: command.postKey, navigationVersion: command.navigationVersion, status: 'dom_unsupported', text: '', error: 'fresh_chat_not_verified' }, 10000).catch(() => {});
    return;
  }
  let execResult = null;
  const dispatchPromise = new Promise(resolve => {
    const deadlineMs = typeof command.deadlineAtMs === 'number' && command.deadlineAtMs > 0 ? command.deadlineAtMs - Date.now() : (typeof command.deadlineAt === 'number' && command.deadlineAt > 1e11 ? command.deadlineAt - Date.now() : (typeof command.deadlineAt === 'number' && command.deadlineAt > 0 ? command.deadlineAt * 1000 - Date.now() : 60000));
    const timeoutMs = Math.max(5000, deadlineMs);
    const keepAliveInterval = setInterval(() => {
      if (!inFlightCommandResolvers.has(command.requestId)) { clearInterval(keepAliveInterval); return; }
      try { chrome.runtime.getPlatformInfo(() => {}); } catch (_) {}
    }, 4500);
    const timer = setTimeout(() => {
      if (inFlightCommandResolvers.has(command.requestId)) {
        clearInterval(keepAliveInterval); inFlightCommandResolvers.delete(command.requestId); trackCancelledRequestId(command.requestId);
        try { chrome.tabs.sendMessage(activeRuntime.tabId, { type: 'NFA_CANCEL_COMMAND', requestId: command.requestId }, () => {}); } catch (_) {}
        resolve({ status: 'timeout', text: '', error: 'command_deadline_exceeded' });
      }
    }, timeoutMs);
    inFlightCommandResolvers.set(command.requestId, { resolve, timer, keepAliveInterval, commandMetadata: { requestId: command.requestId, postKey: command.postKey, navigationVersion: command.navigationVersion } });
    command.tabId = freshCheck.tabId || activeRuntime.tabId;
    command.contentInstanceId = freshCheck.contentInstanceId;
    command.conversationEpoch = freshCheck.conversationEpoch;
    chrome.tabs.sendMessage(activeRuntime.tabId, { type: 'NFA_EXECUTE_COMMAND', command }, res => {
      if (chrome.runtime.lastError || !res?.ok) {
        const inFlight = inFlightCommandResolvers.get(command.requestId);
        if (inFlight) { if (inFlight.timer) clearTimeout(inFlight.timer); if (inFlight.keepAliveInterval) clearInterval(inFlight.keepAliveInterval); inFlightCommandResolvers.delete(command.requestId); }
        resolve({ status: 'failed', text: '', error: chrome.runtime.lastError?.message || 'failed_to_start_command' });
      }
    });
  });
  try { execResult = await dispatchPromise; } catch (e) { execResult = { status: 'failed', text: '', error: String(e?.message || e) }; }
  const existingDelivery = resultDeliveryRegistry.get(command.requestId);
  if (existingDelivery && existingDelivery.state === 'accepted') {
    console.log('[GEMINI][BACKGROUND] Result delivery already accepted:', command.requestId);
  } else if (existingDelivery && existingDelivery.state === 'forwarding' && existingDelivery.promise) {
    console.log('[GEMINI][BACKGROUND] Result delivery handled by primary handler (in-flight):', command.requestId);
    try { await existingDelivery.promise; } catch (_) {}
  } else {
    const fwdResult = await forwardResultToPython({
      requestId: command.requestId,
      postKey: command.postKey,
      navigationVersion: command.navigationVersion,
      result: execResult,
      deliveryId: `${command.requestId}:cycle:0`
    }, null, 10000);
    if (fwdResult.accepted) {
      console.log('[GEMINI][BACKGROUND][RESULT_ACCEPTED]', command.requestId, fwdResult.reason || 'accepted');
    } else {
      console.warn('[GEMINI][BACKGROUND][RESULT_DELIVERY_UNCONFIRMED]', command.requestId, fwdResult.reason);
    }
  }
  try {
    const postExecRuntime = await findActiveGeminiRuntime();
    const now = Date.now();
    await bridgeFetch('/v1/heartbeat', 'POST', {
      status: postExecRuntime ? (postExecRuntime.ping.status || 'ready') : 'gemini_tab_not_found', transportAlive: true,
      runtimeAlive: Boolean(postExecRuntime), runtimeStatus: postExecRuntime ? postExecRuntime.ping.status : 'disconnected',
      title: postExecRuntime?.ping.title || 'Google Gemini', url: postExecRuntime?.ping.url || 'https://gemini.google.com/app',
      extensionVersion: contract.extensionVersion, contentBuild: contract.runtimeBuild, buildId: contract.runtimeBuild,
      protocolVersion: contract.protocolVersion, bridgeSchemaVersion: contract.bridgeSchemaVersion, consumerId: 'background-r16',
      lastRuntimePingAt: now, busyRequestId: postExecRuntime?.ping.busyRequestId || null, busySince: postExecRuntime?.ping.busySince || null,
      busyDeadlineAt: postExecRuntime?.ping.busyDeadlineAt || null
    }, 5000);
    lastHeartbeatSentAt = now;
  } catch (_) {}
}

async function startTransportEngine() {
  if (isTransportLoopRunning) return;
  isTransportLoopRunning = true;
  startHeartbeatLoop();
  while (true) {
    try { await runCommandCycle(); } catch (cycleErr) { console.debug('[GEMINI][BACKGROUND] cycle error:', cycleErr); await new Promise(r => setTimeout(r, 1000)); }
  }
}

async function diagnose() {
  const injection = await ensureAllGeminiTabs();
  if (injection.tabsFound === 0) return { ok: false, status: 'gemini_tab_not_found', injection };
  await new Promise(resolve => setTimeout(resolve, 500));
  try { const bridge = await bridgeFetch('/v1/status', 'GET', null, 3000); return { ok: true, status: bridge.status || 'unknown', bridge, injection }; }
  catch (error) { return { ok: false, status: 'loopback_bridge_unreachable', error: String(error?.message || error), injection }; }
}

chrome.runtime.onInstalled.addListener(() => { ensureAllGeminiTabs().catch(() => {}); });
chrome.runtime.onStartup.addListener(() => { ensureAllGeminiTabs().catch(() => {}); });
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab.url || '';
  if ((changeInfo.status === 'complete' || changeInfo.url) && /^https:\/\/gemini\.google\.com\//.test(url)) ensureGeminiRuntime(tabId).catch(() => {});
});
if (!globalThis.__NFA_SKIP_AUTO_START__) {
  startTransportEngine().catch(() => {});
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'getRuntimeContract') {
    getRuntimeContract().then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message?.type === 'ensureGeminiRuntime' || message?.type === 'reconnectGemini') {
    const tabId = sender.tab?.id;
    if (tabId) ensureGeminiRuntime(tabId).then(data => sendResponse(data)).catch(error => sendResponse({ ok: false, status: 'inject_failed', error: String(error?.message || error) }));
    else ensureAllGeminiTabs().then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, status: 'inject_failed', error: String(error?.message || error) }));
    return true;
  }
  if (message?.type === 'diagnoseExtension') {
    diagnose().then(data => sendResponse(data)).catch(error => sendResponse({ ok: false, status: 'diagnostic_failed', error: String(error?.message || error) }));
    return true;
  }
  if (message?.type === 'NFA_EXECUTION_RESULT' || message?.type === 'NFA_COMMAND_RESULT') {
    if (cancelledRequestIds.has(message.requestId)) {
      console.log('[GEMINI][BACKGROUND] Dropping late execution result for cancelled rid:', message.requestId);
      sendResponse({ ok: true, received: true, accepted: false, reason: 'request_cancelled' });
      return true;
    }
    const inFlight = inFlightCommandResolvers.get(message.requestId);
    const fallbackMeta = inFlight?.commandMetadata || null;
    if (inFlight) {
      if (inFlight.timer) clearTimeout(inFlight.timer);
      if (inFlight.keepAliveInterval) clearInterval(inFlight.keepAliveInterval);
      inFlightCommandResolvers.delete(message.requestId);
      inFlight.resolve(message.result);
    } else {
      console.log('[GEMINI][BACKGROUND] resolver_missing_result_recovered', message.requestId);
    }
    forwardResultToPython(message, fallbackMeta, 3500)
      .then(ack => sendResponse(ack))
      .catch(error => sendResponse({ ok: false, received: true, accepted: false, reason: String(error?.message || error) }));
    return true;
  }
  if (message?.type === 'NFA_WAIT_DIAG') {
    if (message.diag?.rid && cancelledRequestIds.has(message.diag.rid)) { sendResponse({ ok: false, error: 'cancelled' }); return true; }
    if (message.diag) bridgeFetch('/v1/diag', 'POST', message.diag, 4000).catch(() => {});
    sendResponse({ ok: true }); return true;
  }
  if (message?.type === 'NFA_EVENT') {
    if (message.event?.rid && cancelledRequestIds.has(message.event.rid)) { sendResponse({ ok: false, error: 'cancelled' }); return true; }
    if (message.event) bridgeFetch('/v1/event', 'POST', message.event, 4000).catch(() => {});
    sendResponse({ ok: true }); return true;
  }
  if (message?.type === 'bridgeFetch') {
    bridgeFetch(message.path, message.method || 'GET', message.body || null).then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  return false;
});

globalThis.__NFA_BACKGROUND__ = {
  forwardResultToPython,
  resultDeliveryRegistry,
  resultEnvelope,
  computeResultFingerprint,
  pruneResultDeliveryRegistry,
  bridgeFetch,
  runCommandCycle
};
