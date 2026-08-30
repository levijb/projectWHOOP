# WHOOP v2 data model and Phase 1 conventions

This document records the verified WHOOP Developer API v2 fields used by projectWHOOP. All four
collection endpoints return `{"records": [...], "next_token": "..."}`. `next_token` is absent
on the final page.

Every activity record has `score_state` equal to `SCORED`, `PENDING_SCORE`, or `UNSCORABLE`.
The nested `score` object is available only for `SCORED`; silver score fields are nullable so
the other states remain valid data rather than ingestion failures.

## Cycle (`GET /v2/cycle`)

| API field | Silver field | Notes |
|---|---|---|
| `id` | `cycle_id` | Stable integer key |
| `user_id` | `user_id` | Integer |
| `created_at`, `updated_at` | same | UTC timestamps |
| `start`, `end` | `start_at`, `end_at` | `end` is nullable for an ongoing cycle |
| `timezone_offset` | same | Offset reported by WHOOP |
| `score_state` | same | Three-state enum |
| `score.strain` | `strain` | Non-negative, nullable |
| `score.kilojoule` | `kilojoule` | Non-negative, nullable |
| `score.average_heart_rate` | `average_heart_rate` | Nullable |
| `score.max_heart_rate` | `max_heart_rate` | Nullable |

## Recovery (`GET /v2/recovery`)

| API field | Silver field | Notes |
|---|---|---|
| `cycle_id` | same | Stable key for one recovery per cycle |
| `sleep_id`, `user_id` | same | Join/reference fields |
| `created_at`, `updated_at` | same | UTC timestamps |
| `score_state` | same | Three-state enum |
| `score.user_calibrating` | `user_calibrating` | Nullable boolean |
| `score.recovery_score` | `recovery_score` | Nullable, 0–100 |
| `score.resting_heart_rate` | `resting_heart_rate` | Nullable, non-negative |
| `score.hrv_rmssd_milli` | `hrv_rmssd_milli` | Already milliseconds; no conversion |
| `score.spo2_percentage` | `spo2_percentage` | WHOOP 4.0+, optional, 0–100 |
| `score.skin_temp_celsius` | `skin_temp_celsius` | WHOOP 4.0+, optional |

Recovery can also be accessed under a cycle-by-ID response. The standalone recovery collection
is retained as the primary path because it has worked in prior project pulls.

## Sleep (`GET /v2/activity/sleep`)

Identity and timing fields are `id`, `cycle_id`, `user_id`, `created_at`, `updated_at`, `start`,
`end`, `timezone_offset`, `nap`, and `score_state`. Silver renames `id` to `sleep_id`, `start` and
`end` to `start_at` and `end_at`, and `nap` to `is_nap`.

All duration fields below are milliseconds at the API boundary and decimal hours in silver:

| API score field | Silver field |
|---|---|
| `stage_summary.total_in_bed_time_milli` | `total_in_bed_hours` |
| `stage_summary.total_awake_time_milli` | `total_awake_hours` |
| `stage_summary.total_no_data_time_milli` | `total_no_data_hours` |
| `stage_summary.total_light_sleep_time_milli` | `total_light_sleep_hours` |
| `stage_summary.total_slow_wave_sleep_time_milli` | `total_slow_wave_sleep_hours` |
| `stage_summary.total_rem_sleep_time_milli` | `total_rem_sleep_hours` |
| `sleep_needed.baseline_milli` | `baseline_sleep_need_hours` |
| `sleep_needed.need_from_sleep_debt_milli` | `sleep_debt_need_hours` |
| `sleep_needed.need_from_recent_strain_milli` | `recent_strain_need_hours` |
| `sleep_needed.need_from_recent_nap_milli` | `recent_nap_need_hours` |

The stage summary also supplies `sleep_cycle_count` and `disturbance_count`. Other direct score
fields are `respiratory_rate`, `sleep_performance_percentage`,
`sleep_consistency_percentage`, and `sleep_efficiency_percentage`.

Nap rows remain in the `sleep` table for lossless analysis. `join_daily` and DuckDB's
`daily_summary` exclude `is_nap = true` and select the latest-updated main sleep per cycle. This
prevents a nap from overwriting nightly sleep metrics.

## Workout (`GET /v2/activity/workout`)

Identity and timing fields are `id`, `user_id`, `created_at`, `updated_at`, `start`, `end`,
`timezone_offset`, `sport_name`, and `score_state`. Silver renames `id` to `workout_id` and the
two interval fields to `start_at`/`end_at`.

Score fields are `strain`, `average_heart_rate`, `max_heart_rate`, `kilojoule`,
`percent_recorded`, `distance_meter`, `altitude_gain_meter`, and `altitude_change_meter`.
`zone_durations.zone_zero_milli` through `zone_five_milli` become `zone_0_hours` through
`zone_5_hours`.

Current v2 records identify sport only through `sport_name`; deprecated `sport_id` and `v1_id`
are neither parsed nor stored. The verified workout schema also has no `cycle_id`. Daily views
therefore assign a workout to the cycle interval containing its `start_at`; unmatched workouts
remain safely available in the standalone table but do not enter a daily aggregate.

## Storage and update semantics

- Bronze partitions are `data/bronze/{cycles,recovery,sleep,workouts}/YYYY-MM-DD.jsonl`.
  Repeating a pull on the same UTC date atomically replaces that date/type partition.
- `data/_state/sync_state.json` is updated only after all four collections are written. The next
  pull starts at the previous `last_synced_date` (inclusive) so pending scores can mature.
- DuckDB tables use delete-then-insert upserts within one transaction: `cycle_id` keys cycles
  and recovery, `sleep_id` keys sleep, and `workout_id` keys workouts.
- Pandera validates types, required timestamps/columns, score bounds, and non-negative metrics
  before DuckDB is opened.

