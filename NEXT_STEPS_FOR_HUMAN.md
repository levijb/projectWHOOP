# Manual steps before a real WHOOP pull

1. Create a WHOOP developer application at `developer.whoop.com` and register
   `http://localhost:3000/callback` as its redirect URI.
2. Copy `.env.example` to `.env` and enter the application client ID and client secret locally.
   Do not commit `.env`.
3. Run `python scripts/authenticate.py` once on your own machine, complete the browser login, and
   place the returned access/refresh tokens in the local `.env`.
4. Run `python scripts/download_whoop_data.py --days-back 180` to perform the first real pull.

## Notebook migration (retire the old client)

`src/whoop_client.py` and `src/whoop_oauth.py` are the pre-Phase-1 client and OAuth helpers.
They were kept rather than deleted during the Phase 1 merge because
`notebooks/WHOOP_EDA.ipynb` still imports them directly (`from src.whoop_client import
WhoopClient`, `from src.whoop_oauth import get_whoop_access_token`, in multiple cells).
`notebooks/WHOOP_Data_Explorer.ipynb` and `notebooks/WHOOP_Data_Explorer_DuckDB.ipynb` do not
reference either module, so they're already clear.

To finish retiring the old client:
1. Update `WHOOP_EDA.ipynb` to use `whoop_pipeline.client.WhoopClient` and
   `whoop_pipeline.oauth.get_whoop_access_token` instead (constructor/return shapes differ
   slightly — check `src/whoop_pipeline/client.py` and `src/whoop_pipeline/oauth.py`).
2. Once no notebook imports `src.whoop_client` or `src.whoop_oauth`, delete both files (and
   their `__pycache__` entries).

This is a deliberate follow-up task, not something done automatically as part of the Phase 1
merge — rewriting a notebook's cells isn't a mechanical change.

## Phase 2: enabling the scheduled pipeline

The scheduled workflow (`.github/workflows/scheduled-pipeline.yml`) builds the Docker image and
runs it daily, but **cannot succeed yet** — it references four repo secrets that don't exist.
To enable it:

1. In GitHub repo settings → Secrets and variables → Actions, add:
   - `WHOOP_CLIENT_ID`
   - `WHOOP_CLIENT_SECRET`
   - `WHOOP_ACCESS_TOKEN`
   - `WHOOP_REFRESH_TOKEN`
   (same values as your local `.env`, from the steps above.)
2. Note that the current pipeline uses `WHOOP_ACCESS_TOKEN` as-is; it does not yet refresh an
   expired access token from `WHOOP_REFRESH_TOKEN` before running. Access tokens are
   short-lived, so a truly unattended daily schedule will eventually need that refresh logic —
   that's token-lifecycle work for a future session, not solved here.
3. Once the secrets exist, either wait for the daily cron or trigger it manually from the
   Actions tab (`workflow_dispatch` is enabled).
4. Before trusting scheduled runs, verify the Dockerfile itself with a real `docker build` —
   it was written and its individual commands were verified outside a container, but Docker
   isn't installed on the machine this was built on, so the image itself has never actually
   been built.

**Postgres is intentionally not part of Phase 2.** It was originally slated for this phase as a
"serving layer," but nothing in the project needs to write predictions or serve a dashboard
yet. It's deferred to whichever phase first actually needs a queryable store for something
beyond local DuckDB — most likely Phase 3, alongside MLflow. This is a deliberate scope
decision, not an oversight.

