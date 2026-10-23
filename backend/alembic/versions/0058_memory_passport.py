"""memory passport: imports, and the import each fact, source and person came in with

Additive only: a new per-instance table (memory_import, under Row-Level Security) and a nullable
import_id on memory_item, document and contact, so erasing an import deletes exactly what it brought.

Revision ID: 0058
Revises: 0055
Create Date: 2026-10-23 01:10:41.360175

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0058"
down_revision: str | Sequence[str] | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("memory_import",)
CHILDREN = ("memory_item", "document", "contact")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "memory_import",
        sa.Column("owner_person_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("assistant", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("people_count", sa.Integer(), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memory_import_agent_id"), "memory_import", ["agent_id"], unique=False)
    op.create_index(op.f("ix_memory_import_owner_person_id"), "memory_import", ["owner_person_id"], unique=False)
    op.create_index(op.f("ix_memory_import_tenant_id"), "memory_import", ["tenant_id"], unique=False)
    for table in CHILDREN:
        op.add_column(table, sa.Column("import_id", sa.UUID(), nullable=True))
        op.create_index(op.f(f"ix_{table}_import_id"), table, ["import_id"], unique=False)
        op.create_foreign_key(
            f"{table}_import_id_fkey", table, "memory_import", ["import_id"], ["id"], ondelete="SET NULL"
        )
    from superaide.db.rls import enable

    enable(op.get_bind(), TABLES)


def downgrade() -> None:
    """Downgrade schema."""
    from superaide.db.rls import disable

    for table in CHILDREN:
        op.drop_constraint(f"{table}_import_id_fkey", table, type_="foreignkey")
        op.drop_index(op.f(f"ix_{table}_import_id"), table_name=table)
        op.drop_column(table, "import_id")
    disable(op.get_bind(), TABLES)
    op.drop_index(op.f("ix_memory_import_tenant_id"), table_name="memory_import")
    op.drop_index(op.f("ix_memory_import_owner_person_id"), table_name="memory_import")
    op.drop_index(op.f("ix_memory_import_agent_id"), table_name="memory_import")
    op.drop_table("memory_import")
