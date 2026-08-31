'use strict';

const BASE = 'http://127.0.0.1:43127';

const txtGeminiTabs = document.getElementById('txtGeminiTabs');
const txtContentBuild = document.getElementById('txtContentBuild');
const txtRuntimePing = document.getElementById('txtRuntimePing');
const txtBridgeTarget = document.getElementById('txtBridgeTarget');
const txtLoopbackPerm = document.getElementById('txtLoopbackPerm');
const txtDirectProbe = document.getElementById('txtDirectProbe');
const txtHeartbeatStatus = document.getElementById('txtHeartbeatStatus');
const txtHeartbeatAge = document.getElementById('txtHeartbeatAge');
const btnRecover = document.getElementById('btnRecover');

// 1. Check Loopback / Local Network Access Permission directly in foreground
async function queryLoopbackPermission() {
  if (!navigator?.permissions?.query) {
    return 'not_supported';
  }
  try {
    const status = await navigator.permissions.query({ name: 'loopback-network' });
    return status.state; // 'granted' | 'prompt' | 'denied'
  } catch (_) {
    try {
      const status = await navigator.permissions.query({ name: 'local-network' });
      return status.state;
    } catch (_) {
      return 'not_supported';
    }
  }
}

// 2. Direct Foreground Fetch to trigger LNA prompt and probe Python HTTP Bridge
async function directForegroundProbe() {
  try {
    const res = await fetch(`${BASE}/v1/status`, {
      method: 'GET',
      cache: 'no-store',
      headers: { 'Accept': 'application/json' }
    });
    if (!res.ok) {
      return { ok: false, status: res.status, error: `HTTP ${res.status}` };
    }
    const data = await res.json();
    return { ok: true, status: 200, data };
  } catch (err) {
    const msg = String(err?.message || err);
    if (/permission|blocked|denied|security/i.test(msg)) {
      return { ok: false, status: 0, error: 'permission_denied', msg };
    }
    return { ok: false, status: 0, error: 'unreachable', msg };
  }
}

async function refreshUI() {
  // Load runtime contract
  chrome.runtime.sendMessage({ type: 'getRuntimeContract' }, (res) => {
    if (res?.ok && res.data) {
      txtContentBuild.textContent = `${res.data.extensionVersion} (${res.data.runtimeBuild})`;
    }
  });

  // Query LNA permission state
  const permState = await queryLoopbackPermission();
  if (permState === 'granted') {
    txtLoopbackPerm.innerHTML = '<span class="badge badge-ok">granted</span>';
  } else if (permState === 'denied') {
    txtLoopbackPerm.innerHTML = '<span class="badge badge-err">denied (차단됨)</span>';
  } else if (permState === 'prompt') {
    txtLoopbackPerm.innerHTML = '<span class="badge badge-warn">prompt (허용 필요)</span>';
  } else {
    txtLoopbackPerm.innerHTML = '<span class="badge badge-info">not_supported (기본)</span>';
  }

  // Execute direct foreground probe (CRITICAL: Runs in popup document, NOT background)
  const probe = await directForegroundProbe();
  if (probe.ok) {
    txtDirectProbe.innerHTML = '<span class="badge badge-ok">200 OK (연결 정상)</span>';
    const st = probe.data?.status || 'unknown';
    if (st === 'ready') {
      txtHeartbeatStatus.innerHTML = '<span class="badge badge-ok">ready</span>';
    } else if (st === 'heartbeat_never_received') {
      txtHeartbeatStatus.innerHTML = '<span class="badge badge-warn">heartbeat_never_received</span>';
    } else {
      txtHeartbeatStatus.innerHTML = `<span class="badge badge-warn">${st}</span>`;
    }

    if (typeof probe.data?.heartbeat_age_ms === 'number') {
      txtHeartbeatAge.textContent = `${probe.data.heartbeat_age_ms}ms`;
    } else {
      txtHeartbeatAge.textContent = '-';
    }
  } else {
    if (probe.error === 'permission_denied') {
      txtDirectProbe.innerHTML = '<span class="badge badge-err">LNA 권한 차단됨</span>';
    } else {
      txtDirectProbe.innerHTML = '<span class="badge badge-err">unreachable (앱 미실행)</span>';
    }
    txtHeartbeatStatus.innerHTML = '<span class="badge badge-err">bridge_unreachable</span>';
    txtHeartbeatAge.textContent = '-';
  }

  // Check Gemini tabs & ping runtime
  chrome.tabs.query({ url: '*://gemini.google.com/*' }, (tabs) => {
    if (!tabs || tabs.length === 0) {
      txtGeminiTabs.innerHTML = '<span class="badge badge-warn">0개 (탭 열림 필요)</span>';
      txtRuntimePing.innerHTML = '<span class="badge badge-warn">no_tab</span>';
    } else {
      txtGeminiTabs.innerHTML = `<span class="badge badge-ok">${tabs.length}개</span>`;
      chrome.tabs.sendMessage(tabs[0].id, { type: 'NFA_RUNTIME_PING' }, (ping) => {
        if (chrome.runtime.lastError || !ping?.alive) {
          txtRuntimePing.innerHTML = '<span class="badge badge-err">no_response (재주입 필요)</span>';
        } else {
          txtRuntimePing.innerHTML = `<span class="badge badge-ok">alive (${ping.status || 'active'})</span>`;
        }
      });
    }
  });
}

btnRecover.addEventListener('click', async () => {
  btnRecover.disabled = true;
  btnRecover.textContent = '연결 권한 확인 및 복구 중...';

  try {
    // 1. Explicit foreground probe on user interaction to grant LNA permission
    await directForegroundProbe();

    // 2. Request background service worker to ensure all Gemini tabs have live content runtime
    await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: 'ensureGeminiRuntime' }, () => {
        void chrome.runtime.lastError;
        resolve();
      });
    });

    await new Promise((r) => setTimeout(r, 600));
    await refreshUI();
  } finally {
    btnRecover.disabled = false;
    btnRecover.textContent = '로컬 브리지 연결 허용 및 자동 복구';
  }
});

refreshUI();
