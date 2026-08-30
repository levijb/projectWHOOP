"""Production-quality local pipeline for personal WHOOP data."""

from whoop_pipeline.client import WhoopClient
from whoop_pipeline.config import WhoopConfig

__all__ = ["WhoopClient", "WhoopConfig"]
