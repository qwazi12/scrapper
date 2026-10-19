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

Native campaign treatment drafts now preserve all format copy, selected formats,
beat/source/asset selections, clip ranges and intentional end treatments. Their
browser key includes the API origin. Restore/discard is explicit and never creates
a campaign, render or queue item. Malformed recovered structures remain available
as private text, while rendering/submission of that structure is blocked. They
are not silently discarded or coerced into a different treatment.

The form retains the selected approved research fingerprint. A recovered selection
that differs from the current approved brief cannot save until the owner inspects
the current source context and explicitly selects it. This keeps treatment words
while requiring current research review. The new client always submits that
fingerprint; a change after the displayed review returns409 at create. This API
field is additive for older clients, which may omit it but still require current
approved research and downstream input rechecks. It is not an independent semantic
verification of the brief or every legacy caller's selection.

The campaign form disables edits while its mounted save is pending. A confirmed
receipt clears that mounted draft, and a failed view refresh remains a distinct
read error. The read-only Refresh native campaigns button does not retry the
mutation. Failed/unconfirmed requests retain the full draft. If the editor is
left while a request is in flight, its late response cannot clear a newer editor's
browser words; the retained draft stays available for explicit review against the
current campaign list. No automatic request retry or deduplication of deliberately
created private campaigns is implied. The actual scenario checks two distinct
manual saves, not an automatic duplicate.

Still-import file selection and other remaining forms are outside this native
treatment recovery slice. Browser recovery is local/private storage, not a server
backup or media-file archive. The current ledger records actual backend/PG/UI
results, delayed-response and failed-storage cases before this slice is counted.

The Asset library import template now recovers its API-origin-scoped permission,
description, tags, entity labels and caption intervals. Only metadata and a last
selected filename/size hint are stored. File bytes are never placed in browser
draft storage; after leaving/reloading, the owner must choose the local file again.
Restore/discard cannot import. Malformed recovered caption structures stay private
and readable while import is blocked. Mounted imports disable edits; late receipts
from an unmounted editor cannot clear a newer editor's metadata.

A confirmed new-asset receipt clears that submitted metadata draft without clearing
the current file selection or template. Catalog-refresh failure is reported
separately from the confirmed import. Duplicate reuse preserves the server's
existing permissions/annotations; it does not clear newer unsaved form metadata
or claim that those words replaced the asset record. No automatic import retry or
provenance revision is implied.

Catalog read failures retain the last loaded results, label them potentially out
of date, and offer a read-only refresh. Initial failure leaves contents unconfirmed
rather than presenting zero as a healthy result. Request generations prevent older
search responses from replacing a newer query's rows/counts/error state. Query
inputs remain stable during import; the post-import read uses their current
submitted context instead of silently resetting them.
