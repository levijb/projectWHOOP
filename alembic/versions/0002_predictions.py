"""Minimal serving records for next-cycle recovery forecasts.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    schema = "whoop" if op.get_bind().dialect.name == "postgresql" else None
    op.create_table(
        "predictions",
        sa.Column("cycle_id", sa.BigInteger(), primary_key=True),
        sa.Column("model_name", sa.String(), primary_key=True),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("target_cycle_id", sa.BigInteger(), nullable=True),
        sa.Column("origin_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("predicted_value", sa.Float(), nullable=False),
        sa.Column("ci_lower", sa.Float(), nullable=False),
        sa.Column("ci_upper", sa.Float(), nullable=False),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("error", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "predicted_value >= 0 AND predicted_value <= 100", name="prediction_score_range"
        ),
        sa.CheckConstraint(
            "ci_lower >= 0 AND ci_upper <= 100 AND ci_lower <= ci_upper",
            name="prediction_interval_range",
        ),
        sa.CheckConstraint(
            "actual_value IS NULL OR (actual_value >= 0 AND actual_value <= 100)",
            name="prediction_actual_range",
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = "whoop" if op.get_bind().dialect.name == "postgresql" else None
    op.drop_table("predictions", schema=schema)
