'use strict';

const BASE = 'http://127.0.0.1:43127';
let runtimeContractCache = null;
const injectionInFlight = new Map();

async function getRuntimeContract() {
  if (runtimeContractCache) return runtimeContractCache;
  const url = chrome.runtime.getURL('runtime_contract.json');
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`runtime_contract_http_${res.status}`);
  runtimeContractCache = await res.json();
  return runtimeContractCache;
}

async function bridgeFetch(path, method = 'GET', body = null) {
  try {
    const response = await fetch(`${BASE}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `http_${response.status}`);
    return data;
  } catch (error) {
    const isNetwork = /fetch|connect|network|econnrefused/i.test(String(error?.message || error));
    if (isNetwork) {
      throw new Error('loopback_bridge_unreachable');
    }
    throw error;
  }
}

async function pingRuntime(tabId) {
  return new Promise(resolve => {
    try {
      chrome.tabs.sendMessage(tabId, { type: 'NFA_RUNTIME_PING' }, response => {
        if (chrome.runtime.lastError || !response?.alive) {
          resolve({ ok: false, error: chrome.runtime.lastError?.message || 'no_response' });
        } else {
          resolve({ ok: true, build: response.build, status: response.status, instanceId: response.instanceId });
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
    // 1. Check if an active, valid content runtime is responding to ping
    const ping = await pingRuntime(tabId);
    if (ping.ok && ping.build === contract.runtimeBuild) {
      return { ok: true, status: 'already_loaded', build: ping.build };
    }

    // 2. Ping failed or outdated build -> inject fresh content.js
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js']
    });

    // 3. Verify injection via live ping
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

async function diagnose() {
  const injection = await ensureAllGeminiTabs();
  if (injection.tabsFound === 0) {
    return { ok: false, status: 'gemini_tab_not_found', injection };
  }

  await new Promise(resolve => setTimeout(resolve, 1000));
  try {
    const bridge = await bridgeFetch('/v1/status');
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

// Re-inject into Gemini tabs on service worker wake
ensureAllGeminiTabs().catch(() => {});

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

  if (message?.type !== 'bridgeFetch') return false;

  bridgeFetch(message.path, message.method || 'GET', message.body || null)
    .then(data => sendResponse({ ok: true, data }))
    .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
  return true;
});
