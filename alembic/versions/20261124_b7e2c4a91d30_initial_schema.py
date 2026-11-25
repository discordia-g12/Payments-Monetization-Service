"""Esquema base: billetera de puntos, boosts de servidor, Premium, idempotencia y outbox

Revision ID: b7e2c4a91d30
Revises:
Create Date: 2026-11-24 00:00:00.000000+00:00

Escrita a mano (no autogenerada) para dejar los CHECK y los índices parciales, que autogenerate no detecta.
Los nombres son los de la convención de app/db/base.py: `alembic check` compara esta migración con los
modelos (tests/test_migrations.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e2c4a91d30"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(name: str, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default else None,
    )


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("balance", sa.BigInteger(), server_default="0", nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("balance >= 0", name=op.f("ck_wallets_balance_non_negative")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_wallets")),
    )

    op.create_table(
        "server_boosts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("points_spent", sa.Integer(), nullable=False),
        _timestamp("created_at"),
        _timestamp("removed_at", nullable=True, default=False),
        sa.CheckConstraint("points_spent > 0", name=op.f("ck_server_boosts_points_spent_positive")),
        sa.CheckConstraint(
            "removed_at IS NULL OR removed_at >= created_at", name=op.f("ck_server_boosts_removed_after_created")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["wallets.user_id"], name=op.f("fk_server_boosts_user_id_wallets"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_boosts")),
    )
    op.create_index(
        "ix_server_boosts_active_server_id",
        "server_boosts",
        ["server_id"],
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_index("ix_server_boosts_user_id_server_id", "server_boosts", ["user_id", "server_id"])

    op.create_table(
        "server_boost_counters",
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("active_boosts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("level", sa.SmallInteger(), server_default="0", nullable=False),
        _timestamp("updated_at"),
        sa.CheckConstraint("active_boosts >= 0", name=op.f("ck_server_boost_counters_active_boosts_non_negative")),
        sa.CheckConstraint("level BETWEEN 0 AND 3", name=op.f("ck_server_boost_counters_level_valid")),
        sa.PrimaryKeyConstraint("server_id", name=op.f("pk_server_boost_counters")),
    )

    op.create_table(
        "point_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("package_id", sa.String(length=32), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("payment_reference", sa.String(length=128), nullable=True),
        sa.Column("server_boost_id", sa.Uuid(), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint(
            "kind IN ('purchase', 'boost', 'refund', 'adjustment')", name=op.f("ck_point_transactions_kind_valid")
        ),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_point_transactions_amount_non_zero")),
        sa.CheckConstraint("balance_after >= 0", name=op.f("ck_point_transactions_balance_after_non_negative")),
        sa.CheckConstraint(
            "kind <> 'purchase' OR (amount > 0 AND package_id IS NOT NULL AND price_cents IS NOT NULL "
            "AND currency IS NOT NULL AND payment_reference IS NOT NULL)",
            name=op.f("ck_point_transactions_purchase_details"),
        ),
        sa.CheckConstraint(
            "kind <> 'boost' OR (amount < 0 AND server_boost_id IS NOT NULL)",
            name=op.f("ck_point_transactions_boost_details"),
        ),
        sa.CheckConstraint("price_cents IS NULL OR price_cents > 0", name=op.f("ck_point_transactions_price_positive")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["wallets.user_id"], name=op.f("fk_point_transactions_user_id_wallets"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["server_boost_id"],
            ["server_boosts.id"],
            name=op.f("fk_point_transactions_server_boost_id_server_boosts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_point_transactions")),
        sa.UniqueConstraint("payment_reference", name=op.f("uq_point_transactions_payment_reference")),
    )
    op.create_index("ix_point_transactions_user_id_created_at", "point_transactions", ["user_id", "created_at"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default="false", nullable=False),
        _timestamp("canceled_at", nullable=True, default=False),
        sa.Column("last_payment_reference", sa.String(length=128), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("plan IN ('monthly', 'yearly')", name=op.f("ck_subscriptions_plan_valid")),
        sa.CheckConstraint(
            "status IN ('active', 'past_due', 'canceled', 'expired')", name=op.f("ck_subscriptions_status_valid")
        ),
        sa.CheckConstraint(
            "current_period_end > current_period_start", name=op.f("ck_subscriptions_period_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
    )
    # Una sola suscripción viva por usuario
    op.create_index(
        "uq_subscriptions_live_user_id",
        "subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'past_due')"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index(
        "ix_subscriptions_status_current_period_end", "subscriptions", ["status", "current_period_end"]
    )

    op.create_table(
        "idempotency_records",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _timestamp("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('processing', 'completed')", name=op.f("ck_idempotency_records_status_valid")
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR response_status IS NOT NULL",
            name=op.f("ck_idempotency_records_completed_has_response"),
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_idempotency_records")),
    )
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _timestamp("created_at"),
        _timestamp("processed_at", nullable=True, default=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_events_pending_created_at",
        "outbox_events",
        ["created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("idempotency_records")
    op.drop_table("subscriptions")
    # point_transactions apunta a server_boosts y a wallets: va antes
    op.drop_table("point_transactions")
    op.drop_table("server_boost_counters")
    op.drop_table("server_boosts")
    op.drop_table("wallets")
