# UK TV Listings Preview background guide

A free, personal accessibility project by Sidney Tambin, separate from Inclusophy Ltd. This project builds the shared schedule for the private Alexa Preview. The public skill is not connected to it yet.

The importer workflow downloads onTVnow's documented public feeds, validates complete exports, preserves source and channel identities, checks for coverage regressions, and publishes a compact guide to GitHub Pages. It retains the previous immutable guide alongside the new one. A failed build does not deploy or replace the live data.

The builder uses three to five bulk source requests plus targeted JSON requests for conflicting channel/day schedules. Requests are paced to at most40 per minute, including retries, regardless of listener count. A download can take minutes here without delaying an Alexa answer. It retrieves schedules only; no listener identifiers, spoken requests, preferences or Amazon credentials enter this repository or workflow.

Processing uses standard public-repository GitHub-hosted runners and public GitHub Pages. A separate personal Supabase Free project runs the timer. No paid runner, billing change or paid service is enabled. Deployment artifacts expire after one day. The live site retains two compressed guides. A daily record documents successful publication.

Listings source and published public-use basis: [onTVnow data access](https://www.ontvnow.co.uk/data-access). Listings retain source links. The data is not relicensed by this project. Missing listings do not establish off-air periods; only explicit source markers do. Full Freeview coverage and a full 168-hour schedule for every channel are not claimed. No unverified audio-description flags are inferred.

See [skill help and privacy](https://tv-listings-by-sidney.luckyledge3.chatgpt.site). The workflow publishes only after its tests and source validation pass. Roll back skill integration by removing `background_snapshot_url` from the Preview catalogue; the existing on-demand adapter remains available.

13 November 2026: GitHub's cron trigger is retired as unreliable after no automatic runs were observed over several hours. Push/manual/API dispatch runs remain available. The independent Supabase Cron job checks each minute, polls the published manifest every two minutes, and requests a refresh when the last collection is at least 30 minutes old. Processing and publication take additional time; this is not real-time broadcast monitoring. Dispatch attempts are bounded to one per 25 minutes and imports are serialised. The Preview checks for a newer manifest at most every five minutes while serving requests.

The scheduler uses a non-expiring fine-grained GitHub connection explicitly authorised by the owner. It has Actions read/write only for this repository, plus mandatory metadata read, and is stored encrypted in Supabase Vault. No token is stored in source files or logs. Private operations tables record timestamps and HTTP outcomes only, retain seven days, and cannot be read or triggered by anonymous or ordinary API users. See scheduler.sql for reproducible setup; it defaults to disabled until the connection is configured and the operator enables it. Disable refresh_state.enabled to pause dispatch without changing the skill or removing the last published guide.

Old/new overlaps are checked against the source's channel/day JSON revision timestamps. Only demonstrably older conflicting records are removed. Unknown or equal revisions remain flagged; source identity changes fail validation. Source data coverage remains subject to the accepted limitations above. Confirmation of autonomous refresh and runtime adoption is recorded in the private project's release evidence.
