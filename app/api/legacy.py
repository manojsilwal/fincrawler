"""Legacy finance/cards endpoints — removed in hybrid refactor."""

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Legacy"])


@router.get("/quote")
@router.get("/quote/smart")
@router.post("/extract")
@router.post("/cards/recommend")
@router.post("/cards/points-usage")
@router.delete("/cache")
async def legacy_removed():
    raise HTTPException(
        410,
        detail="Finance/cards endpoints removed in hybrid shopping-intel refactor. Use /shop/search.",
    )
