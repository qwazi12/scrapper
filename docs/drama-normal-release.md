# Ordinary Drama release contract

`GET /api/drama/normal/contract` describes `drama-normal-v2`. Deployment installs
the controls and never grants rights, selects a series, creates a plan, changes
Ready state or submits a post. Legacy v1 plan application remains unavailable.
Existing inspection, review and Ready receipts are prerequisites. The authorized
coordinator records the user's existing permission and accurate agent QA; this
does not require an additional round of manual human wording approval or assert
that a human watched an artifact when only agent checks were performed.

## Permission and plan

All routes require owner authentication. Permission binds one canonical series,
season and inventory revision to an explicitly identified Facebook Page and X
account. Wildcards, display names, another series and future accounts grant no
authority. Permission updates/revocation and plan application hold publication
generation leases for the affected series/items. Requests carry operation IDs,
current fingerprints and exact evidence references; records are immutable.

| Route | Behavior |
| --- | --- |
| `GET /permissions/{series_key}` | Current permission reference and fingerprint |
| `POST /permissions` | Conditionally record existing permission and provenance |
| `POST /permissions/revoke` | Conditionally revoke an exact permission reference |
| `POST /plan-preview` | Read-only eligible slots and acknowledgment digest |
| `POST /plan` | Apply that exact preview with full source/queue/permission/native CAS |
| `GET /items/{id}/delivery` | Current derived delivery status and retained observation |
| `POST /items/{id}/verify-delivery` | Observe known requests and actual native content; archive only when both destinations pass |

Plans contain exactly two lane positions, with `null` representing a vacancy.
Only occupied lanes with their own permission can receive assignments. Slots are
10:00, 11:30, 13:00, 14:30, 16:00, 17:30, 19:00 and 20:30 America/New_York,
alternating A/B: at most four attempts per lane and eight total per local day.
One approved lane allows four, not eight. Canonical episode numbers determine
order. No gap skipping, quota borrowing, lane refill, catch-up or same-slot retry
occurs. Only future slots more than one minute away can be planned, and dispatch
has a five-minute window. Both DST transitions preserve local wall-clock times.

Audit creation time lives on the immutable receipt, outside the preview fact
digest. A short delay is harmless while eligible slots and all protected facts
are unchanged. Crossing the planning cutoff requires a new preview. Replacing a
plan cannot omit an unresolved assignment and leave its schedule misleadingly
active; already verified complete history can be omitted.

## Transport, completion and sequencing

Each final intent checks current generation ownership, exact native account/pins,
full protected queue/source-binding snapshot, permission, quota and slot. Both
the canonical original hash and the selected reviewed derivative hash/lineage
are bound through actual multipart transport. Definite partial failures retry
only the failed destination in a newly assigned future slot. Uncertainty holds.
Retained database protocol fences prevent an old publisher or unversioned permit
from submitting these identities after rollback.

Provider acceptance, success URLs, queue status and preview images never prove
delivery. The verifier uses only journaled requests, independently reads the
native author/post/copy/video, downloads bounded native media, decodes it and
compares timeline/video and each audio channel against the reviewed artifact.
Normal reencoding, resolution, frame rate and gain changes are tolerated; byte,
codec and waveform equality are not required. Truncation, frozen/missing video,
wrong/silent/missing audio or changed native identity/copy holds completion.
These are disclosed technical coverage checks, not semantic or rights proof.

Verification journals its fresh terminal provider results atomically with the
observation before archiving. It therefore works before or after background
polling. A verified immutable publication is durable history with no daily
expiry and no automatic native rereads. The exact original/derivative, Ready,
copy, request, attempt and native IDs remain bound. Current permission and target
identity must still match. A later held native observation, contradictory or
uncertain provider result, identity revision, rollback or late receipt closes
sequencing until reconciled. Missing proof never becomes success with age.

## Native-read dependency

Official Facebook Graph Video and X Post/media-expansion readers are implemented
but disabled and unconfigured by default. Posting credentials are not native-read
authority. The contract reports configuration booleans without credentials.
Deployment creates no credentials, expands no integration and spends no paid
native API credits. JSON reads are streamed to a 1 MiB bound; media reads are
limited to 256 MiB and 142 seconds. Signed CDN URLs are not retained or forwarded
credentials. Unsupported redirects and ambiguous post identity hold the item.

The existing provider media list is insufficient when Facebook's separate native
title/author is absent or X supplies a preview rather than playable media.
An unconfigured native reader holds that item's delivery proof and successors;
it does not globally disable a properly reviewed, permitted first episode.
No new public calibration or paid native call is implied by this implementation.
