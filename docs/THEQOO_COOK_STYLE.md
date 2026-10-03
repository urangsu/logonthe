# Cooking Comment Style Adaptation

## Evidence

On 2026-10-03, sampled the first six non-notice posts from
https://theqoo.net/cook?filter_mode=hot and each post's latest public comment page.
No login, private endpoint, older comment pagination, or access restriction bypass
was used. The anonymous page's CSRF token was supplied with its ordinary public
comment-list request. The initial request without this token was rejected.
The robots URL returned a not-found HTML page, not usable robots directives.
This sampling is not a guarantee of permission for unrestricted crawling.

The aggregate report is `THEQOO_COOK_STYLE_AUDIT.json`. It contains source URLs
and counts only, not comment text, member identifiers, or session cookies.
Author replies and deleted comments were excluded. Comments were not deduplicated:
these are observations, not counts of unique people or sentences.

- 111 visible comments; median length 22 characters.
- 77 were at most 40 characters.
- 29 opened with a short reaction; 16 contained laughter/tear markers.
- 18 contained a question; 10 ended with a period.

The sample includes both food showcases and a kitchen-injury discussion.
It is small and hot-post-selected, not representative of all Korean comments.
Laughter, slang, excitement, and missing punctuation must not become mandatory.

## Runtime Changes

- v3.5 revision: `casual-food-reaction-v3`.
- Food-domain prompts invite a short, immediate reaction rather than explanation.
- Food combination/taste plans no longer demand an "observer" voice or repeated
  paraphrasing of taste descriptions.
- Existing reviewed user examples retain priority. Crawled comments are never
  added to the user-edit corpus or sent to Gemini as evidence for another post.
- Non-food prompts receive no cooking-specific style instruction.
- Existing length, optional decoration, factual-grounding, duplicate, and submission
  guards remain unchanged. False first-hand experience is still forbidden.
- No runtime networking is added. No GUI restart or Naver interaction is performed.

Original adaptation examples below are written for this document, not copied from
the sampled comments and not fixed templates inserted into every prompt:

- If the post describes very thin dumpling wrappers:
  `와 만두피 얇은 거 보니까 속부터 눈에 들어오네요 ㅎㅎ`
- If the post shows gimbap served with tteokbokki:
  `김밥은 저 떡볶이 국물에 찍어 먹고 싶네요~`
- If the post describes cheese paired with honey:
  `치즈에 꿀 조합은 괜히 자꾸 생각날 것 같아요`

## Repeatable Audit

Optional audit-only dependency: `scripts/requirements-style-audit.txt`.
Run `python3 scripts/sample_theqoo_cook_style.py --output /tmp/cook-style-audit.json`.
It samples at most six posts, waits one second between requests, stores only
aggregate statistics, and stops on HTTP errors, schema changes, or access rejection.
It does not reuse or overwrite the older `theqoo_muk` collector or its data.

## Acceptance Boundary

Unit tests verify prompt inclusion, food-only scope, existing style policy,
compactness, source filtering, response parsing, and absence of raw text in statistics.
They do not establish that real Gemini outputs are more natural.
After restarting the assistant, inspect `revision=casual-food-reaction-v3` and compare
actual Gemini drafts with their final normalized comments on several distinct posts.
Do not retry an uncertain send or publish comments merely to perform this audit.
