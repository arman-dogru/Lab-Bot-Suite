# Jetson Bot acceptance record

Verified on October 30–31, 2026. Latest functional verification: October 31, 13:34 America/Toronto. Final backup verification is recorded in PROGRESS.md.

| Capability | Evidence |
| --- | --- |
| Device compatibility | Official pinned ARM64 Hermes image runs Python 3.14.9 on the preserved Ubuntu 18.04 / NVIDIA Linux 4.9 host. |
| Code and tool installation | Discord request installed pyfiglet, created and executed a program, verified the sum of squares as 385 and returned an artifact. |
| Documents and attachments | Uploaded Word document read; CAD 23 result returned as CSV/PDF through Discord. PDF, Word, spreadsheet and slide creation/readback also passed locally on device; document probe peak was 99.9 MiB. |
| Persistent skill and memory | Created a reusable skill and deployment memory; after service recreation, a new Discord request recalled the marker and reused the skill/program. |
| Standing goals | Actual multi-stage goals completed through native goal cycles, with checked output and native completion verdicts. Routine continuation messages stayed out of chat. |
| Practical website | Bot built and deployed Project Hub. Task board edits/moves, priorities/dates, notes/revisions, archive/reopen and search verified in the browser. |
| Website files and webhook | Real upload/download bytes matched SHA256 83a03f4e440d5d1ed2330eeae446e9b72a7964ad70b535b37dc91e55a2e53039. JSON/Markdown exports worked. Valid authenticated webhook created a task; invalid token and foreign-origin requests were rejected. |
| Private website deployment | Independent service listens on 127.0.0.1:8787 only, wrong Host rejected; pinned image, constrained app user, no bot/provider secrets mounted. Data and upload survived service restart. |
| Scheduling and boot | Native saved one-off job addf30871017 ran after a real headless reboot and produced SCHEDULE-AFTER-REBOOT-PASS. Wi-Fi and verified clock recovered without a desktop login. |
| Shared work capacity | Real native script-only scheduled job68450cdd5547 waited for a held chat slot, then ran once and produced SCHEDULE-SERIAL-PASS. Local status remained responsive without inference. |
| Stop | Live /stop killed the active fixture and prevented its delayed output. Queued automatic recovery cancellation also passed. |
| Budget | 10 accounting tests cover reservations, concurrency, retries, missing usage, persistence and rollover. Actual Toronto midnight reset, actual xAI settlement and routed helper/compaction calls observed. |
| Budget recovery | Ordinary request and a native scheduled job resumed automatically after budget pause; explicit pauses and cancellation were preserved. |
| Interrupted goal recovery | Worker checkpoint saved, process interrupted by service stop, result absent. Without a new user prompt, restarted gateway resumed the original goal, reused checkpoint_count=1 in a different process and produced RESTART-RESUME-PASS with exit 0. Native goal done at 13:24. |
| Owner direct messages | Actual owner DM `/jetson status` replied ready at 13:34; billed amount unchanged and no model call used. |
| Owner control | Actual Discord adapter admission checked using simulated unauthorized DM/server events; denied before tool/model execution. This did not use a second live Discord account. |
| Quiet activity | Separate private chat, status and activity channels. One edited activity card with counts; prompts, arguments, tool output and private reasoning are not forwarded. Status controls require no inference. |
| Backups and rollback | Isolated configuration/credential/ledger/site restoration passed; final refreshed archive recorded in PROGRESS.md. Replacement and rollback used a distinct image based on the same tested release. |
| Support suite | All 65 checks passed on Mac Python 3.12 and the Jetson container Python 3.14 after the final recovery correction. |

## Practical limits

- Local model inference has not been enabled or benchmarked. Grok is configured through the private budget gateway; provider/model changes need new compatibility and pricing checks.
- The 4 GB Nano runs in 5 W mode with one substantial chat/scheduled job at a time. Large document workloads still depend on available memory and compatible tools.
- The Nano currently depends on Mac USB power. Reliable independent operation requires the correct dedicated 5 V supply and a saved Wi-Fi network.
- The Mac automation extension’s file-URL permission prevented automated browser file selection. The app’s real upload/download HTTP flow passed; normal user file selection is available.
- Unknown execution outcomes are not blindly replayed. Explicit stops/pauses, current owner authorization, native freshness and restart-loop limits remain authoritative. Repeated recovery failures pause the affected job.
- Native context-reset goal transfer passed isolated API/regression checks. Its full live transfer path has not been separately acceptance-tested; live large-project compaction and service-restart recovery were tested.
- The $20 daily allowance is an operational safeguard. The agent has administrator access and is instructed to preserve it; it is not a boundary against deliberate administrator changes.
- Future core versions require their own migration tests. The rollback exercise verified the procedure within the same release. Application backups are not a full NVIDIA operating-system image.
