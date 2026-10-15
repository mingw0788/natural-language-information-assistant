from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api")


@router.get("/history")
def history(
    request: Request,
    query: str = Query(default="", max_length=300),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    page = request.app.state.repository.list_history(query, limit, offset)
    return page.model_dump(mode="json")
