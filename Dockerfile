# One-shot pipeline runner: builds the asset graph, runs it once, and exits.
# Not a persistent Dagster daemon/webserver -- an always-on host is out of scope for a
# personal project. Meant to be triggered externally (see .github/workflows/scheduled-pipeline.yml).

FROM python:3.11-slim

WORKDIR /app

# Install the package first (better layer caching): only pyproject.toml + src/ are needed for
# `pip install .`, so dependency layers don't get invalidated by dbt/test-fixture changes.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# dbt/ isn't packaged into the wheel ([tool.hatch.build.targets.wheel] only ships
# src/whoop_pipeline), so it's copied separately and pointed to explicitly.
COPY dbt ./dbt
ENV WHOOP_DBT_PROJECT_DIR=/app/dbt

# Pre-generate the dbt manifest at build time. dagster_dbt.DbtProject.prepare_if_dev() only
# regenerates the manifest under `dagster dev`; this one-shot container never runs that, so the
# manifest must already exist on disk before `dagster job execute` imports the asset module.
RUN dbt parse --project-dir dbt --profiles-dir dbt

# Copied so this image can be verified locally with zero WHOOP credentials (the whoop resource
# defaults to fixtures unless WHOOP_PIPELINE_USE_LIVE_CLIENT is explicitly set -- see
# src/whoop_pipeline/orchestration/definitions.py). Real scheduled runs set that flag plus the
# three WHOOP_* secrets and never touch these.
COPY tests/fixtures ./tests/fixtures

ENV DAGSTER_HOME=/app/.dagster_home
RUN mkdir -p "$DAGSTER_HOME"

ENTRYPOINT ["dagster", "job", "execute", "-j", "whoop_pipeline_job", "-m", "whoop_pipeline.orchestration.definitions"]
