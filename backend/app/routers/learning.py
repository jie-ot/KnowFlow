"""Explicit study actions originating from a repeatedly forgotten card."""

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..db import get_db
from ..services.learning import append_understanding, card_learning, create_learning_task

router = APIRouter(prefix="/api/learning/cards", tags=["薄弱知识点"])


class UnderstandingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=100)
    base_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=200_000)


class LearningTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=100)
    plan_id: str | None = None
    plan_goal: str | None = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["learn", "review", "practice", "read"] = "learn"
    due_date: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)


@router.get("/{card_id}")
def get_learning(card_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return card_learning(conn, card_id)


@router.post("/{card_id}/understanding")
def add_understanding(card_id: str, body: UnderstandingIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return append_understanding(conn, card_id, body.operation_id, body.base_version, body.text)


@router.post("/{card_id}/tasks", status_code=201)
def add_task(card_id: str, body: LearningTaskIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return create_learning_task(conn, card_id, body.operation_id, body.model_dump(exclude={"operation_id"}))
