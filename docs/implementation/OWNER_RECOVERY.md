# Owner-facing request and draft recovery

The shared browser client preserves typed HTTP status and Retry-After information
while showing a readable error. FastAPI validation errors identify the affected
field and message; raw validation inputs/context and proxy HTML are not displayed.
Error-body reading is bounded to16KiB. This changes message handling, not server
authorization or request ceilings. No client retry loop was added.

| Situation | Owner-visible behavior |
|---|---|
| Invalid input | Field/message explanation; form words stay available for repair |
| Changed or busy context | Conflict message; refresh and review the current version |
| Rate limit | Server delay is retained and shown; retry remains explicit |
| Refused credentials | Cached for the exact API origin/token pair, allowing a new connection to recover |
| Network response missing | Response is unconfirmed; check the current item before retrying |
| Save confirmed, view refresh failed | The confirmed save remains a success; refresh failure is reported separately |

Authorized-comment imports and creative experiments now use browser draft recovery.
Their text, selections and linked learning-decision reference survive tab changes
and reloads after an edit. Recovery does not submit either form. Draft controls
are explicit type=button, so restoring/discarding cannot accidentally trigger the
enclosing form's submission. A confirmed server receipt clears the local form;
a failed or unconfirmed request keeps its words. The Audience evidence refresh
button retries a read without resubmitting the preceding mutation.

Experiment templates are local form defaults, not remote saved experiments. The
form-template recovery option allows an owner to restore the full previously
edited form after reload, including the reviewed decision ID. Platform/account/
format/age stay locked for a linked form, and the backend still requires the
latest current decision, matching metadata and current evidence before recording
an experiment. A changed template does not silently replace owner edits.
Server-backed Studio/Countdown drafts retain their stricter comparison/conflict
rules; the template option is limited to new local form drafts.

Local words stay in this browser until saved or deliberately discarded. They can
contain the owner's pasted source text; import deidentification runs before the
server stores feedback. If browser storage fails while a form is dirty, both
internal Editorial tabs and main navigation ask before losing the on-screen words.
Cancel keeps the current form. Explicit discard removes the local draft without
sending an import or registration request.

Queue read failures now retain the last loaded snapshot instead of silently
presenting an empty or healthy queue. An initial failure says contents are
unconfirmed. The recovery view preserves edit state, presents retained titles as
read-only and offers a read retry; editing/publishing controls return after a
successful current read. Pausing auto-posting remains available. Request generations
also prevent a late response from an older pipeline from replacing a newer view.

The E2E fixture resets only test request buckets between independent browser
cases. Production rate limits and auth checks are unchanged. A dedicated case
seeds an exhausted fixture bucket, observes the real limiter's429 response and
Retry-After, waits the real window, and explicitly retries a read. This is a
seeded exhaustion/recovery test, not a600-request load benchmark. It also checks
that an unsaved queue edit survives the unavailable background read and that a
first-load outage cannot masquerade as zero items.

The new pure error tests run with `npm --prefix frontend run test:api-errors`.
Browser scenarios verify actual HTTP/UI imports, private registration, reload and
failed-storage recovery, linked experiment metadata, readable422 errors, missing
responses, confirmed-save/read-refresh separation and API-origin auth recovery.
Provider responses used by the local stack remain synthetic. These controls do
not complete persistence for every other form or establish real provider success.
