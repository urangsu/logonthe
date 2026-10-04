# Effective Target and Comment Quality Repair

## 2026-10-04 Changes

- Goal remains the number of unique posts with a submitted comment OR a newly
  verified like. It is not a comment-only goal.
- A dispatched click with NOT_LIKED or UNKNOWN after-state is not a successful like.
- Existing likes do not inflate the new-like statistic.
- Goal registration runs at one per-post finalization boundary, including exceptions
  after a verified like. Re-registering the same post has no effect.
- The candidate limit prevents new collection, not processing of an existing queue.
  Incoming batches are truncated to remaining candidate capacity.
- Decoration settings explicitly set to false override learned ratios. Missing
  settings may still use reviewed learning data.
- Food anchors require a recognized menu/ingredient root, including compounds.
  Unknown title nouns are not treated as dishes just because they appear near a
  restaurant-review keyword. When unsupported, the generic fallback remains.
- Recent submitted comments now reach the example selector. Recently reused
  examples and recurring phrase families lose priority. One valid reviewed example
  is retained instead of being replaced by a generic fallback.
- Non-food categories do not inherit food-category examples or fallback sentences.
- The active v3.5 prompt identifies repeated phrase families across the last five
  comments, rather than varying only their endings. These are variation hints,
  not new hard bans.
- PLACE, PRODUCT, and SERVICE reaction plans also use direct reactions instead of
  an observer/reporting voice. Factual and first-hand-experience guards remain.
- Active prompt revision: `varied-grounded-reaction-v4`.

## Evidence and Limits

The preceding run at 01:25-01:54 submitted 22 comments and activated 22 likes.
Among its final comments, 13 used "조합" and 11 used "이라니". This is the baseline,
not evidence of improved outputs from the new revision.

New regression tests first reproduced failed-like counting, duplicate registration,
queue loss at the candidate cap, missing goal registration after a comment exception,
decoration overrides, noisy food anchors, reused style examples, and repeated wording.
Controller tests mock browser and submission behavior and must not be described as
live posting acceptance.

The older Enter-retry test now supplies an eligible-post fixture so it tests Enter
retry behavior rather than an unrelated missing like-count DOM. Runtime-isolation
tests now use real config keys and mock sampling persistence to avoid writing into
the user's campaign history.

No running assistant was restarted. No likes, comments, browser login, external
corpus import, or additional crawling was performed. Validate actual Gemini drafts
and normalized output after an ordinary restart before claiming naturalness improved.
