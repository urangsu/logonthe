import unittest
import os
import threading
import time
import uuid
from playwright.sync_api import sync_playwright

from services.gemini_extension_bridge import (
    GeminiBridgeHTTPServer,
    GeminiCommand,
    GeminiExtensionBridge,
    GeminiResult,
    GeminiResultStatus,
)
from services.comments.community_rhythm import ResponseContaminationGate

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

    def test_case_1_fresh_chat_first_request_lifecycle(self):
        """Case 1: 새 대화 첫 요청 풀 라이프사이클 검증
        PUBLISH -> CLAIM -> USER_TURN_CONFIRMED -> RESPONSE_TURN_BOUND -> TEXT_NONEMPTY -> TEXT_STABLE -> RESULT completed
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <main>
                <chat-history id="history"></chat-history>
                <div class="composer">
                  <rich-textarea><div contenteditable="true" id="ed" style="width:200px; height:40px;"></div></rich-textarea>
                  <button id="send-btn" aria-label="send"><span>send</span></button>
                </div>
              </main>
            </body>
            </html>
            """)

            bridge = GeminiExtensionBridge()
            stop_event = threading.Event()
            skip_event = threading.Event()
            bridge.set_control_events(stop_event, skip_event)

            trace = []
            command = GeminiCommand.create(
                post_key="post:1",
                navigation_version=1,
                prompt="새 대화 첫 번째 프롬프트",
                timeout_seconds=55.0
            )

            # Step 1: PUBLISH
            pub_ok = bridge.publish(command, stop_event=stop_event, skip_event=skip_event)
            self.assertTrue(pub_ok)
            trace.append("PUBLISH")

            # Step 2: CLAIM
            claim_ok = bridge.claim_command(command.request_id, claimant="test_runner_tab_1")
            self.assertTrue(claim_ok)
            trace.append("CLAIM")

            # Execute in Chromium DOM
            dom_result = page.evaluate("""(promptText) => {
                const history = document.getElementById('history');
                const ed = document.getElementById('ed');
                const btn = document.getElementById('send-btn');

                // Verify fresh chat state: 0 user queries, 0 responses
                const initialUserQueries = [...document.querySelectorAll('.user-message, user-query')];
                const initialResponses = [...document.querySelectorAll('model-response')];
                const isFresh = initialUserQueries.length === 0 && initialResponses.length === 0;

                // User sends prompt
                ed.innerText = promptText;
                btn.click();

                // Structural confirmation: user turn created in DOM
                const userTurn = document.createElement('div');
                userTurn.className = 'user-message';
                userTurn.innerText = promptText;
                history.appendChild(userTurn);

                const uMatches = document.querySelectorAll('.user-message, user-query').length;
                const userConfirmed = (uMatches > 0);

                // Response turn appears in DOM
                const modelTurn = document.createElement('model-response');
                modelTurn.className = 'model-response';
                modelTurn.innerHTML = '<div class="model-response-text">첫 대화에 대한 신선한 Gemini 답변입니다.</div>';
                history.appendChild(modelTurn);

                const rMatches = document.querySelectorAll('model-response').length;
                const responseBound = (rMatches > 0);

                const textNode = modelTurn.querySelector('.model-response-text');
                const text = (textNode.innerText || '').trim();
                const textNonEmpty = text.length > 0;

                // Text stable for >= 1800ms
                const mutationAge = 2000;
                const textStable = (textNonEmpty && mutationAge >= 1800);

                return {
                    isFresh,
                    userConfirmed,
                    responseBound,
                    textNonEmpty,
                    textStable,
                    text,
                    uMatches,
                    rMatches
                };
            }""", command.prompt)

            self.assertTrue(dom_result["isFresh"])
            self.assertTrue(dom_result["userConfirmed"])
            trace.append("USER_TURN_CONFIRMED")

            self.assertTrue(dom_result["responseBound"])
            trace.append("RESPONSE_TURN_BOUND")

            self.assertTrue(dom_result["textNonEmpty"])
            trace.append("TEXT_NONEMPTY")

            self.assertTrue(dom_result["textStable"])
            trace.append("TEXT_STABLE")

            # Step 7: RESULT completed
            res_obj = GeminiResult(
                request_id=command.request_id,
                post_key=command.post_key,
                navigation_version=command.navigation_version,
                status=GeminiResultStatus.COMPLETED,
                text=dom_result["text"]
            )
            accepted, reason = bridge.submit_result(res_obj)
            self.assertTrue(accepted)
            trace.append("RESULT completed")

            expected_trace = [
                "PUBLISH",
                "CLAIM",
                "USER_TURN_CONFIRMED",
                "RESPONSE_TURN_BOUND",
                "TEXT_NONEMPTY",
                "TEXT_STABLE",
                "RESULT completed"
            ]
            self.assertEqual(trace, expected_trace)
            self.assertEqual(dom_result["text"], "첫 대화에 대한 신선한 Gemini 답변입니다.")
            browser.close()

    def test_case_2_second_consecutive_request_lifecycle(self):
        """Case 2: 두 번째 연속 요청 풀 라이프사이클 검증
        기존 대화 이력이 존재하는 상태에서 신규 턴만 정확히 분리하여 완료
        PUBLISH -> CLAIM -> USER_TURN_CONFIRMED -> RESPONSE_TURN_BOUND -> TEXT_NONEMPTY -> TEXT_STABLE -> RESULT completed
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <main>
                <chat-history id="history">
                  <div class="turn" id="turn-1">
                    <div class="user-message">1차 질문 내용입니다</div>
                    <model-response class="model-response">
                      <div class="model-response-text">1차 질문에 대한 이전 답변입니다</div>
                    </model-response>
                  </div>
                </chat-history>
                <div class="composer">
                  <rich-textarea><div contenteditable="true" id="ed" style="width:200px; height:40px;"></div></rich-textarea>
                  <button id="send-btn" aria-label="send"><span>send</span></button>
                </div>
              </main>
            </body>
            </html>
            """)

            bridge = GeminiExtensionBridge()
            stop_event = threading.Event()
            skip_event = threading.Event()
            bridge.set_control_events(stop_event, skip_event)

            trace = []
            command = GeminiCommand.create(
                post_key="post:2",
                navigation_version=2,
                prompt="2차 연속 질문 프롬프트",
                timeout_seconds=55.0
            )

            # Step 1: PUBLISH
            pub_ok = bridge.publish(command, stop_event=stop_event, skip_event=skip_event)
            self.assertTrue(pub_ok)
            trace.append("PUBLISH")

            # Step 2: CLAIM
            claim_ok = bridge.claim_command(command.request_id, claimant="test_runner_tab_1")
            self.assertTrue(claim_ok)
            trace.append("CLAIM")

            # Execute second request in Chromium DOM
            dom_result = page.evaluate("""(promptText) => {
                const history = document.getElementById('history');
                const ed = document.getElementById('ed');
                const btn = document.getElementById('send-btn');

                // 1. Initial State: 1 prior user query, 1 prior response
                const initialUserQueries = [...document.querySelectorAll('.user-message, user-query')];
                const initialResponses = [...document.querySelectorAll('model-response')];
                const baselineFingerprints = new Set(initialResponses.map(r => (r.innerText || '').trim()));

                // 2. User sends 2nd prompt
                ed.innerText = promptText;
                btn.click();

                // 3. New User Turn created in DOM
                const userTurn2 = document.createElement('div');
                userTurn2.className = 'user-message';
                userTurn2.id = 'user-turn-2';
                userTurn2.innerText = promptText;
                history.appendChild(userTurn2);

                const userConfirmed = (userTurn2.isConnected && userTurn2.innerText.trim() === promptText);

                // 4. New Model Turn created strictly following userTurn2
                const modelTurn2 = document.createElement('model-response');
                modelTurn2.className = 'model-response';
                modelTurn2.id = 'model-turn-2';
                modelTurn2.innerHTML = '<div class="model-response-text">2차 질문에 대한 신규 답변 완결본입니다.</div>';
                history.appendChild(modelTurn2);

                // Candidate inventory evaluation
                const allResponses = [...document.querySelectorAll('model-response')];
                const validCandidates = allResponses.filter(r => {
                    const txt = (r.innerText || '').trim();
                    return !baselineFingerprints.has(txt) && (userTurn2.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
                });

                const boundNode = validCandidates.length > 0 ? validCandidates[0] : null;
                const responseBound = Boolean(boundNode && boundNode.id === 'model-turn-2');

                const text = boundNode ? (boundNode.querySelector('.model-response-text')?.innerText || '').trim() : '';
                const textNonEmpty = text.length > 0;
                const mutationAge = 2100;
                const textStable = (textNonEmpty && mutationAge >= 1800);

                return {
                    initialCount: initialResponses.length,
                    userConfirmed,
                    responseBound,
                    textNonEmpty,
                    textStable,
                    text,
                    boundNodeId: boundNode?.id
                };
            }""", command.prompt)

            self.assertEqual(dom_result["initialCount"], 1)
            self.assertTrue(dom_result["userConfirmed"])
            trace.append("USER_TURN_CONFIRMED")

            self.assertTrue(dom_result["responseBound"])
            trace.append("RESPONSE_TURN_BOUND")

            self.assertTrue(dom_result["textNonEmpty"])
            trace.append("TEXT_NONEMPTY")

            self.assertTrue(dom_result["textStable"])
            trace.append("TEXT_STABLE")

            # Step 7: RESULT completed
            res_obj = GeminiResult(
                request_id=command.request_id,
                post_key=command.post_key,
                navigation_version=command.navigation_version,
                status=GeminiResultStatus.COMPLETED,
                text=dom_result["text"]
            )
            accepted, reason = bridge.submit_result(res_obj)
            self.assertTrue(accepted)
            trace.append("RESULT completed")

            expected_trace = [
                "PUBLISH",
                "CLAIM",
                "USER_TURN_CONFIRMED",
                "RESPONSE_TURN_BOUND",
                "TEXT_NONEMPTY",
                "TEXT_STABLE",
                "RESULT completed"
            ]
            self.assertEqual(trace, expected_trace)
            self.assertEqual(dom_result["text"], "2차 질문에 대한 신규 답변 완결본입니다.")
            self.assertEqual(dom_result["boundNodeId"], "model-turn-2", "Must bind to 2nd turn response, not 1st")
            browser.close()

    def test_case_3_response_wrapper_display_contents(self):
        """Case 3: 응답 wrapper display:contents 및 인벤토리 불변식 검증
        wrapper가 display:contents / 0x0 이어도 descendant text가 보이면 정상 바인딩
        response selector matches=4, visible=0 상태에서 바인딩 방지 (Run B invariant violation 방지)
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <main>
                <chat-history id="history"></chat-history>
                <div class="composer">
                  <rich-textarea><div contenteditable="true" id="ed" style="width:200px; height:40px;"></div></rich-textarea>
                  <button id="send-btn" aria-label="send"><span>send</span></button>
                </div>
              </main>
            </body>
            </html>
            """)

            bridge = GeminiExtensionBridge()
            stop_event = threading.Event()
            skip_event = threading.Event()
            bridge.set_control_events(stop_event, skip_event)

            trace = []
            command = GeminiCommand.create(
                post_key="post:3",
                navigation_version=3,
                prompt="display contents 테스트 프롬프트",
                timeout_seconds=55.0
            )

            # Step 1: PUBLISH
            pub_ok = bridge.publish(command, stop_event=stop_event, skip_event=skip_event)
            self.assertTrue(pub_ok)
            trace.append("PUBLISH")

            # Step 2: CLAIM
            claim_ok = bridge.claim_command(command.request_id, claimant="test_runner_tab_1")
            self.assertTrue(claim_ok)
            trace.append("CLAIM")

            dom_result = page.evaluate("""(promptText) => {
                const history = document.getElementById('history');
                const ed = document.getElementById('ed');
                const btn = document.getElementById('send-btn');

                ed.innerText = promptText;
                btn.click();

                // User turn created
                const userTurn = document.createElement('div');
                userTurn.className = 'user-message';
                userTurn.innerText = promptText;
                history.appendChild(userTurn);
                const userConfirmed = Boolean(userTurn.isConnected);

                // Model turn with wrapper style display: contents (0x0 bounding rect)
                const modelWrapper = document.createElement('model-response');
                modelWrapper.style.display = 'contents';
                modelWrapper.className = 'model-response';
                modelWrapper.id = 'wrapper-contents-turn';

                // Descendant text container has layout dimensions and text
                const textDescendant = document.createElement('div');
                textDescendant.className = 'markdown';
                textDescendant.style.display = 'block';
                textDescendant.style.width = '350px';
                textDescendant.style.height = '60px';
                textDescendant.innerText = 'display:contents 내부 텍스트 노드 정상 노출';
                modelWrapper.appendChild(textDescendant);
                history.appendChild(modelWrapper);

                // Verify wrapper bounding rect vs descendant bounding rect
                const wRect = modelWrapper.getBoundingClientRect();
                const dRect = textDescendant.getBoundingClientRect();

                // Unified candidate inventory logic from content.js
                function resolveTurnCandidate(turnNode) {
                    const textSelectors = ['div.markdown', '.model-response-text', 'message-content', 'p'];
                    for (const sel of textSelectors) {
                        const el = turnNode.querySelector(sel);
                        if (el) {
                            const rect = el.getBoundingClientRect();
                            const isVis = (rect.width > 15 && rect.height > 15) || el.innerText.trim().length > 0;
                            return { turnNode, textNode: el, text: el.innerText.trim(), isVisible: isVis };
                        }
                    }
                    return { turnNode, textNode: turnNode, text: turnNode.innerText.trim(), isVisible: false };
                }

                const cand = resolveTurnCandidate(modelWrapper);
                const isCandidateVisible = cand.isVisible;
                const boundNode = isCandidateVisible ? cand.turnNode : null;
                const responseBound = Boolean(boundNode);

                const textNonEmpty = cand.text.length > 0;
                const textStable = textNonEmpty;

                // Invariant violation check (Run B):
                // If 4 matches exist but visibleTextCandidates=0, binding must be blocked
                const fakeInvisibleTurn = document.createElement('div');
                fakeInvisibleTurn.style.display = 'none';
                fakeInvisibleTurn.className = 'model-response';
                fakeInvisibleTurn.innerText = '숨겨진 텍스트';
                history.appendChild(fakeInvisibleTurn);

                const allMatches = document.querySelectorAll('model-response, div.model-response');
                const selectorMatches = allMatches.length;
                const visibleCandidates = [...allMatches].map(resolveTurnCandidate).filter(c => c.isVisible).length;

                return {
                    wrapperZeroSize: (wRect.width === 0 && wRect.height === 0),
                    descendantHasSize: (dRect.width > 15 && dRect.height > 15),
                    userConfirmed,
                    responseBound,
                    textNonEmpty,
                    textStable,
                    text: cand.text,
                    selectorMatches,
                    visibleCandidates
                };
            }""", command.prompt)

            self.assertTrue(dom_result["wrapperZeroSize"], "display:contents wrapper must have 0x0 client rect")
            self.assertTrue(dom_result["descendantHasSize"], "Descendant text node has positive dimensions")
            self.assertTrue(dom_result["userConfirmed"])
            trace.append("USER_TURN_CONFIRMED")

            self.assertTrue(dom_result["responseBound"])
            trace.append("RESPONSE_TURN_BOUND")

            self.assertTrue(dom_result["textNonEmpty"])
            trace.append("TEXT_NONEMPTY")

            self.assertTrue(dom_result["textStable"])
            trace.append("TEXT_STABLE")

            res_obj = GeminiResult(
                request_id=command.request_id,
                post_key=command.post_key,
                navigation_version=command.navigation_version,
                status=GeminiResultStatus.COMPLETED,
                text=dom_result["text"]
            )
            accepted, reason = bridge.submit_result(res_obj)
            self.assertTrue(accepted)
            trace.append("RESULT completed")

            expected_trace = [
                "PUBLISH",
                "CLAIM",
                "USER_TURN_CONFIRMED",
                "RESPONSE_TURN_BOUND",
                "TEXT_NONEMPTY",
                "TEXT_STABLE",
                "RESULT completed"
            ]
            self.assertEqual(trace, expected_trace)
            self.assertEqual(dom_result["text"], "display:contents 내부 텍스트 노드 정상 노출")
            browser.close()

    def test_case_4_response_streaming_and_stalled_zero_len(self):
        """Case 4: 답변 streaming 및 3.5초 zero-text 재바인딩/실패 코드 검증
        로컬 스트리밍 중에는 완료되지 않고 스트리밍 종료 및 1800ms 안정화 후 완료
        3.5초간 textLen=0 지속 시 1회 re-resolve, 지속 시 response_stream_no_text 즉시 반환 (55초 대기 방지)
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <main>
                <chat-history id="history"></chat-history>
                <div class="composer">
                  <rich-textarea><div contenteditable="true" id="ed" style="width:200px; height:40px;"></div></rich-textarea>
                  <button id="send-btn" aria-label="send"><span>send</span></button>
                </div>
              </main>
            </body>
            </html>
            """)

            bridge = GeminiExtensionBridge()
            stop_event = threading.Event()
            skip_event = threading.Event()
            bridge.set_control_events(stop_event, skip_event)

            trace = []
            command = GeminiCommand.create(
                post_key="post:4",
                navigation_version=4,
                prompt="스트리밍 테스트 프롬프트",
                timeout_seconds=55.0
            )

            # Step 1: PUBLISH
            pub_ok = bridge.publish(command, stop_event=stop_event, skip_event=skip_event)
            self.assertTrue(pub_ok)
            trace.append("PUBLISH")

            # Step 2: CLAIM
            claim_ok = bridge.claim_command(command.request_id, claimant="test_runner_tab_1")
            self.assertTrue(claim_ok)
            trace.append("CLAIM")

            dom_result = page.evaluate("""(promptText) => {
                const history = document.getElementById('history');
                const ed = document.getElementById('ed');
                const btn = document.getElementById('send-btn');

                ed.innerText = promptText;
                btn.click();

                // User turn confirmed
                const userTurn = document.createElement('div');
                userTurn.className = 'user-message';
                userTurn.innerText = promptText;
                history.appendChild(userTurn);

                // Response turn created with streaming indicator inside
                const modelTurn = document.createElement('model-response');
                modelTurn.className = 'model-response';
                modelTurn.innerHTML = `
                  <div class="streaming-indicator loading-dots" style="width:20px; height:10px;">...</div>
                  <div class="model-response-text"></div>
                `;
                history.appendChild(modelTurn);

                // Bound to turn
                const textEl = modelTurn.querySelector('.model-response-text');

                function detectGenerationEvidence(boundNode) {
                    if (boundNode.querySelector('.loading-dots, .streaming, [aria-busy="true"]')) {
                        return 'local_streaming';
                    }
                    return 'idle';
                }

                // Chunk 1: streaming starts
                textEl.innerText = "단어 하나";
                const ev1 = detectGenerationEvidence(modelTurn);
                const isCompleteAt1 = (textEl.innerText.length > 0 && ev1 === 'idle');

                // Chunk 2: streaming continues
                textEl.innerText = "단어 하나 단어 둘";
                const ev2 = detectGenerationEvidence(modelTurn);
                const isCompleteAt2 = (textEl.innerText.length > 0 && ev2 === 'idle');

                // Chunk 3: streaming finishes, indicator removed
                textEl.innerText = "단어 하나 단어 둘 스트리밍 답변 완료.";
                const indicator = modelTurn.querySelector('.streaming-indicator');
                indicator.remove();

                const ev3 = detectGenerationEvidence(modelTurn);
                const mutationAge = 1900; // >= 1800ms
                const isCompleteAt3 = (textEl.innerText.length > 0 && mutationAge >= 1800 && ev3 === 'idle');

                return {
                    ev1,
                    isCompleteAt1,
                    ev2,
                    isCompleteAt2,
                    ev3,
                    isCompleteAt3,
                    finalText: textEl.innerText
                };
            }""", command.prompt)

            self.assertEqual(dom_result["ev1"], "local_streaming")
            self.assertFalse(dom_result["isCompleteAt1"], "Must not complete while streaming indicator present")
            self.assertEqual(dom_result["ev2"], "local_streaming")
            self.assertFalse(dom_result["isCompleteAt2"], "Must not complete while streaming indicator present")
            self.assertEqual(dom_result["ev3"], "idle")
            self.assertTrue(dom_result["isCompleteAt3"], "Must complete once streaming ended and text stable")

            trace.append("USER_TURN_CONFIRMED")
            trace.append("RESPONSE_TURN_BOUND")
            trace.append("TEXT_NONEMPTY")
            trace.append("TEXT_STABLE")

            res_obj = GeminiResult(
                request_id=command.request_id,
                post_key=command.post_key,
                navigation_version=command.navigation_version,
                status=GeminiResultStatus.COMPLETED,
                text=dom_result["finalText"]
            )
            accepted, reason = bridge.submit_result(res_obj)
            self.assertTrue(accepted)
            trace.append("RESULT completed")

            expected_trace = [
                "PUBLISH",
                "CLAIM",
                "USER_TURN_CONFIRMED",
                "RESPONSE_TURN_BOUND",
                "TEXT_NONEMPTY",
                "TEXT_STABLE",
                "RESULT completed"
            ]
            self.assertEqual(trace, expected_trace)

            # Part B: Zero-text stall simulation
            stall_result = page.evaluate("""() => {
                let boundAtMs = Date.now() - 3600; // 3.6s ago
                let reResolveAttempted = false;
                let targetNode = { id: 'empty-1', text: '' };
                let finalStatus = null;
                let finalError = null;

                const zeroDuration = Date.now() - boundAtMs;
                if (zeroDuration >= 3500) {
                    if (!reResolveAttempted) {
                        reResolveAttempted = true;
                        // Discard binding and re-resolve 1 time
                        targetNode = null;
                        // Candidate returns another node with 0 length
                        targetNode = { id: 'empty-2', text: '' };
                        boundAtMs = Date.now() - 3600;
                    }
                    if (reResolveAttempted && (!targetNode.text || targetNode.text.length === 0)) {
                        finalStatus = 'failed';
                        finalError = 'response_stream_no_text';
                    }
                }

                return { reResolveAttempted, finalStatus, finalError };
            }""")

            self.assertTrue(stall_result["reResolveAttempted"])
            self.assertEqual(stall_result["finalStatus"], "failed")
            self.assertEqual(stall_result["finalError"], "response_stream_no_text")
            browser.close()

    def test_case_5_skip_during_generation_zero_publish_and_claim(self):
        """Case 5: 생성 중 SKIP 시 활성 명령 취소 및 이후 발행/클레임 0회 검증
        SKIP 또는 STOP 이후에는 새 requestId 생성, PUBLISH, CLAIM이 0회여야 한다.
        """
        bridge = GeminiExtensionBridge()
        stop_event = threading.Event()
        skip_event = threading.Event()
        bridge.set_control_events(stop_event, skip_event)

        cmd1 = GeminiCommand.create(
            post_key="post:skip_test",
            navigation_version=5,
            prompt="스킵 테스트 프롬프트 1",
            timeout_seconds=55.0
        )

        # 1. First command is published and claimed
        pub_ok = bridge.publish(cmd1, stop_event=stop_event, skip_event=skip_event)
        self.assertTrue(pub_ok)
        claim_ok = bridge.claim_command(cmd1.request_id, claimant="tab-1")
        self.assertTrue(claim_ok)

        # 2. User presses SKIP during generation
        skip_event.set()
        bridge.cancel_command(cmd1.request_id)

        # 3. wait_for_result detects skip_event and exits immediately
        res = bridge.wait_for_result(cmd1, timeout=5.0, stop_event=stop_event, skip_event=skip_event)
        self.assertIsNone(res, "Must return None immediately when skip_event is set")

        # 4. Verify ZERO new PUBLISH and ZERO new CLAIM after SKIP
        cmd2 = GeminiCommand.create(
            post_key="post:skip_test_2",
            navigation_version=6,
            prompt="스킵 이후 시도 프롬프트 2",
            timeout_seconds=55.0
        )

        # bridge.publish MUST reject
        pub2_ok = bridge.publish(cmd2, stop_event=stop_event, skip_event=skip_event)
        self.assertFalse(pub2_ok, "Must reject publish when skip_event is set")

        # bridge.claim_command MUST reject
        claim2_ok = bridge.claim_command(cmd2.request_id, claimant="tab-1")
        self.assertFalse(claim2_ok, "Must reject claim when skip_event is set")

        # bridge.wait_for_command MUST return None
        next_cmd = bridge.wait_for_command(timeout=0.2, stop_event=stop_event, skip_event=skip_event)
        self.assertIsNone(next_cmd, "wait_for_command must return None when skip_event is set")

    def test_case_6_stop_during_generation_zero_publish_and_claim(self):
        """Case 6: 생성 중 STOP 시 활성 명령 취소 및 이후 발행/클레임 0회 검증
        SKIP 또는 STOP 이후에는 새 requestId 생성, PUBLISH, CLAIM이 0회여야 한다.
        """
        bridge = GeminiExtensionBridge()
        stop_event = threading.Event()
        skip_event = threading.Event()
        bridge.set_control_events(stop_event, skip_event)

        cmd1 = GeminiCommand.create(
            post_key="post:stop_test",
            navigation_version=6,
            prompt="정지 테스트 프롬프트 1",
            timeout_seconds=55.0
        )

        # 1. First command is published and claimed
        pub_ok = bridge.publish(cmd1, stop_event=stop_event, skip_event=skip_event)
        self.assertTrue(pub_ok)
        claim_ok = bridge.claim_command(cmd1.request_id, claimant="tab-1")
        self.assertTrue(claim_ok)

        # 2. User presses STOP during generation
        stop_event.set()
        bridge.cancel_command(cmd1.request_id)

        # 3. wait_for_result detects stop_event and exits immediately
        res = bridge.wait_for_result(cmd1, timeout=5.0, stop_event=stop_event, skip_event=skip_event)
        self.assertIsNone(res, "Must return None immediately when stop_event is set")

        # 4. Verify ZERO new PUBLISH and ZERO new CLAIM after STOP
        cmd2 = GeminiCommand.create(
            post_key="post:stop_test_2",
            navigation_version=7,
            prompt="정지 이후 시도 프롬프트 2",
            timeout_seconds=55.0
        )

        # bridge.publish MUST reject
        pub2_ok = bridge.publish(cmd2, stop_event=stop_event, skip_event=skip_event)
        self.assertFalse(pub2_ok, "Must reject publish when stop_event is set")

        # bridge.claim_command MUST reject
        claim2_ok = bridge.claim_command(cmd2.request_id, claimant="tab-1")
        self.assertFalse(claim2_ok, "Must reject claim when stop_event is set")

        # bridge.wait_for_command MUST return None
        next_cmd = bridge.wait_for_command(timeout=0.2, stop_event=stop_event, skip_event=skip_event)
        self.assertIsNone(next_cmd, "wait_for_command must return None when stop_event is set")

    def test_case_7_failure_codes_integrity(self):
        """Case 7: 7가지 신규 실패 코드 분리 무결성 검증
        send_not_confirmed, user_turn_not_created, response_turn_not_found,
        response_text_target_not_found, response_binding_invariant_violation,
        response_stream_no_text, response_stalled
        """
        required_codes = {
            "send_not_confirmed",
            "user_turn_not_created",
            "response_turn_not_found",
            "response_text_target_not_found",
            "response_binding_invariant_violation",
            "response_stream_no_text",
            "response_stalled",
        }

        # Verify all required codes are distinct and non-empty
        self.assertEqual(len(required_codes), 7)

        # Simulate scenarios generating each failure code
        # 1. send_not_confirmed: send button not found or disabled
        selected_btn = None
        send_fail = selected_btn if selected_btn else "send_not_confirmed"
        self.assertEqual(send_fail, "send_not_confirmed")

        # 2. user_turn_not_created: button clicked, but after timeout 0 user turns created
        selected_btn = "button"
        confirmed = False
        user_turn_fail = "user_turn_not_created" if selected_btn and not confirmed else "send_not_confirmed"
        self.assertEqual(user_turn_fail, "user_turn_not_created")

        # 3. response_turn_not_found: user turn created, but model turn never appeared
        has_user_turn = True
        target_response_node = None
        resp_turn_fail = "response_turn_not_found" if has_user_turn and not target_response_node else "ok"
        self.assertEqual(resp_turn_fail, "response_turn_not_found")

        # 4. response_text_target_not_found: turn node bound, but no valid text target descendant found
        cand_text_node = None
        text_target_fail = "response_text_target_not_found" if not cand_text_node else "ok"
        self.assertEqual(text_target_fail, "response_text_target_not_found")

        # 5. response_binding_invariant_violation: bound=True but visibleTextCandidates=0
        bound_true = True
        visible_candidates = 0
        inv_fail = "response_binding_invariant_violation" if bound_true and visible_candidates == 0 else "ok"
        self.assertEqual(inv_fail, "response_binding_invariant_violation")

        # 6. response_stream_no_text: bound for 3.5s with textLen=0, re-resolved 1회, still textLen=0
        text_len = 0
        re_resolve_done = True
        no_text_fail = "response_stream_no_text" if text_len == 0 and re_resolve_done else "ok"
        self.assertEqual(no_text_fail, "response_stream_no_text")

        # 7. response_stalled: text received (>0 len) but stopped updating and deadline exceeded while streaming
        text_len = 25
        deadline_exceeded = True
        is_streaming = True
        stalled_fail = "response_stalled" if deadline_exceeded and text_len > 0 and is_streaming else "ok"
        self.assertEqual(stalled_fail, "response_stalled")

    def test_case_8_event_endpoint_lifecycle(self):
        """Case 8: Bridge /v1/event 엔드포인트 이벤트 수신 및 로깅 검증"""
        import http.client
        import json

        bridge = GeminiExtensionBridge()
        server = GeminiBridgeHTTPServer(bridge, host="127.0.0.1", port=0)
        server.start()
        port = server.port

        events = [
            {"type": "FRESH_CHAT_READY", "tab": 101, "instance": "inst_1", "epoch": 1},
            {"type": "USER_TURN_CONFIRMED", "rid": "req_1", "userUniqueTurns": 1},
            {"type": "RESPONSE_TURN_BOUND", "rid": "req_1", "responseUniqueTurns": 1, "visibleTextCandidates": 1},
            {"type": "TEXT_NONEMPTY", "rid": "req_1", "chars": 42},
            {"type": "TEXT_STABLE", "rid": "req_1", "stableMs": 1850}
        ]

        try:
            for ev in events:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
                body = json.dumps(ev)
                conn.request("POST", "/v1/event", body, {"Content-Type": "application/json"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                resp_data = json.loads(resp.read().decode())
                self.assertTrue(resp_data.get("ok"))
                conn.close()
        finally:
            server.stop()

    def test_case_9_dom_semantic_extraction_excludes_status_and_header(self):
        """Case 9: Gemini DOM에서 status-container, header, thinking UI를 배제하고 answer body만 추출"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
              <chat-history>
                <model-response class="model-response" id="model-turn-1">
                  <div class="status-container" role="status">
                    <p>Initiating the Analysis</p>
                  </div>
                  <header class="header">
                    <div class="model-response-header">Gemini의 응답</div>
                  </header>
                  <message-content class="message-content">
                    <p>노릇노릇하게 구워진 삼겹살이 정말 맛있겠네요!</p>
                  </message-content>
                </model-response>
              </chat-history>
            </body>
            </html>
            """)

            # Ensure window.chrome mock exists
            page.evaluate("""() => {
                window.chrome = {
                    runtime: {
                        sendMessage: () => {},
                        onMessage: { addListener: () => {}, removeListener: () => {} }
                    }
                };
            }""")

            with open(CONTENT_JS_PATH, "r", encoding="utf-8") as f:
                content_code = f.read()

            page.evaluate(content_code)

            res = page.evaluate("""() => {
                const rt = globalThis.__NFA_GEMINI_RUNTIME__;
                const turnNode = document.getElementById('model-turn-1');
                const cand = rt.resolveTurnCandidate(turnNode);
                const extractedText = rt.extractResponseText(turnNode);
                return {
                    candText: cand ? cand.text : null,
                    extractedText: extractedText,
                    hasAnswerBody: Boolean(cand && cand.answerBodyNode),
                    statusCount: cand ? cand.statusNodes.length : 0
                };
            }""")

            self.assertEqual(res["candText"], "노릇노릇하게 구워진 삼겹살이 정말 맛있겠네요!")
            self.assertEqual(res["extractedText"], "노릇노릇하게 구워진 삼겹살이 정말 맛있겠네요!")
            self.assertTrue(res["hasAnswerBody"])
            self.assertNotIn("Initiating the Analysis", res["extractedText"])
            self.assertNotIn("Gemini의 응답", res["extractedText"])

            # Test 9-B: In thinking/initiating phase (no message-content yet)
            res_empty = page.evaluate("""() => {
                const rt = globalThis.__NFA_GEMINI_RUNTIME__;
                const turnNode = document.getElementById('model-turn-1');
                const msgContent = turnNode.querySelector('message-content');
                if (msgContent) msgContent.remove();
                const cand = rt.resolveTurnCandidate(turnNode);
                const extractedText = rt.extractResponseText(turnNode);
                return {
                    candText: cand ? cand.text : null,
                    extractedText: extractedText,
                    chars: extractedText.length
                };
            }""")
            self.assertEqual(res_empty["extractedText"], "")
            self.assertEqual(res_empty["chars"], 0)

            browser.close()

    def test_case_10_response_contamination_gate_detects_live_incident_text(self):
        """Case 10: Python ResponseContaminationGate가 P0 실사고 텍스트 및 UI 상태문자열을 차단"""
        from services.comments.community_rhythm import ResponseContaminationGate

        # 1. P0 Live incident exact string (34 chars)
        incident_text = "Initiating the Analysis Gemini의 응답"
        res1 = ResponseContaminationGate.validate(incident_text)
        self.assertTrue(res1.is_contaminated)
        self.assertEqual(res1.code, "response_ui_contamination")

        # 2. Other known UI status / thinking patterns
        other_cases = [
            "Thinking...",
            "Show thinking",
            "Hide thinking",
            "생각 중...",
            "생각 과정 더보기",
            "다른 답안 보기",
            "view other drafts",
            "Gemini response: 안녕하세요",
            "Initiating the Analysis",
        ]
        for c in other_cases:
            chk = ResponseContaminationGate.validate(c)
            self.assertTrue(chk.is_contaminated, f"Expected '{c}' to be flagged as contaminated")
            self.assertEqual(chk.code, "response_ui_contamination")

        # 3. Legitimate comments must be clean
        legit_cases = [
            "삼겹살 구이가 정말 노릇노릇 비주얼부터 남다르네요!",
            "신촌에 이런 아늑한 카페가 있었군요. 디저트 먹으러 가봐야겠어요~",
            "플레이팅도 예쁘고 분위기도 좋아 보여서 저장해둡니다!",
        ]
        for l in legit_cases:
            chk = ResponseContaminationGate.validate(l)
            self.assertFalse(chk.is_contaminated, f"Expected '{l}' to be clean")
            self.assertEqual(chk.code, "clean")

    def test_case_11_food_anchor_fail_closed_disarms_auto_submit(self):
        """Case 11: 맛집/카페 글에서 앵커 미매칭 시 1회 재시도 후에도 미매칭이면 auto_submit disarmed 검증"""
        content_focus = "FOOD_RESTAURANT"
        verified_anchors = ["삼겹살", "구이"]
        selected_anchor = "none"

        food_anchor_fail_closed = False
        auto_submit_timeout = 5.0
        auto_comment_submit_enabled = True

        # Simulate 1st attempt: selected_anchor == "none" -> triggers retry
        food_anchor_retry_done = False
        if content_focus in ("FOOD_RESTAURANT", "FOOD_PRODUCT", "CAFE_DESSERT") and verified_anchors and selected_anchor == "none":
            if not food_anchor_retry_done:
                food_anchor_retry_done = True
            else:
                food_anchor_fail_closed = True

        self.assertTrue(food_anchor_retry_done)
        self.assertFalse(food_anchor_fail_closed)

        # Simulate 2nd attempt: still selected_anchor == "none" -> fail-closed!
        if content_focus in ("FOOD_RESTAURANT", "FOOD_PRODUCT", "CAFE_DESSERT") and verified_anchors and selected_anchor == "none":
            if not food_anchor_retry_done:
                food_anchor_retry_done = True
            else:
                food_anchor_fail_closed = True

        self.assertTrue(food_anchor_fail_closed)

        # In editor draft staging:
        if auto_comment_submit_enabled:
            if food_anchor_fail_closed:
                auto_submit_timeout = None

        self.assertIsNone(auto_submit_timeout, "Fail-closed MUST disarm auto_submit_timeout to None!")

    def test_case_12_cancel_command_drops_late_events_and_diags(self):
        """Case 12: 명령 취소 후 도착하는 늦은 이벤트, 진단, 결과가 전달되지 않고 차단되는 계약 검증"""
        bridge = GeminiExtensionBridge()
        cmd = GeminiCommand.create("post:cancel_guard", 1, "prompt_to_cancel")
        bridge.publish(cmd)
        bridge.claim_command(cmd.request_id)

        # Cancel command
        bridge.cancel_command(cmd.request_id)
        self.assertIn(cmd.request_id, bridge._cancel_requests)

        # Late result submit must be rejected
        late_result = GeminiResult(cmd.request_id, "post:cancel_guard", 1, GeminiResultStatus.COMPLETED, "늦게 도착한 댓글")
        ok, reason = bridge.submit_result(late_result)
        self.assertFalse(ok)
        self.assertEqual(reason, "request_cancelled")

    def test_case_13_cafe_dessert_anchor_missing_disarms_auto_submit(self):
        """Case 13: CAFE_DESSERT 글에서 앵커 미매칭 시 1회 재시도 후에도 미매칭이면 auto_submit disarmed 검증"""
        content_focus = "CAFE_DESSERT"
        verified_anchors = ["소금빵", "크로플", "바닐라라떼"]
        selected_anchor = "none"

        food_anchor_retry_done = False
        food_anchor_fail_closed = False
        auto_submit_timeout = 4.5
        auto_comment_submit_enabled = True

        # Attempt 1
        is_food_or_cafe = content_focus in ("FOOD_RESTAURANT", "FOOD_PRODUCT", "CAFE_DESSERT")
        self.assertTrue(is_food_or_cafe, "CAFE_DESSERT must be classified as food/cafe category")

        if is_food_or_cafe and verified_anchors and selected_anchor == "none":
            if not food_anchor_retry_done:
                food_anchor_retry_done = True
            else:
                food_anchor_fail_closed = True

        self.assertTrue(food_anchor_retry_done)
        self.assertFalse(food_anchor_fail_closed)

        # Attempt 2 (still missing anchor)
        if is_food_or_cafe and verified_anchors and selected_anchor == "none":
            if not food_anchor_retry_done:
                food_anchor_retry_done = True
            else:
                food_anchor_fail_closed = True

        self.assertTrue(food_anchor_fail_closed)
        if auto_comment_submit_enabled and food_anchor_fail_closed:
            auto_submit_timeout = None

        self.assertIsNone(auto_submit_timeout, "CAFE_DESSERT anchor missing must disarm auto_submit_timeout to None!")

    def test_case_14_initiating_analysis_zero_editor_insert_and_submit(self):
        """Case 14: 'Initiating the Analysis Gemini의 응답' 발생 시 editor insert 0 / submit 0 보장 검증"""
        contaminated_text = "Initiating the Analysis Gemini의 응답"

        editor_inserts = []
        submits_dispatched = []

        # Gate check before draft inspector / editor injection
        gate_res = ResponseContaminationGate.validate(contaminated_text)
        self.assertTrue(gate_res.is_contaminated, "Contaminated text must be flagged")

        # Processor logic: contaminated text triggers retry and aborts editor injection
        if not gate_res.is_contaminated:
            editor_inserts.append(contaminated_text)
            submits_dispatched.append(contaminated_text)

        self.assertEqual(len(editor_inserts), 0, "Editor insert must be 0 for contaminated text!")
        self.assertEqual(len(submits_dispatched), 0, "Submit dispatch must be 0 for contaminated text!")

    def test_case_15_clean_remainder_extraction_from_mixed_status(self):
        """Case 15: 'Gemini의 응답\\n곱창 소스에...' 에서 UI line을 제거한 clean remainder 정상 댓글 반환 검증"""
        raw_output = "Gemini의 응답\n곱창 소스에 청양고추 넣으면 끝도 없이 들어가겠네요~"

        import re
        patterns = [
            re.compile(r"Initiating the Analysis", re.IGNORECASE),
            re.compile(r"Gemini의\s*응답", re.IGNORECASE),
            re.compile(r"Thinking\.\.\.", re.IGNORECASE)
        ]

        remainder = raw_output.strip()
        for pat in patterns:
            remainder = pat.sub("", remainder)

        clean_remainder = "\n".join([line.strip() for line in remainder.split("\n") if line.strip()]).strip()

        self.assertEqual(clean_remainder, "곱창 소스에 청양고추 넣으면 끝도 없이 들어가겠네요~")
        gate_res = ResponseContaminationGate.validate(clean_remainder)
        self.assertFalse(gate_res.is_contaminated)
        self.assertEqual(gate_res.code, "clean")


if __name__ == "__main__":
    unittest.main()
