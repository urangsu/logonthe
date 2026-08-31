const statusEl = document.getElementById('status');
const contractEl = document.getElementById('contract');
const diagnoseBtn = document.getElementById('diagnose');

function setStatus(text) {
  statusEl.textContent = text;
}

function describe(result) {
  if (!result) return '진단 결과 없음';

  const lines = [];
  lines.push(`상태: ${result.status || 'unknown'}`);

  const tabsFound = result.injection?.tabsFound;
  if (typeof tabsFound === 'number') lines.push(`Gemini 탭: ${tabsFound}개`);

  const injected = result.injection?.results || [];
  if (injected.length) {
    for (const item of injected) {
      lines.push(`탭 ${item.tabId}: ${item.status}${item.build ? ` (${item.build})` : ''}`);
    }
  }

  if (result.bridge) {
    lines.push(`Bridge: ${result.bridge.status || 'unknown'}`);
    if (result.bridge.extension_version) lines.push(`Extension: ${result.bridge.extension_version}`);
    if (result.bridge.content_build) lines.push(`Build: ${result.bridge.content_build}`);
    if (typeof result.bridge.heartbeat_age_ms === 'number') {
      lines.push(`Heartbeat age: ${result.bridge.heartbeat_age_ms}ms`);
    }
  }

  if (result.error) lines.push(`오류: ${result.error}`);
  return lines.join('\n');
}

async function loadContract() {
  chrome.runtime.sendMessage({ type: 'getRuntimeContract' }, response => {
    if (chrome.runtime.lastError) {
      contractEl.textContent = `계약 로드 실패: ${chrome.runtime.lastError.message}`;
      return;
    }
    if (!response?.ok) {
      contractEl.textContent = `계약 로드 실패: ${response?.error || 'unknown'}`;
      return;
    }
    const c = response.data || {};
    contractEl.textContent = `Version ${c.extensionVersion || '?'} / Build ${c.runtimeBuild || '?'} / Protocol ${c.protocolVersion || '?'}`;
  });
}

function runDiagnostic() {
  diagnoseBtn.disabled = true;
  setStatus('Gemini 탭 및 로컬 브리지 확인 중...');

  chrome.runtime.sendMessage({ type: 'diagnoseExtension' }, response => {
    diagnoseBtn.disabled = false;

    if (chrome.runtime.lastError) {
      setStatus(`진단 실패: ${chrome.runtime.lastError.message}`);
      return;
    }

    setStatus(describe(response));
  });
}

diagnoseBtn.addEventListener('click', runDiagnostic);
loadContract();
runDiagnostic();
