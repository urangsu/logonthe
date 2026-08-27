(options) => {
  "use strict";
  if (window.__naverHelper) window.__naverHelper.dispose();
  const policy = window.NaverHelperPolicy(options.policy);
  const editorSelector = '#naverComment__write_textarea,div.u_cbox_text[contenteditable="true"],div.u_cbox_write_box div[contenteditable="true"],textarea.u_cbox_text';
  const submitSelector = 'button[data-action="comment#upload"],button.u_cbox_btn_upload,input.u_cbox_btn_upload[type="submit"]';
  const nativeEditor = () => Array.from(document.querySelectorAll(editorSelector)).filter(el => el.getClientRects().length && (el.tagName === "TEXTAREA" || el.isContentEditable));
  const editorText = el => el.tagName === "TEXTAREA" ? el.value : el.innerText;
  let locked = Boolean(options.locked), busy = false, disposed = false;
  let status = options.draft.status || "none", pending = options.draft.pending || null;
  let inserted = options.draft.inserted || "";
  let nativeText = options.draft.nativeText || "";
  const host = document.createElement("aside");
  host.id = "naver-assistant-helper";
  const shadow = host.attachShadow({mode: "open"});
  const style = document.createElement("style");
  style.textContent = `
    :host{all:initial;position:fixed;z-index:2147483647;right:16px;top:16px;width:min(390px,calc(100vw - 32px));max-height:calc(100vh - 32px);color:var(--text-primary);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Noto Sans KR",sans-serif;}
    *{box-sizing:border-box} section{border:1px solid var(--border);border-radius:16px;background:var(--surface-panel);overflow:auto;max-height:calc(100vh - 32px);padding:16px;}
    h2{font-size:16px;margin:0 0 4px;color:var(--text-primary)} p{margin:6px 0;color:var(--text-secondary)}.header{display:flex;justify-content:space-between;align-items:center;gap:8px}.collapsed>:not(.header){display:none!important}
    label{display:block;margin:12px 0 5px;color:var(--text-secondary);font-size:12px}textarea,input{display:block;width:100%;font:inherit;color:var(--text-primary);background:var(--surface-base);border:1px solid var(--border);border-radius:8px;padding:8px;resize:vertical;}
    textarea:focus,input:focus,button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}textarea{min-height:76px}input{min-height:36px}
    button,a{font:inherit;font-size:13px;border:1px solid var(--border);border-radius:8px;padding:8px 10px;background:var(--surface-raised);color:var(--text-primary);cursor:pointer;text-decoration:none;display:inline-block;}
    button:disabled{opacity:.45;cursor:default}button.primary{background:var(--accent);color:var(--surface-base)}.row{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.notice{color:var(--warning)}
    output{white-space:pre-wrap;display:block;color:var(--text-secondary);font-size:12px;margin:8px 0;overflow-wrap:anywhere}.title{font-weight:600;color:var(--text-primary);overflow-wrap:anywhere}.small{font-size:12px}summary{cursor:pointer;color:var(--text-secondary)}
    @media(max-width:600px){:host{right:8px;top:8px;width:calc(100vw - 16px)}section{max-height:65vh}}
  `;
  for (const [name, value] of Object.entries(options.tokens)) host.style.setProperty("--" + name.replaceAll("_", "-"), value);
  shadow.append(style);
  const section = document.createElement("section");
  section.setAttribute("aria-label", "네이버 블로그 수동 댓글 도우미");
  section.innerHTML = `<div class="header"><h2>Naver Blog Assistant</h2><button id="toggle" aria-expanded="true">접기</button></div><p class="small">V1.1 · ChatGPT 수동 모드</p><p class="title" id="title"></p><p id="key" class="small"></p>
    <details><summary>본문 확인 및 직접 수정</summary><label for="excerpt">현재 글의 본문 발췌 (40–6000자)</label><textarea id="excerpt" rows="5"></textarea></details>
    <output id="body-state" aria-live="polite"></output><div class="row"><button id="copy">프롬프트 복사</button><button id="chatgpt">ChatGPT 열기</button></div>
    <details id="prompt-details" hidden><summary>복사가 안 되면 아래 내용을 직접 복사하세요</summary><textarea id="prompt" readonly aria-label="ChatGPT 프롬프트" rows="5"></textarea></details>
    <label for="answer">ChatGPT 답변 붙여넣기 · 직접 편집</label><textarea id="answer" rows="4" placeholder="답변을 여기에 붙여넣으세요"></textarea>
    <label for="suffix">접미문구 (선택 · 길이에 포함)</label><input id="suffix" type="text"><output id="validation" aria-live="polite"></output>
    <details id="native-saved" hidden><summary>네이버 입력창에서 보존한 내용</summary><textarea id="native-text" readonly aria-label="보존된 네이버 입력창 내용"></textarea><button id="restore-native">이 내용을 답변으로 복원</button></details>
    <div class="row"><button id="insert" class="primary">댓글창에 넣기</button><button id="like">공감</button><button id="check-like">공감 상태 확인</button></div>
    <p class="small">네이버 댓글창은 직접 여세요. 등록은 네이버의 등록 버튼을 직접 눌러야 합니다. Enter로 등록하지 않습니다.</p>
    <output id="status" role="status" aria-live="polite"></output><p class="small">수정한 초안은 이 기기에 자동 보존됩니다.</p>
    <div class="row"><button id="next">다음 글</button><button id="skip">건너뛰기</button><button id="stop">중지</button></div>`;
  shadow.append(section); document.documentElement.append(host);
  const $ = id => shadow.getElementById(id);
  $("title").textContent = options.title || "제목 확인 필요";
  $("key").textContent = options.postKey;
  $("excerpt").value = options.draft.excerpt ?? options.excerpt;
  $("answer").value = options.draft.answer || "";
  $("suffix").value = options.draft.suffix || "";
  $("native-text").value = nativeText;
  $("native-saved").hidden = !nativeText;
  let likeState = options.likeState || "unknown";
  function snapshot() {
    return {excerpt: $("excerpt").value, answer: $("answer").value, suffix: $("suffix").value, status, pending, inserted, nativeText};
  }
  function envelope(action, extra = {}) {
    return {postKey: options.postKey, navigationVersion: options.navigationVersion,
      requestId: crypto.randomUUID(), action, snapshot: snapshot(), ...extra};
  }
  function send(action, extra = {}) {
    if (disposed) return;
    return window.__naverHelperCommand(envelope(action, extra)).catch(() => {
      $("status").textContent = "연결이 끊겼습니다. 내용을 별도로 복사해 보관해 주세요.";
    });
  }
  function render() {
    const valid = policy.validate($("answer").value, $("suffix").value);
    $("validation").textContent = `${valid.length} / ${options.policy.maxLength}자` + (valid.valid ? " · 입력 가능" : "\n" + valid.reasons.join("\n"));
    const enough = policy.bodySufficient($("excerpt").value);
    $("body-state").textContent = enough ? "현재 글의 본문을 확인한 뒤 프롬프트를 복사하세요." : "본문이 부족하거나 너무 깁니다. 본문 확인을 펼쳐 직접 발췌를 입력하세요. 제목만으로 생성하지 않습니다.";
    $("copy").disabled = !enough || locked;
    $("insert").disabled = !valid.valid || locked || busy || status === "unknown" || status === "submitted";
    $("like").disabled = locked || busy || likeState !== "not_liked";
    $("like").textContent = likeState === "liked" ? "공감 완료" : likeState === "not_liked" ? "공감" : "공감 상태 불명";
    if (locked) $("status").textContent = "페이지가 변경되어 입력과 공감을 차단했습니다. 다음 글 또는 중지를 선택하세요.";
  }
  function trusted(id, fn) { $(id).addEventListener("click", event => { if (event.isTrusted) fn(event); }); }
  trusted("toggle", () => {
    const collapsed = section.classList.toggle("collapsed");
    $("toggle").textContent = collapsed ? "열기" : "접기";
    $("toggle").setAttribute("aria-expanded", String(!collapsed));
  });
  for (const field of ["excerpt", "answer", "suffix"]) $(field).addEventListener("input", () => { render(); send("save"); });
  trusted("copy", async () => {
    if (locked || !policy.bodySufficient($("excerpt").value)) return;
    try {
      const prompt = policy.prompt(options.title, $("excerpt").value, $("suffix").value);
      $("prompt").value = prompt;
      await navigator.clipboard.writeText(prompt);
      $("status").textContent = "프롬프트를 복사했습니다. ChatGPT에서 직접 붙여넣으세요.";
    } catch (error) {
      if ($("prompt").value) { $("prompt-details").hidden = false; $("prompt-details").open = true; $("prompt").focus(); $("prompt").select(); }
      $("status").textContent = "복사를 완료하지 못했습니다. " + error.message;
    }
  });
  // Open ChatGPT in the user's normal browser. Opening it with window.open
  // would create a new tab inside Playwright's managed Chromium, which Google
  // may reject as an unsafe automated browser during sign-in.
  trusted("chatgpt", () => send("open_chatgpt"));
  trusted("restore-native", () => {
    $("answer").value = nativeText; $("suffix").value = "";
    $("status").textContent = "보존된 최종 입력 내용을 복원했습니다. 접미문구가 중복되지 않도록 별도 접미문구는 비웠습니다.";
    render(); send("save");
  });
  trusted("insert", () => {
    if (locked || busy || !policy.validate($("answer").value, $("suffix").value).valid || ["unknown", "submitted"].includes(status)) return;
    const editors = nativeEditor();
    if (editors.length !== 1) { $("status").textContent = "네이버 댓글창을 직접 열어 주세요. 입력창을 하나로 확인할 수 없습니다."; return; }
    const previousText = editorText(editors[0]);
    if (previousText && !window.confirm("네이버 댓글창에 기존 내용이 있습니다. 현재 답변으로 덮어쓸까요?")) return;
    busy = true; render(); send("insert", {previousText});
  });
  trusted("like", () => { if (!locked && !busy && likeState === "not_liked") { busy = true; render(); send("like"); } });
  trusted("check-like", () => { if (!locked) send("check_like"); });
  for (const action of ["next", "skip", "stop"]) trusted(action, () => send(action));
  function mineComments() {
    return Array.from(document.querySelectorAll("li.u_cbox_comment")).filter(el => {
      const info = el.getAttribute("data-info") || "";
      return el.classList.contains("u_cbox_type_mine") || /(?:^|[,{\s])["']?mine["']?\s*:\s*true(?:[,}\s]|$)/i.test(info);
    }).map(el => {
      const info = el.getAttribute("data-info") || "";
      const match = info.match(/["']?commentNo["']?\s*:\s*["']?([0-9]+)/i);
      return {id: el.getAttribute("data-comment-no") || (match && match[1]) || el.id || null,
        text: policy.normalize((el.querySelector(".u_cbox_contents,.u_cbox_text_mention") || {}).innerText || "")};
    });
  }
  function enterGuard(event) {
    if (!(event.target instanceof Element) || !event.target.matches(editorSelector) || event.key !== "Enter") return;
    event.stopImmediatePropagation();
    if (!event.isComposing && event.keyCode !== 229) event.preventDefault();
  }
  function nativeClick(event) {
    if (!event.isTrusted || locked || !(event.target instanceof Element) || !event.target.closest(submitSelector)) return;
    const editors = nativeEditor();
    if (editors.length !== 1) return;
    const text = policy.normalize(editorText(editors[0]));
    if (!text.trim()) return;
    pending = {text, baseline: mineComments().map(item => item.id).filter(Boolean), clickedAt: Date.now()};
    status = "unknown";
    $("status").textContent = "등록 버튼 클릭을 관찰했습니다. 내 댓글이 새로 표시되어야 등록 완료로 확인합니다. 확인 전에는 재입력하지 않습니다.";
    render(); send("native_submit");
  }
  function nativeInput(event) {
    if (!event.isTrusted || locked || !(event.target instanceof Element) || !event.target.matches(editorSelector)) return;
    nativeText = editorText(event.target);
    $("native-text").value = nativeText; $("native-saved").hidden = !nativeText;
    if (!["unknown", "submitted"].includes(status)) status = "drafted";
    render(); send("save");
  }
  document.addEventListener("keydown", enterGuard, true);
  document.addEventListener("keypress", enterGuard, true);
  document.addEventListener("keyup", enterGuard, true);
  document.addEventListener("click", nativeClick, true);
  document.addEventListener("input", nativeInput, true);
  const urlTimer = setInterval(() => { if (location.href !== options.url) { locked = true; render(); } }, 75);
  window.__naverHelper = {
    postKey: options.postKey, navigationVersion: options.navigationVersion, snapshot,
    observe() {
      if (locked || location.href !== options.url || !pending || status !== "unknown") return false;
      const found = mineComments().some(item => item.id && !pending.baseline.includes(item.id) && item.text === pending.text);
      if (found) { const evidence = mineComments().find(item => item.id && !pending.baseline.includes(item.id) && item.text === pending.text); pending = {...pending, observed: evidence}; status = "submitted"; $("status").textContent = "등록 확인: 새로 표시된 내 댓글의 내용과 식별자가 일치합니다."; render(); send("observed_submit"); }
      return found;
    },
    response(data) {
      if (data.postKey !== options.postKey || data.navigationVersion !== options.navigationVersion) return;
      busy = false;
      // Status transitions are owned by the worker's observed event. A page cannot
      // promote itself to submitted through this public rendering callback.
      if (data.inserted !== undefined) inserted = data.inserted;
      if (data.likeState) likeState = data.likeState;
      $("status").textContent = data.message || ""; render();
    },
    insert(data) {
      if (locked || location.href !== options.url || data.postKey !== options.postKey || data.navigationVersion !== options.navigationVersion || ["unknown", "submitted"].includes(status)) return {ok:false,reason:"stale_command"};
      const checked = policy.validate($("answer").value, $("suffix").value);
      if (!checked.valid || checked.text !== data.text) return {ok:false,reason:"draft_changed"};
      const editors = nativeEditor();
      if (editors.length !== 1) return {ok:false,reason:"editor_unavailable"};
      const editor = editors[0];
      if (editorText(editor) !== data.previousText) return {ok:false,reason:"editor_changed"};
      if (editor.tagName === "TEXTAREA") Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(editor, data.text);
      else editor.textContent = data.text;
      editor.dispatchEvent(new Event("input", {bubbles:true}));
      editor.dispatchEvent(new Event("change", {bubbles:true}));
      editor.focus();
      if (editorText(editor) !== data.text) return {ok:false,reason:"readback_mismatch"};
      inserted = data.text; nativeText = data.text; status = "drafted"; send("save");
      return {ok:true};
    },
    like(data) {
      if (locked || location.href !== options.url || data.postKey !== options.postKey || data.navigationVersion !== options.navigationVersion) return {ok:false,reason:"stale_command"};
      const all = Array.from(document.querySelectorAll("a.u_likeit_list_button[data-type],button.u_likeit_list_button[data-type],a.u_likeit_list_btn[data-type],button.u_likeit_list_btn[data-type]"));
      if (!all.length || !all.every(el => {
        const on = ["aria-pressed", "aria-checked", "aria-selected"].some(a => el.getAttribute(a) === "true") || ["on", "_on", "active"].some(c => el.classList.contains(c));
        const off = ["aria-pressed", "aria-checked", "aria-selected"].some(a => el.getAttribute(a) === "false") || el.classList.contains("off");
        return off && !on;
      })) return {ok:false,reason:"like_state_unknown_or_liked"};
      const choices = all.filter(el => el.getAttribute("data-type") === "like" && el.getClientRects().length && el.getAttribute("aria-disabled") !== "true" && !el.disabled);
      if (choices.length !== 1) return {ok:false,reason:"like_option_not_visible"};
      choices[0].click();
      return {ok:true};
    },
    dispose() {
      disposed = true; clearInterval(urlTimer);
      document.removeEventListener("keydown", enterGuard, true); document.removeEventListener("keypress", enterGuard, true); document.removeEventListener("keyup", enterGuard, true); document.removeEventListener("click", nativeClick, true);
      document.removeEventListener("input", nativeInput, true);
      host.remove(); delete window.__naverHelper;
    }
  };
  if (status === "unknown") $("status").textContent = "이전 등록 결과를 확인하지 못했습니다. 네이버에서 직접 확인하세요. 자동 재시도하지 않습니다.";
  else if (status === "submitted") $("status").textContent = "이 기기에 등록 관찰 기록이 있습니다. 중복 입력을 차단합니다.";
  render();
  return true;
}
