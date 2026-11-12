# UK TV Listings Preview background guide

A free, personal accessibility project by Sidney Tambin, separate from Inclusophy Ltd. This project builds the shared schedule for the private Alexa Preview. The public skill is not connected to it yet.

The scheduled workflow downloads onTVnow's documented public feeds about every 30 minutes, validates complete exports, preserves source and channel identities, checks for coverage regressions, and publishes a compact guide to GitHub Pages. It retains the previous immutable guide alongside the new one. A failed build does not deploy or replace the live data. GitHub schedules can be delayed; the skill has bounded saved-data fallback.

The builder normally uses three to five source requests per refresh, regardless of listener count. A download can take minutes here without delaying an Alexa answer. It retrieves schedules only; no listener identifiers, spoken requests, preferences or Amazon credentials enter this repository or workflow.

Only standard public-repository GitHub-hosted runners and public GitHub Pages are used. No paid runner, billing change, external credential or paid service is required. Deployment artifacts expire after one day. The live site retains two compressed guides. A daily success record also provides repository activity so GitHub's 60-day inactivity rule does not disable a healthy schedule.

Listings source and published public-use basis: [onTVnow data access](https://www.ontvnow.co.uk/data-access). Listings retain source links. The data is not relicensed by this project. Missing listings do not establish off-air periods; only explicit source markers do. Full Freeview coverage and a full 168-hour schedule for every channel are not claimed. No unverified audio-description flags are inferred.

See [skill help and privacy](https://tv-listings-by-sidney.luckyledge3.chatgpt.site). The workflow publishes only after its tests and source validation pass. Roll back skill integration by removing `background_snapshot_url` from the Preview catalogue; the existing on-demand adapter remains available.
