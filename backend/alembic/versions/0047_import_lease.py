"""import_lease: time-limited leases for cards imported from third-party exports

One row per lease, per instance, under the Row-Level Security policy (ADR 0009). Imported cards are
ordinary memory_item rows carrying lease_id; ending a lease deletes them. Nothing writes either
unless IMPORT_LEASE_ENABLED is on.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-23 04:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "import_lease",
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=30)), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("card_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_import_lease_agent_id"), "import_lease", ["agent_id"], unique=False)
    op.create_index(op.f("ix_import_lease_owner_person_id"), "import_lease", ["owner_person_id"], unique=False)
    op.create_index(op.f("ix_import_lease_tenant_id"), "import_lease", ["tenant_id"], unique=False)
    op.add_column("memory_item", sa.Column("lease_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_memory_item_lease_id"), "memory_item", ["lease_id"], unique=False)
    op.create_foreign_key("fk_memory_item_lease", "memory_item", "import_lease", ["lease_id"], ["id"])
    from superaide.db.rls import enable

    enable(op.get_bind(), ["import_lease"])


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    disable(op.get_bind(), ["import_lease"])
    op.drop_constraint("fk_memory_item_lease", "memory_item", type_="foreignkey")
    op.drop_index(op.f("ix_memory_item_lease_id"), table_name="memory_item")
    op.drop_column("memory_item", "lease_id")
    op.drop_index(op.f("ix_import_lease_tenant_id"), table_name="import_lease")
    op.drop_index(op.f("ix_import_lease_owner_person_id"), table_name="import_lease")
    op.drop_index(op.f("ix_import_lease_agent_id"), table_name="import_lease")
    op.drop_table("import_lease")
