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

