(() => {
  const KEY = '__NAVER_ASSISTANT_GEMINI_BOOTSTRAP_13_2_3__';
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  function ensureRuntime() {
    try {
      chrome.runtime.sendMessage({ type: 'ensureGeminiRuntime' }, () => {
        void chrome.runtime.lastError;
      });
    } catch (_) {}
  }

  ensureRuntime();
  window.addEventListener('pageshow', ensureRuntime);
  window.addEventListener('focus', ensureRuntime);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') ensureRuntime();
  });
})();
