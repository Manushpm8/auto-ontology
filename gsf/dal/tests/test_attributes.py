# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for attribute graph traversal."""

from unittest.mock import MagicMock, patch

from gsf.dal.attributes import find_join_path


@patch("gsf.dal.attributes.get_neo4j_conn")
def test_join_path_traverses_semantic_fk_only_outgoing(
    get_connection: MagicMock,
) -> None:
    connection = get_connection.return_value
    connection.query_read.return_value = []

    assert find_join_path("anchor", "destination") == []

    query = connection.query_read.call_args.args[0]
    assert "relationshipFilter: 'SEMANTIC_FK>|HAS_ATTRIBUTE|CONTAINS'" in query
