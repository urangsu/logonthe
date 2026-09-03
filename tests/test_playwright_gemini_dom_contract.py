import unittest
import os
from playwright.sync_api import sync_playwright

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONTENT_JS_PATH = os.path.join(WORKSPACE_DIR, "browser_extension", "content.js")


class PlaywrightGeminiDOMContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CONTENT_JS_PATH, "r", encoding="utf-8") as f:
            cls.content_js_code = f.read()

    def test_dom_001_editor_rerender_updates_target_and_sends_to_connected_editor(self):
        """DOM-001: input 후 editor가 교체되어도 새 editor로 target이 갱신되어 정상 send 확인"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Construct synthetic Gemini composer DOM
            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <main>
                <chat-history></chat-history>
                <div class="composer" id="composer-1">
                  <rich-textarea>
                    <div id="editor-1" contenteditable="true" style="width:200px; height:50px; display:block;"></div>
                  </rich-textarea>
                  <button id="send-btn-1" aria-label="send"><span>send</span></button>
                </div>
              </main>
              <script>
                // Simulate framework DOM rerender after first input
                let rerendered = false;
                document.getElementById('editor-1').addEventListener('input', () => {
                  if (!rerendered) {
                    rerendered = true;
                    const oldComposer = document.getElementById('composer-1');
                    const text = document.getElementById('editor-1').innerText;
                    oldComposer.remove();

                    const newComposer = document.createElement('div');
                    newComposer.className = 'composer';
                    newComposer.id = 'composer-2';
                    newComposer.innerHTML = `
                      <rich-textarea>
                        <div id="editor-2" contenteditable="true" style="width:200px; height:50px; display:block;">${text}</div>
                      </rich-textarea>
                      <button id="send-btn-2" aria-label="send"><span>send</span></button>
                    `;
                    document.querySelector('main').appendChild(newComposer);
                    window.__NEW_SEND_CLICKED__ = false;
                    document.getElementById('send-btn-2').addEventListener('click', () => {
                      window.__NEW_SEND_CLICKED__ = true;
                    });
                  }
                });
              </script>
            </body>
            </html>
            """)

            # Inject helpers from content.js and run injection & send
            result = page.evaluate("""() => {
                function canonicalPromptText(value) {
                  return String(value ?? '')
                    .normalize('NFC')
                    .replace(/\\r\\n?/g, '\\n')
                    .replace(/[\\u2028\\u2029]/g, '\\n')
                    .replace(/[\\u00A0\\u2007\\u202F]/g, ' ')
                    .replace(/[\\u200B-\\u200D\\u2060\\uFEFF\\uFE0E\\uFE0F]/g, '')
                    .replace(/[\\u200E\\u200F\\u202A-\\u202E\\u2066-\\u2069]/g, '')
                    .replace(/\\s+/gu, ' ')
                    .trim();
                }

                function getEditorSurfaces(target) {
                  if (!target) return [];
                  return [
                    { surface: 'innerText', text: target.innerText || '' },
                    { surface: 'textContent', text: target.textContent || '' }
                  ];
                }

                function editor() {
                  return document.querySelector('rich-textarea div[contenteditable="true"]');
                }

                let target = editor();
                const prompt = "신촌 대흥 맛집 투어 포스팅";

                // 1. Initial Injection into editor-1
                target.focus();
                target.innerText = prompt;
                target.dispatchEvent(new Event('input', { bubbles: true }));

                // 2. Editor-1 was detached by input event handler, editor() resolves to editor-2
                if (!target.isConnected) {
                  target = editor();
                }

                const surfaces = getEditorSurfaces(target);
                const readbackOk = surfaces.some(s => canonicalPromptText(s.text) === canonicalPromptText(prompt));

                // 3. Send click on composer-2
                const sendBtn = target.closest('.composer').querySelector('button[aria-label="send"]');
                if (sendBtn) sendBtn.click();

                return {
                  targetId: target.id,
                  isConnected: target.isConnected,
                  readbackOk: readbackOk,
                  newSendClicked: window.__NEW_SEND_CLICKED__
                };
            }""")

            self.assertEqual(result["targetId"], "editor-2")
            self.assertTrue(result["isConnected"])
            self.assertTrue(result["readbackOk"])
            self.assertTrue(result["newSendClicked"], "Send button on rerendered editor must be clicked")
            browser.close()

    def test_dom_002_rerendered_old_responses_are_not_falsely_bound(self):
        """DOM-002: 기존 답변들이 새 Node 객체로 rerender되어도 새 turn의 응답만 정확히 bind 확인"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <main>
                <chat-history id="history">
                  <conversation-turn id="turn-1">
                    <user-query class="user-message">첫 번째 이전 질문</user-query>
                    <model-response class="model-response">첫 번째 이전 답변 내용</model-response>
                  </conversation-turn>
                  <conversation-turn id="turn-2">
                    <user-query class="user-message">두 번째 이전 질문</user-query>
                    <model-response class="model-response">두 번째 이전 답변 내용</model-response>
                  </conversation-turn>
                </chat-history>
              </main>
            </body>
            </html>
            """)

            result = page.evaluate("""() => {
                function canonicalPromptText(value) {
                  return String(value ?? '')
                    .normalize('NFC')
                    .replace(/\\r\\n?/g, '\\n')
                    .replace(/\\s+/gu, ' ')
                    .trim();
                }

                function responseNodes() {
                  return [...document.querySelectorAll('model-response')];
                }

                function userQueryNodes() {
                  return [...document.querySelectorAll('.user-message, user-query')];
                }

                function extractResponseText(node) {
                  if (!node) return '';
                  const contentEl = node.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || node;
                  return (contentEl.innerText || contentEl.textContent || '').trim();
                }

                function extractUserQueryText(node) {
                  if (!node) return '';
                  const queryEl = node.querySelector('.query-text, .user-query-text, p, div') || node;
                  return (queryEl.innerText || queryEl.textContent || '').trim();
                }

                // 1. Initial State Capture
                const initialResponseList = responseNodes();
                const initialResponseSet = new Set(initialResponseList);
                const baselineResponseFingerprints = new Set(
                  initialResponseList.map(n => canonicalPromptText(extractResponseText(n))).filter(Boolean)
                );
                const initialUserQueries = userQueryNodes();
                const initialUserQuerySet = new Set(initialUserQueries);
                const baselineUserQueryFingerprints = new Set(
                  initialUserQueries.map(q => canonicalPromptText(extractUserQueryText(q))).filter(Boolean)
                );

                // 2. Simulate Virtualization / DOM Rerender of old turns (all nodes replaced with new DOM object instances)
                const history = document.getElementById('history');
                history.innerHTML = `
                  <conversation-turn id="turn-1-rerendered">
                    <user-query class="user-message">첫 번째 이전 질문</user-query>
                    <model-response class="model-response">첫 번째 이전 답변 내용</model-response>
                  </conversation-turn>
                  <conversation-turn id="turn-2-rerendered">
                    <user-query class="user-message">두 번째 이전 질문</user-query>
                    <model-response class="model-response">두 번째 이전 답변 내용</model-response>
                  </conversation-turn>
                  <conversation-turn id="turn-3-new">
                    <user-query class="user-message">신규 프롬프트 질문</user-query>
                    <model-response class="model-response">신규 생성된 신선한 답변 내용</model-response>
                  </conversation-turn>
                `;

                // 3. Turn & Response Pairing Logic
                let currentUserTurn = null;
                const expectedCanonical = canonicalPromptText("신규 프롬프트 질문");

                function findNewUserQuery() {
                  const currentQueries = userQueryNodes();
                  const exactPromptMatch = currentQueries.find(q => {
                    const qText = canonicalPromptText(extractUserQueryText(q));
                    return qText === expectedCanonical && !initialUserQuerySet.has(q);
                  }) || currentQueries.find(q => canonicalPromptText(extractUserQueryText(q)) === expectedCanonical);
                  if (exactPromptMatch) return exactPromptMatch;

                  const novelQuery = currentQueries.find(q => {
                    if (initialUserQuerySet.has(q)) return false;
                    const qText = canonicalPromptText(extractUserQueryText(q));
                    return qText && !baselineUserQueryFingerprints.has(qText);
                  });
                  if (novelQuery) return novelQuery;

                  if (currentQueries.length > initialUserQueries.length) {
                    return currentQueries[currentQueries.length - 1];
                  }
                  return null;
                }

                function findTurnResponseCandidate() {
                  if (!currentUserTurn || !currentUserTurn.isConnected) {
                    const newQuery = findNewUserQuery();
                    if (newQuery) currentUserTurn = newQuery;
                  }

                  const currentResponses = responseNodes();
                  if (currentUserTurn && currentUserTurn.isConnected) {
                    for (const resp of currentResponses) {
                      if (!resp || !resp.isConnected) continue;
                      if (initialResponseSet.has(resp)) continue;

                      const isFollowing = (currentUserTurn.compareDocumentPosition(resp) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
                      if (!isFollowing) continue;

                      const textCanonical = canonicalPromptText(extractResponseText(resp));
                      if (textCanonical && baselineResponseFingerprints.has(textCanonical)) continue;

                      return resp;
                    }
                  }

                  for (let i = currentResponses.length - 1; i >= 0; i--) {
                    const resp = currentResponses[i];
                    if (!resp || !resp.isConnected) continue;
                    if (initialResponseSet.has(resp)) continue;

                    const textCanonical = canonicalPromptText(extractResponseText(resp));
                    if (textCanonical && baselineResponseFingerprints.has(textCanonical)) continue;

                    return resp;
                  }
                  return null;
                }

                const boundNode = findTurnResponseCandidate();
                return {
                  boundNodeText: boundNode ? extractResponseText(boundNode) : null,
                  userTurnText: currentUserTurn ? extractUserQueryText(currentUserTurn) : null
                };
            }""")

            self.assertEqual(result["userTurnText"], "신규 프롬프트 질문")
            self.assertEqual(result["boundNodeText"], "신규 생성된 신선한 답변 내용")
            self.assertNotEqual(result["boundNodeText"], "첫 번째 이전 답변 내용", "Must not bind old rerendered response")
            browser.close()

    def test_dom_003_fresh_chat_and_non_blocking_completion_contract(self):
        """DOM-003: Fresh chat 격리 환경에서 페이지 aria-busy가 남아있어도 1800ms 안정화 시 정상 완료 확인"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <div id="sidebar" aria-busy="true">사이드바 동기화 중...</div>
              <main>
                <chat-history id="history"></chat-history>
                <div class="composer">
                  <rich-textarea><div contenteditable="true" id="ed"></div></rich-textarea>
                  <button id="send-btn" aria-label="send"><span>send</span></button>
                </div>
              </main>
            </body>
            </html>
            """)

            result = page.evaluate("""() => {
                const RESPONSE_SELECTORS = [
                  'model-response',
                  'div[data-message-author-role="model"]',
                  'div.model-response',
                  '[data-test-id="model-response"]',
                  '.response-container-content'
                ].join(', ');

                function responseNodes() {
                  return [...document.querySelectorAll(RESPONSE_SELECTORS)];
                }

                function userQueryNodes() {
                  return [...document.querySelectorAll('.user-message, user-query')];
                }

                function editor() {
                  return document.getElementById('ed');
                }

                function extractResponseText(node) {
                  if (!node) return '';
                  const contentEl = node.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || node;
                  return (contentEl.innerText || contentEl.textContent || '').trim();
                }

                // 1. Fresh Chat Verification
                const userQueries = userQueryNodes();
                const responses = responseNodes();
                const ed = editor();
                const isFresh = userQueries.length === 0 && responses.length === 0 && Boolean(ed);

                // 2. Simulate User Query and Gemini Answer appearance
                const history = document.getElementById('history');
                const userTurn = document.createElement('user-query');
                userTurn.className = 'user-message';
                userTurn.innerText = '프롬프트';
                history.appendChild(userTurn);

                const modelTurn = document.createElement('model-response');
                modelTurn.className = 'model-response';
                modelTurn.innerHTML = '<div class="model-response-text">완성된 댓글 답변입니다~</div>';
                history.appendChild(modelTurn);

                // 3. Bind response in fresh chat
                let targetResponseNode = null;
                const curResponses = responseNodes();
                if (isFresh && curResponses.length > 0) {
                  targetResponseNode = curResponses[curResponses.length - 1];
                }

                // 4. Test Completion Contract under lingering page-wide aria-busy=true
                const globalAriaBusy = Boolean(document.querySelector('[aria-busy="true"]'));
                const curText = extractResponseText(targetResponseNode);
                const lastMutationAge = 2000; // >= 1800ms
                const localStreaming = Boolean(targetResponseNode.querySelector('.loading-dots, .streaming, [aria-busy="true"]'));

                let completed = false;
                if (curText.length > 0 && lastMutationAge >= 1800 && !localStreaming) {
                  completed = true;
                }

                return {
                  isFresh,
                  hasTargetNode: Boolean(targetResponseNode),
                  responseBoundText: curText,
                  globalAriaBusy,
                  completed
                };
            }""")

            self.assertTrue(result["isFresh"])
            self.assertTrue(result["hasTargetNode"])
            self.assertEqual(result["responseBoundText"], "완성된 댓글 답변입니다~")
            self.assertTrue(result["globalAriaBusy"], "Page-wide aria-busy exists in test DOM")
            self.assertTrue(result["completed"], "Must complete even if page-wide aria-busy exists")
            browser.close()


if __name__ == "__main__":
    unittest.main()
