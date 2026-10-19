# Private published-comment review

The Editorial desk → Audience & experiments inbox reads published YouTube
comments only after an owner selects a current, exact content mapping and clicks
Read next comment page. Opening the page never queries YouTube. Existing
`YOUTUBE_API_KEY` configuration is used for the read-only Data API; this workflow
creates no OAuth grant and has no sending, moderation, or public reply endpoint.
Instagram/Facebook feedback still uses explicitly authorized text imports.

## Durable pages and honest coverage

Each mapping has a top-level page cursor; each captured parent has a separate
reply cursor. Each click retrieves at most 100 resources, preserves its next-page
token, and commits valid entries and the cursor together. Stop/restart and source
mapping corrections fence late workers before promotion. A failed request keeps
the previous token and evidence. A repeated token or malformed/cross-video page
becomes unavailable without advancing the sequence. Restart keeps retained
entries; unchanged source versions deduplicate. The UI exposes provider rows,
new versions, duplicates, pages, errors and pagination exhaustion. Retained
feedback has private search and pagination; navigating search results keeps
previously seen parent choices accessible during the current selection.

A finished page sequence is a snapshot of accessible published resources at its
read times, not proof of all historical/private/held/deleted comments. Reported
reply counts and actual retained reply counts are shown separately. Missing or
mismatched context requires an explicit limited-context rationale before manual
review. Pagination cannot make an absence into a verified deletion.

## Privacy and exact review

API field selection excludes author names, author channel IDs and avatars. Common
URLs, email addresses, handles and phone numbers inside text are redacted before
persistence. Material ISO dates survive phone redaction. This is a bounded
heuristic, not a promise that all personal information has been identified. The
owner must inspect remaining text. Remote resource IDs are retained privately
for deduplication and threading. General media passes cannot access inbox,
source context or decision history. No public reuse is automatic.

Source evidence and private decisions are append-only in SQLite and PostgreSQL.
A source edit keeps the owner's draft words and invalidates approval. The review
version binds feedback creation identity, exact source evidence, current content
mapping, thread membership/coverage and saved draft. Stale saves return 409;
reused integer feedback IDs cannot inherit review history. Acknowledgment resets
when draft/context/rationale changes. Browser drafts use the creation identity,
provide restore/conflict controls and protect navigation when storage is failing.
A manual-review decision records the exact cleaned words, source context and
coverage rationale. Nothing is sent.

Corrections target exact retained research claims, narration sentences or native
beats. A proposal records its unverified reference/excerpt/rationale and creates
an unapproved source-review opportunity with empty evidence. It cannot mutate a
script or a published item. Exact retries return the same saved proposal and
follow-up receipt instead of creating duplicates. Independent source review is
still required before a factual correction is certified.

## Provider contract and verification limits

Google's official [commentThreads.list reference](https://developers.google.com/youtube/data/reference/rest/v3/commentThreads/list)
documents GET requests, plainText output, 1–100 resources, pageToken and published
moderation defaults. The [thread resource](https://developers.google.com/youtube/data/reference/rest/v3/commentThreads)
explains why embedded replies may be incomplete; the separate
[comments.list reference](https://developers.google.com/youtube/data/reference/rest/v3/comments/list)
documents parentId paging. This implementation requests the parent endpoint
explicitly, follows no redirects, and sanitizes upstream errors and credentials.
The API key uses the documented [Google system header](https://docs.cloud.google.com/apis/docs/system-parameters)
rather than a URL parameter, keeping normal HTTP request logs free of the key.
No real provider/account request was made while building this feature.

The committed browser scenario uses explicitly enabled synthetic comment responses
in the isolated app. HTTP/SQL/UI, pagination, local drafts, rendering the original
source image, mapping, source changes, review and correction decisions use actual
application code. Fixture query receipts explicitly say real_account_access=false.
Real authorized YouTube responses, quota behavior and long-lived provider-data
refresh/deletion operations remain separate acceptance lanes. Common-contact
redaction does not replace privacy review or a data-retention policy.
