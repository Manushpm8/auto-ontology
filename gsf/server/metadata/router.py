# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API route for turning free-text into PQL (or the pre-PQL data objects)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gsf.server.metadata import service as dal

router = APIRouter()


class DataForTextRequest(BaseModel):
    """Payload for ``POST /data-for-text``."""

    question: str = Field(..., min_length=1)
    # "pql" → return the PQL created for the text; "json" → return the data objects
    # gathered before the PQL is created (relevant tables, join paths, columns).
    output_type: Literal["json", "pql"] = "pql"


@router.post("/data-for-text")
def get_data_for_text(body: DataForTextRequest) -> dict:
    """Run the prediction flow for a question.

    Returns the PQL generated for the text (``output_type="pql"``), or the data
    objects created before PQL generation (``output_type="json"``). Returns 422
    when the flow cannot produce a result for the question.
    """
    try:
        result = dal.get_data_for_text(body.question, body.output_type)
    except dal.PredictionFlowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"data": result}
