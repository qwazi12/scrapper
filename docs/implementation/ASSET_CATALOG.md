# Authorized local asset catalog

The Editorial desk Asset library indexes local authorized video/audio alongside existing normalized stills. Inputs require owner-created/licensed attestation and a scene description.50MiB source,0.25–300s duration,60MiB normalized output,180MiB free preflight and bounded probe/transcode/decode timeouts constrain each import. Video is normalized to at most1280×720 at30fps with optional audio; audio becomes AAC. This is a working preview/reuse copy, not preservation of original-resolution masters. Source SHA and normalized SHA remain distinct. No channel download, provider call or publishing occurs here.

Each video has three sampled preview frames with timestamps, SHA-256 and difference hashes. Tags, descriptions and people/entity labels come from the owner. Optional owner-supplied caption intervals require ordered, finite, non-overlapping times within actual duration. These annotations are unverified; no face recognition, automatic transcription or semantic model has run. Permission attestation is retained with the asset.

Duplicate normalized bytes reuse the original row/provenance only after the media and previews pass their retained hashes. Corruption produces409 and does not silently replace bytes or claim success. Recover the exact retained asset before reuse. Upload/transcode temporary files are owned and cleaned on failure. Simultaneous identical imports converge through unique SHA plus no-replace links; a database failure may retain an unreferenced verified file for later managed recovery rather than deleting another process's copy.

Search streams database candidates and keeps a bounded result set. It ranks lexical tag/description/caption matches and returns matching caption spans for playback; it does not certify scene relevance or identity. Private metadata/captions need owner credentials. Signed media passes permit only binary media/preview routes. Those responses verify immutable hashes while holding the file claim through the last byte. Native beat cards accept indexed stills. Short/Reel-only campaigns can now use explicit0.25–12second video intervals with selected hold/loop treatment, muted source audio, retained silent-excerpt hashes and source/output file claims. See NATIVE_CLIPS.md for the exact limits and current verification. Explicit background-audio intervals, levels and silence/loop treatment are now implemented for Short/Reel-only campaigns; see NATIVE_AUDIO.md and its current verification. Beat-level semantic retrieval and deeper musical matching/transitions remain unfinished.

Evidence: focused6:11 passed; full backend7:613 passed/1optional skip; actual PostgreSQL7:147 passed; production build7 passed. Browser8:2 passed,6.0s, actual fixture import/decode/playback/three previews/caption search/duplicate reuse plus private manual native exports. Earlier browser4 failed at an ambiguous filter label and was corrected with explicit accessible select labels. The first corruption regression reused deliberately corrupted fixture bytes from another case; sources now have per-case procedural tones, and the full corrected suite passes. Evidence is saved under artifacts/e2e/history/asset-catalog8. None of this is real-account analytics, semantic retrieval or automatic rights verification.

Import metadata/caption words now recover privately by API origin; actual files must be chosen again after reload. Confirmed unique imports and failed catalog refresh are separate states. Duplicate reuse preserves old provenance and unrecorded changed metadata remains private; no revision is implied. Catalog errors retain labeled snapshots/unconfirmed initial contents, and older searches cannot replace newer results. Full23-browser regression passed; see OWNER_RECOVERY.md and the current evidence report.

## Reviewed metadata and use permission revisions

Owner-only `GET/POST /api/editorial/assets/{id}/metadata-review` retains the original
catalog bytes, tags and provenance. New immutable decisions can correct license
attestation, scene description, tags, people labels and bounded caption intervals,
or block future use. These are owner attestations; the application does not verify
licenses, scene identity or caption alignment. Each decision includes a reason and
explicit exact-field review. Still images cannot carry timed cues.

Decisions bind catalog ID, normalized hash, kind and original creation time. The
current version also binds original metadata and the latest annotation ID. Missing
legacy identity cannot be reviewed or admitted to a new native treatment. A changed
original catalog record blocks an old overlay pending fresh review; a reused ID
does not adopt the deleted asset's reviews. The latest effective view is queried in
batches of100 for catalog search; history bodies are fetched only in explicit owner
review (latest20, plus total count). Search/preview identity remains lexical/owner
supplied. Binary delivery continues to verify the original immutable media hash.

Saving uses an exclusive source file claim and the shared database writer mutex.
CAS refuses stale edits. The request's deterministic receipt includes the original
expected version and exact canonical decision, so an uncertain exact retry returns
the original receipt. If another decision superseded it, the response says so and
never reinstates its old permissions. SQLite and PostgreSQL append-only triggers
refuse raw SQL updates/deletes. Evidence survives catalog deletion.

The editor caches only private metadata words by API origin and asset identity.
Reloads compare the current record against cached words; stale save409 retains the
draft and resets acknowledgment. Owner comparison/rebase and a new exact-field
acknowledgment are required before saving against the newer source. A confirmed
receipt and catalog refresh are separate states. Accepted or unresolved external
submissions cannot be recalled by a later local metadata decision.

Native input snapshots now include stable catalog identity/effective provenance.
Changed annotations invalidate old admission and exact export approval; blocked use
prevents controlled rendering, approval, enqueue and dispatch. These paths hold the
source file claims through use. The dispatcher rechecks under its claim before
writing durable intent. Historical export manifests/content mappings remain valid
historical evidence; their bytes/lineage are not rewritten. Pre-revision exports
without catalog identity require a fresh render/review, not guessed identity
backfill. Original graphics without catalog assets are unaffected.

New client selections also send optional-compatible `asset_metadata_version` and
`audio_metadata_version`; old API callers may omit them and bind the current source
at creation, but revocation and exact retained-input checks still apply. The new
client requires explicit review of changed/legacy source metadata before saving a
revised treatment. “Draft revised treatment” copies all saved copy, formats, source
references and clip/audio timing/gain/end behavior privately. It never edits an old
campaign, renders, accepts or queues automatically. Replacing another unsaved draft
requires an explicit in-app choice.

Still imports now share the duplicate integrity contract: normal duplicate lookup
and the real unique-insert race fallback verify the winner's image kind, catalog
root and retained SHA under a source reader claim. Missing/corrupted media and an
independent exclusive mutation return409, preserve the old catalog/provenance and
never report reuse success. Tests cover verified/missing/corrupt concurrent winners.
Actual UI shows the refusal and succeeds only after exact byte restoration. Current
evidence: backend683passes/1skip,PG207passes/native restore; scoped browser2passes
in history/still-integrity1. Full24 earlier browser evidence remains separate.
