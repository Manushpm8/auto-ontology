"""one term per attribute

Makes ``UNIQUE(attribute_id)`` on the two PROPERTY_OF link tables a database
rule instead of a convention the writers keep.

Both tables key on ``(attribute_id, term_id)``, which only forbids the same
pair twice -- one attribute could be a property of two Terms. Nothing wanted
that: ``link_to_term`` deletes any other link before inserting, a
ColumnAttribute's ``term_name`` is part of its merge key, and every read joins
through the link, so a second Term would show one attribute twice on pages
that mean to list it once.

Applies as-is on a store that has been written to only through the DAL. A
duplicate would fail here rather than be dropped, because picking which of two
Terms to keep is not a decision a migration can make.

Revision ID: 00a48e8b426f
Revises: 00e5822e88be
Create Date: 2026-09-03 15:57:08.113113

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00a48e8b426f"
down_revision: Union[str, Sequence[str], None] = "00e5822e88be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(
        op.f("uq_column_attribute__term_attribute_id"),
        "column_attribute__term",
        ["attribute_id"],
    )
    op.create_unique_constraint(
        op.f("uq_sql_attribute__term_attribute_id"),
        "sql_attribute__term",
        ["attribute_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("uq_sql_attribute__term_attribute_id"),
        "sql_attribute__term",
        type_="unique",
    )
    op.drop_constraint(
        op.f("uq_column_attribute__term_attribute_id"),
        "column_attribute__term",
        type_="unique",
    )
