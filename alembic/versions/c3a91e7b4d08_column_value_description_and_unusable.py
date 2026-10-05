# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Column value_description and unusable flag

Column metadata now carries a value_description beside the column description.
The semantic compile reads both, and when either says the column should not be
used it sets ``unusable`` and does not create a ColumnAttribute. Usable columns
copy ``value_description`` onto the attribute so retrieval can embed it.

Revision ID: c3a91e7b4d08
Revises: 47ffb7499dfa
Create Date: 2026-10-05 22:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a91e7b4d08"
down_revision: Union[str, Sequence[str], None] = "47ffb7499dfa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "catalog_column", sa.Column("value_description", sa.Text(), nullable=True)
    )
    op.add_column(
        "catalog_column",
        sa.Column(
            "unusable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "column_attribute", sa.Column("value_description", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("column_attribute", "value_description")
    op.drop_column("catalog_column", "unusable")
    op.drop_column("catalog_column", "value_description")
