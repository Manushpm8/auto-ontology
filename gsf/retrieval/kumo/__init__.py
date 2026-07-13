# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""KumoRFM-backed prediction for the text-to-SQL agent."""

from gsf.retrieval.kumo.predictor import predict_from_question

__all__ = ["predict_from_question"]
