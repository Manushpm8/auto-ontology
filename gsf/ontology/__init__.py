# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GSF Ontology construction pipeline.

Subpackages:
    domain_prereading — Phase 0: iterative knowledge graph construction
"""

from gsf.ontology.domain_prereading import (
    DomainSummary,
    run_domain_prereading,
)

__all__ = [
    "DomainSummary",
    "run_domain_prereading",
]
