{#
    ML feature-engineering mart for the recovery-prediction phase (Phase 4). One row per
    cycle_id, built entirely from the already-validated daily_summary source -- no bronze/
    silver/gold logic is reimplemented here, only derived features on top of it.

    Feature set and the "low strain" threshold (8.0, on WHOOP's 0-21 strain scale) are
    documented judgment calls; see SESSION_2_SUMMARY.md for rationale.

    Written to compile against both the dev (DuckDB) and prod (Postgres) targets: day_of_week
    uses the day_of_week() macro (DuckDB and Postgres spell that function differently), and
    the rest-day lookback uses max() instead of last_value(... ignore nulls), since Postgres
    doesn't support IGNORE NULLS -- max() is equivalent here because low_strain_day_index is
    built from a monotonically increasing row_number(), so the largest non-null value seen so
    far in start_at order is also the most recent one. Tests compile prod offline and
    cross-check its SQL on fixture DuckDB; real Postgres still needs a manual smoke test.
#}

with daily as (
    select
        cycle_id,
        user_id,
        start_at,
        cycle_strain,
        recovery_score,
        hrv_rmssd_milli,
        resting_heart_rate,
        sleep_debt_need_hours,
        sleep_performance_percentage,
        total_in_bed_hours
    from {{ source('whoop_gold', 'daily_summary') }}
),

ordered as (
    select
        *,
        row_number() over (order by start_at) as day_index
    from daily
),

low_strain_marked as (
    select
        *,
        case when cycle_strain < 8.0 then day_index end as low_strain_day_index
    from ordered
)

select
    cycle_id,
    user_id,
    start_at,
    {{ day_of_week('start_at') }} as day_of_week,

    cycle_strain,
    recovery_score,
    hrv_rmssd_milli,
    sleep_debt_need_hours,

    -- Rolling 7-cycle averages (most recent 7 cycles, not strictly calendar days -- cycles
    -- are normally ~1/day, so this is a reasonable proxy without needing to fill date gaps).
    avg(recovery_score) over (
        order by start_at rows between 6 preceding and current row
    ) as recovery_score_7d_avg,
    avg(cycle_strain) over (
        order by start_at rows between 6 preceding and current row
    ) as cycle_strain_7d_avg,
    avg(hrv_rmssd_milli) over (
        order by start_at rows between 6 preceding and current row
    ) as hrv_rmssd_milli_7d_avg,

    -- Yesterday's strain, for same-day prediction using only information known so far.
    lag(cycle_strain, 1) over (order by start_at) as prior_day_strain,

    -- Rolling sleep-debt trend: 7-cycle average debt, and today's deviation from it (positive
    -- means debt is currently worse than the recent trend).
    avg(sleep_debt_need_hours) over (
        order by start_at rows between 6 preceding and current row
    ) as sleep_debt_7d_avg_hours,
    sleep_debt_need_hours - avg(sleep_debt_need_hours) over (
        order by start_at rows between 6 preceding and current row
    ) as sleep_debt_trend_hours,

    -- Cycles since the last day with cycle_strain < 8.0 ("rest day"); 0 on a rest day itself,
    -- null before the first rest day is observed.
    day_index - max(low_strain_day_index) over (
        order by start_at rows between unbounded preceding and current row
    ) as days_since_last_low_strain_day

from low_strain_marked
