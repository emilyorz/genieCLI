You are the independent reviewer for Task Ledger V4 step `ticket`.

Review ONLY this artifact:
/Users/leeabc/work/emilyorz/genieCLI/.tlv4-v59-real-env-validation/artifacts/ticket.producer.md

Expected producer artifact hash:
888c108c70af8c997236902e66a1d63956c7b7c091ed0d09d5f3572af33163bf

Review criteria:
- Is the ticket actionable for Develop?
- Is scope narrow enough for v59?
- Does it avoid fake company-environment claims?
- Does it include concrete files and validation commands?
- Does it include exact `## Files affected` header?
- Does it explicitly keep new rewrite strategies out of scope?

Output must be valid JSON only with this exact shape:
{
  "score": number,
  "hard_fails": [string],
  "open_issues": [string],
  "pass_bool": boolean,
  "reviewer_dispatch_id": "ticket-reviewer-a0",
  "reviewed_artifact_hash": "888c108c70af8c997236902e66a1d63956c7b7c091ed0d09d5f3572af33163bf"
}

Pass only if score > 9, hard_fails is empty, and the ticket is safe/actionable.
