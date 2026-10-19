# Live Shorts archive — 2026-10-19

Owner resumed the ten-channel archive and explicitly chose the working LongForm Shared Drive connection. The archive resumed at 20:45 UTC after a successful playback and real Scrapper download check; it is not yet complete.

Latest verified checkpoint, 21:00 UTC: **18 actual Shorts**, **71,024,790 video bytes**, stored and checksum-verified in Drive; **18 SocialPilot Review rows** assigned to `Cinama:*`, with no schedule. The live sync import/filter fixes are deployed at `148f8a9` (Railway `24232195-cf56-4310-853e-70846a849d88`). Two real repeat syncs each scanned 18 videos, skipped 18 and added zero duplicates. The ten JSON manifests are excluded; the ten temporary erroneous manifest Review rows were removed through the undo-backed app action without changing Drive files. All 109 focused isolated tests pass.

The detached Mac archive continuation started at 20:59:53 UTC, with sleep prevention. It processes the existing ledger sequentially and runs the requested Review-only Drive sync after each channel. It stops on renewed access challenges, quota/storage blockers or revoked ownership. Keep the Mac powered and online. This is a running operation, not completion of all ten channels. Local process and final-outcome evidence are in `continuation-state.json` / `continuation-log.txt` under the ignored operation artifacts folder.

Ledger at restart: discovered **4,007**, downloaded **18**, uploaded **18**, skipped **1**, failed **0**, remaining **3,988**. A direct normal-browser check also confirmed Tiny Friends TV currently has only Home/Videos tabs and 17 regular uploads; no available Shorts tab. It remains incomplete, and regular videos are not substituted.

The earlier blocked checkpoint and initial storage setup follow. Their zero-video figures are historical.

Destination: [Movie Clips](https://drive.google.com/drive/folders/1zUxET2b59XTILnDfwOF9-5lgjPPc9NLY) inside the existing LongForm Shared Drive. Ten source-channel folders and ten checksum-verified channel manifests are present.

A real original 6,150-byte synthetic MP4 passed remote size/MD5 verification in this destination. Only that disposable fixture was moved to recoverable Trash afterward, so it cannot become a queue item. The old personal Movie Clips folder was independently tested and returned HTTP403 storageQuotaExceeded; changing to the Shared Drive resolved storage access.

| Channel | Discovered Shorts | Inventory |
|---|---:|---|
| @CharanEbenez | 299 | Complete |
| @AfridiAE | 208 | Complete |
| @RockMovie777 | 130 | Complete |
| @Agi3D3 | 277 | Complete |
| @sir7076 | 259 | Complete |
| @FLICKSFYP | 1,121 | Complete |
| @theaiplot | 674 | Complete |
| @nfiqjfoiqhfqnfi131 | 0 | Incomplete: no available Shorts tab |
| @DoctorWhoShorts | 1,023 | Complete |
| @ArliStoryTime | 16 | Complete |

Earlier blocked video ledger: discovered **4,007**, downloaded **0**, uploaded **0**, skipped **1**, failed **0**, remaining **4,006**.

The failed count does not count sign-in challenges as failed video transfers: those items stay awaiting a usable worker, with their explicit errors on stopped channels. Seven channels were stopped during the batch; two were not attempted after the global challenge halt. Tiny Friends TV stays incomplete. No original video bytes reached the archive or SocialPilot.

Manifest storage: **2,230,576 bytes**; actual archived-video storage: **0 bytes**. Shared Drive pooled available capacity is not exposed by user storageQuota. Local Mac temporary space was about5.1GiB; transfers are sequential with an enforced reserve.

The Mac downloader and an ordinary Brave Personal watch page both show “Sign in to confirm you’re not a bot.” The owned batch was stopped, its subprocess killed and all leases released. No proxy rotation, cookie extraction, CAPTCHA solving or other challenge bypass was attempted. A normal YouTube sign-in tab is handed to the owner. Sign-in is pending; it must not be represented as a guaranteed remedy for worker access.

The existing SocialPilot Cinama profile was verified. Existing auto-posting for other queues remains enabled as previously configured by the owner; this operation has not changed it. Sync Drive has not run because there are no actual Shorts files yet. When verified videos exist, invoke:

```json
{
  "folder_id": "1zUxET2b59XTILnDfwOF9-5lgjPPc9NLY",
  "pipeline": "Movie Clips",
  "auto_approve": false
}
```

Use `POST /api/drive/sync`, inspect the resulting tracked job, confirm Cinama destinations and deduplication, and keep new items in Review. No Ready, schedule or public-publish change is authorized by this archive operation.

Resume only after normal authorized YouTube access is available. Recheck one item before resuming bulk work; if the challenge persists, stop and report it. Reuse existing canonical ledger entries and the new destination. For re-inventory, explicitly pass the new parent folder ID; the old endpoint/CLI default still points to the personal folder. Existing success records must never be re-downloaded/re-uploaded.

Evidence is retained in ignored `artifacts/shorts-archive-20261019/`: refreshed inventories, actual Drive error/success receipts, empty-ledger parent reconciliation, worker logs, folder/manifest verification and current live counts. No application source was changed or deployed for this operation; this checkpoint records live setup and the external blocker.
