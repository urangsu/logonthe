# Recommendation Detail Guards

## Execution Contract

Recommendation discovery returns at most one candidate per call. It does not
query author profiles. Existing URL normalization, duplicate suppression, and
card topic filtering remain in place.

The processor opens and verifies the article, prepares its content, then applies
enabled guards in order: neighbors, post reactions, daily visitors. A failed or
unknown guard excludes the entire post before Gemini, reaction clicks, or the
comment layer. Previously liked posts and comment-only mode follow the same
guard path. Other feed sources retain their existing policy.

Limits preserve their configured semantics: neighbors > maximum, reactions >=
threshold, daily visitors > threshold. Unknown metrics are never converted to
zero and do not trigger the old neighbor-lookup circuit breaker.

The reader uses the current author's detail profile first, then the existing
stats page for that author's mobile home only. Neighbor and visitor queries
reuse that home without a second navigation. Positive and explicitly private
metrics are cached within the processor run; transient failures, conflicts,
owner mismatches, and interruptions are not cached. Render/navigation failures
have at most one retry. Login redirects and closed browsers remain fatal.

Unconfirmed comments stay quarantined in recommendation runs. Pre-run and
pre-guard comment-layer recovery is not performed for this source; it must not
open excluded posts' comment layers or blindly retry an uncertain submission.

Excluded candidates retain the configured next-post pacing. Only an explicit
user skip bypasses it. RunControl checkpoints cover discovery, profile
navigation/read/render/retry, and reaction-summary/option click boundaries.

## Counts and Records

- The target counts distinct posts that passed guards and entered processing,
  including processing failures after a successful guard decision.
- Exclusions do not consume that target. The recommendation candidate scan cap
  is target * 5, including topic- and duplicate-blog exclusions.
- Runtime state separates visited, guard allowed, and guard excluded counts.
  Existing reaction/comment success counters remain separate.
- Optional history `eligibility` stores allowed/reason and metric value, raw
  text, source, error, and cache status. Older records remain readable.
- `POST_ELIGIBILITY` records each decision. `RECOMMENDATION_GUARD_SUMMARY`
  separates author lookups, home navigations, decisions, and examined candidates.
- Completion logs distinguish target_reached, scan_limit, candidates_exhausted,
  and user_stop.

## Verification

Focused tests are in `test_recommendation_post_guards.py`,
`test_post_eligibility_dom.py`, `test_neighbor_count_parser_and_filter.py`, and
`test_like_scroll_pacing_and_pause.py`.

Coverage includes threshold equality, disabled guards, comment-only exclusions,
zero side effects on exclusion, same-home reuse, cache policy, author mismatch,
pause after navigation/summary click, sequential processing, next-post pacing,
and a 40-target run with 200 excluded candidates.

DOM tests use local HTML only, including the supplied buddy span, hidden and
conflicting values, delayed rendering, explicit privacy, and today/total labels.
These are contract evidence, not proof of a logged-in production run.

## Outstanding Live Acceptance

Five-post live read-only acceptance is NOT complete. The browser tool rejected
access to the Naver mobile blog under its site-safety policy. No alternate
browser/profile, duplicate login, live reaction, or comment submission was used
to work around that restriction. Existing running GUI processes were not
restarted; they continue to use their already-loaded code until restarted.

Before production acceptance, run the revised pipeline against five consecutive
recommendation candidates with reaction/comment/Gemini operations replaced by
diagnostic stubs, while retaining the existing profile and timing settings.
Compare visit order, canonical author, visible counts, POST_ELIGIBILITY, and
pause acknowledgement. Confirm no second candidate/profile navigation until
the current decision and pacing have completed, and no navigation/read/click
after pause acknowledgement until resume. Disabled action toggles alone are
not a diagnostic mode because the controller skips posts with no work.

Do not mark LIVE_QA_PASS or automatically start a posting campaign based only
on unit tests or these local DOM fixtures.
