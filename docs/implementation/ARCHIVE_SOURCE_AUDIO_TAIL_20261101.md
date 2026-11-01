# DramaRama original audio-tail recovery

The archive stopped on `7692319483742014740`, episode 48 of *Reborn as My
Daughter’s Shield*. TikTok reports a 31-second duration. Its actual video track
runs 31.967 seconds; its original AAC packet timeline runs 37.708 seconds.
Independent probes of both the advertised HEVC and H.264 files show the same
longer audio. The downloaded fragmented MP4 retained all 959 video packets and
812 audio packets, with identical ordered packet SHA256 values to the selected
HEVC source. The full file decoded successfully. This was an original media
inconsistency rather than a truncated transfer.

The existing duration threshold remains in place. Identity-checked TikTok series
can now resolve the specific case of an audio track exceeding the declared
duration while the video remains within the existing tolerance. A second,
read-only probe of the selected original HTTPS media must independently prove:

- Exactly one video and one audio track, with matching codecs.
- Identical ordered SHA256 payload hashes and counts for every packet.
- Track durations, relative packet timestamps and audio/video offset preserved
  within 250 milliseconds to allow container timestamp rounding.
- Original video within the existing declared-duration tolerance, with the
  longer audio present in the original too.

Any missing/changed packet, short video, short audio, unexpected track, changed
timing, failed source probe or failed full decode still blocks the upload. The
file is preserved in full; no trimming, stretching or synthetic padding occurs.
Proof receipts contain source/output durations, packet counts and ordered hash
digests. They contain no signed media URLs or browser credentials.

Original probing has a 90-second deadline, 25-second network timeout and 16 MiB
metadata-output bound. Media stays in RAM/pipes. Normal verified-source ID and
Drive parent/size/MD5/SHA256 checks remain mandatory. Existing exact-content
deduplication and reserved Drive IDs are retained.

Validation: 76 focused tests passed across transfer/recovery, TikTok identity,
content deduplication, source packets and worker benchmarking. New tests use
generated signals and simulated source metadata to verify the exception as well
as rejection of missing, changed, reordered, retimed and desynchronized packets.
The real failed episode separately passed the new source packet proof and full
decode. Local evidence is under
`artifacts/archive-duration-recheck-20261101/`; these generated artifacts are not
committed to GitHub.

The owner requested continuation. DramaRama resumes with two workers and a
128 MiB media cap per worker, using the existing ledger, source/series folders
and canonical Drive IDs. The completed Tonight’s Conversation job stays
complete. No application deployment, publishing or queue mutation is part of
this fix. Downloaded and aliased episode totals must be reported separately.
