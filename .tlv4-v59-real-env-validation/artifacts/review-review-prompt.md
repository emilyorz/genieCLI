You are the independent OPUS-tier reviewer for Task Ledger V4 step `review` in genieCLI v59.

Review ONLY the review producer artifact and, if needed, the repo diff:
/Users/leeabc/work/emilyorz/genieCLI/.tlv4-v59-real-env-validation/artifacts/review.producer.md

Expected artifact hash:
bc50c7dda24966a71b5bba4cab33679b0de0c6c0a88ba6c5cc6b84a35ba75315

Criteria:
- Is the review artifact truthful and evidence-backed?
- Does it catch the important commit discipline issue (explicit paths only due unrelated untracked .tlv4 dirs)?
- Does it confirm no production behavior changes / no new rewrite strategy?
- Does it correctly distinguish local representative validation vs live pending?
- Any hard fail that should block commit/wrap?

Output valid JSON only with exact shape:
{
  "score": number,
  "hard_fails": [string],
  "open_issues": [string],
  "pass_bool": boolean,
  "reviewer_dispatch_id": "review-reviewer-a0",
  "reviewed_artifact_hash": "bc50c7dda24966a71b5bba4cab33679b0de0c6c0a88ba6c5cc6b84a35ba75315"
}
