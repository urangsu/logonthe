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
    if (!response.ok) throw new Error(data.error || `http_${response.status}`);
    return data;
  } catch (error) {
    const msg = String(error?.message || error);
    if (/permission|blocked|denied|security/i.test(msg)) {
      throw new Error('local_network_access_blocked');
    }
    const isNetwork = /fetch|connect|network|econnrefused|aborted/i.test(msg);
    if (isNetwork) {
      throw new Error('loopback_bridge_unreachable');
    }
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
            title: response.title
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
    if (ping.ok && ping.build === contract.runtimeBuild) {
      return { ok: true, status: 'already_loaded', build: ping.build };
    }

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js']
    });

    await new Promise(resolve => setTimeout(resolve, 300));
    const verify = await pingRuntime(tabId);
    if (verify.ok) {
      return { ok: true, status: 'injected', build: verify.build };
    } else {
      return { ok: false, status: 'injected_unverified', error: verify.error };
    }
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
    if (ping.ok) {
      return { tabId: tab.id, ping, contract };
    }
  }
  return null;
}

async function startHeartbeatLoop() {
  while (true) {
    try {
      const contract = await getRuntimeContract();
      const activeRuntime = await findActiveGeminiRuntime();
      const now = Date.now();
      const heartbeatPayload = {
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
        consumerId: 'background-r4',
        lastRuntimePingAt: now
      };
      await bridgeFetch('/v1/heartbeat', 'POST', heartbeatPayload, 5000);
      lastHeartbeatSentAt = now;
    } catch (_) {}
    await new Promise(r => setTimeout(r, 10000));
  }
}

async function runCommandCycle() {
  const contract = await getRuntimeContract();
  const activeRuntime = await findActiveGeminiRuntime();

  if (!activeRuntime || activeRuntime.ping.status !== 'ready') {
    // If Gemini tab is missing or not ready, wait and retry
    await new Promise(r => setTimeout(r, 2000));
    return;
  }

  // 1. Long-poll for next command (up to 15s wait on bridge server)
  let cmdData = null;
  try {
    cmdData = await bridgeFetch('/v1/command/wait?timeout=15', 'GET', null, 20000);
  } catch (cmdErr) {
    return;
  }

  const command = cmdData?.command;
  if (!command) {
    return;
  }

  // 2. Claim command
  try {
    const claim = await bridgeFetch('/v1/claim', 'POST', {
      requestId: command.requestId,
      claimant: contract.runtimeBuild
    }, 5000);
    if (!claim.claimed) {
      return;
    }
  } catch (_) {
    return;
  }

  // 3. Dispatch NFA_EXECUTE_COMMAND to Gemini Content Script
  let execResult = null;
  try {
    execResult = await new Promise((resolve) => {
      chrome.tabs.sendMessage(
        activeRuntime.tabId,
        { type: 'NFA_EXECUTE_COMMAND', command },
        (res) => {
          if (chrome.runtime.lastError || !res) {
            resolve({
              status: 'failed',
              text: '',
              error: chrome.runtime.lastError?.message || 'no_response_from_content'
            });
          } else {
            resolve(res);
          }
        }
      );
    });
  } catch (e) {
    execResult = { status: 'failed', text: '', error: String(e?.message || e) };
  }

  // 4. Submit execution result to Python
  try {
    await bridgeFetch('/v1/result', 'POST', {
      requestId: command.requestId,
      postKey: command.postKey,
      navigationVersion: command.navigationVersion,
      status: execResult?.status || 'failed',
      text: execResult?.text || '',
      error: execResult?.error || ''
    }, 10000);
  } catch (resErr) {
    console.debug('[GEMINI][BACKGROUND] result submit fail:', resErr);
  }
}

async function startTransportEngine() {
  if (isTransportLoopRunning) return;
  isTransportLoopRunning = true;
  startHeartbeatLoop();
  while (true) {
    try {
      await runCommandCycle();
    } catch (e) {
      await new Promise(r => setTimeout(r, 2000));
    }
  }
}

async function diagnose() {
  const injection = await ensureAllGeminiTabs();
  if (injection.tabsFound === 0) {
    return { ok: false, status: 'gemini_tab_not_found', injection };
  }

  await new Promise(resolve => setTimeout(resolve, 500));
  try {
    const bridge = await bridgeFetch('/v1/status', 'GET', null, 3000);
    return { ok: true, status: bridge.status || 'unknown', bridge, injection };
  } catch (error) {
    return {
      ok: false,
      status: 'loopback_bridge_unreachable',
      error: String(error?.message || error),
      injection
    };
  }
}

chrome.runtime.onInstalled.addListener(() => {
  ensureAllGeminiTabs().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  ensureAllGeminiTabs().catch(() => {});
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab.url || '';
  if ((changeInfo.status === 'complete' || changeInfo.url) && /^https:\/\/gemini\.google\.com\//.test(url)) {
    ensureGeminiRuntime(tabId).catch(() => {});
  }
});

// Launch background transport engine
startTransportEngine().catch(() => {});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'getRuntimeContract') {
    getRuntimeContract()
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  if (message?.type === 'ensureGeminiRuntime' || message?.type === 'reconnectGemini') {
    const tabId = sender.tab?.id;
    if (tabId) {
      ensureGeminiRuntime(tabId)
        .then(data => sendResponse(data))
        .catch(error => sendResponse({ ok: false, status: 'inject_failed', error: String(error?.message || error) }));
    } else {
      ensureAllGeminiTabs()
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, status: 'inject_failed', error: String(error?.message || error) }));
    }
    return true;
  }

  if (message?.type === 'diagnoseExtension') {
    diagnose()
      .then(data => sendResponse(data))
      .catch(error => sendResponse({ ok: false, status: 'diagnostic_failed', error: String(error?.message || error) }));
    return true;
  }

  if (message?.type === 'bridgeFetch') {
    bridgeFetch(message.path, message.method || 'GET', message.body || null)
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }

  return false;
});
