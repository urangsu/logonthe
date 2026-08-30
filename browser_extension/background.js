const BASE = "http://127.0.0.1:43127";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "bridgeFetch") return false;
  (async () => {
    const response = await fetch(`${BASE}${message.path}`, {
      method: message.method || "GET",
      headers: {
        "Content-Type": "application/json"
      },
      body: message.body ? JSON.stringify(message.body) : undefined
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `http_${response.status}`);
    sendResponse({ ok: true, data });
  })().catch(error => sendResponse({ ok: false, error: String(error.message || error) }));
  return true;
});
