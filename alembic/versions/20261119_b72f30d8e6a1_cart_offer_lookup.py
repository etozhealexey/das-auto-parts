"""Index cached offer lookup without blocking price imports or cart requests."""

import sqlalchemy as sa

from alembic import op

revision = "b72f30d8e6a1"
down_revision = "a19b02c3d4e5"
branch_labels = None
depends_on = None

INDEX = "ix_price_offers_cart_lookup"


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    # Concurrent builds must run outside a transaction. Recover an interrupted
    # build too: IF NOT EXISTS alone would silently keep an invalid index.
    with op.get_context().autocommit_block():
        valid = op.get_bind().execute(sa.text(
            "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
        ), {"name": INDEX}).scalar()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY {INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} "
            "ON price_offers (provider, md5(offer_key), fetched_at DESC, id DESC)"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
