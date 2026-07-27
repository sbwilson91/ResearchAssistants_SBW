# Defunct — Strava-based version

This is a frozen snapshot of `running_bot/` from before the Garmin migration
(2026-07-27), preserved for reference since it relied on the Strava API.

Strava access was lost when the Strava subscription lapsed (no renewed
API/refresh token access). This copy is **not run by any CI workflow** and
should not be executed — `STRAVA_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN` are no
longer valid.

The live bot is `running_bot/`, migrated to pull the same data from Garmin
Connect instead of Strava.
