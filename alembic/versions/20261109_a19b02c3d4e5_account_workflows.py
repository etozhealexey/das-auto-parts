"""Manager role, estimate lines and payment ledger."""

import sqlalchemy as sa

from alembic import op

revision = "a19b02c3d4e5"
down_revision = "0e9447334937"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customers",
        sa.Column("is_manager", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "estimate_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "estimate_id",
            sa.Integer(),
            sa.ForeignKey("estimates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("code", sa.String(100)),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_estimate_lines_estimate_id", "estimate_lines", ["estimate_id"])
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("manager_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reference", sa.String(150), nullable=False),
        sa.Column("comment", sa.String(500)),
        sa.Column("reversal_of", sa.Integer(), sa.ForeignKey("payments.id"), unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("customer_id", "reference", name="uq_payment_reference"),
    )
    op.create_index("ix_payments_customer_id", "payments", ["customer_id"])


def downgrade():
    op.drop_table("payments")
    op.drop_table("estimate_lines")
    op.drop_column("customers", "is_manager")
