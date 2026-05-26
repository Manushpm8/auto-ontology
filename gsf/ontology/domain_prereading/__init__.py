# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 0 — Domain Pre-Reading: map-reduce summarization of the corpus."""

from gsf.ontology.domain_prereading.pipeline import run_domain_prereading
from gsf.ontology.domain_prereading.models import ChunkSignals, DomainSummary

__all__ = [
    "ChunkSignals",
    "DomainSummary",
    "run_domain_prereading",
]
