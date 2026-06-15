"""Health check."""

from fastapi import APIRouter

router = APIRouter(tags=["Infra"])


@router.get("/health")
async def health():
    llm_online = False
    try:
        from llm import llm_health_check

        llm_online = await llm_health_check()
    except Exception:
        pass
    return {
        "status": "ok",
        "service": "shopping-intel-crawler",
        "llm_online": llm_online,
    }
