"""Compras de puntos: estado de la saga de compra e idempotencia (ADR-001 §2.11)

Revision ID: c4d9e1f27a85
Revises: b7e2c4a91d30
Create Date: 2026-11-25 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9e1f27a85"
down_revision: str | Sequence[str] | None = "b7e2c4a91d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "point_purchases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("package_id", sa.String(length=32), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=True),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'refunded')", name=op.f("ck_point_purchases_status_valid")
        ),
        sa.CheckConstraint("points > 0", name=op.f("ck_point_purchases_points_positive")),
        sa.CheckConstraint("price_cents > 0", name=op.f("ck_point_purchases_price_positive")),
        sa.CheckConstraint(
            "status <> 'completed' OR (payment_reference IS NOT NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_point_purchases_completed_details"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR failure_reason IS NOT NULL", name=op.f("ck_point_purchases_failed_has_reason")
        ),
        sa.CheckConstraint(
            "status <> 'refunded' OR payment_reference IS NOT NULL",
            name=op.f("ck_point_purchases_refunded_has_payment"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_point_purchases")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name=op.f("uq_point_purchases_user_id_idempotency_key")
        ),
    )
    op.create_index(
        "ix_point_purchases_pending_updated_at",
        "point_purchases",
        ["updated_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("point_purchases")
