"""CAPTCHA solving — CapSolver and 2Captcha integrations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class CaptchaSolver(ABC):
    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    async def solve_recaptcha_v2(self, *, site_key: str, page_url: str) -> str | None:
        ...


class CapSolverClient(CaptchaSolver):
    name = "capsolver"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def is_configured(self) -> bool:
        return bool(self._key)

    async def solve_recaptcha_v2(self, *, site_key: str, page_url: str) -> str | None:
        payload = {
            "clientKey": self._key,
            "task": {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": site_key,
            },
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            create = await client.post("https://api.capsolver.com/createTask", json=payload)
            create.raise_for_status()
            task_id = create.json().get("taskId")
            if not task_id:
                return None
            for _ in range(60):
                import asyncio

                await asyncio.sleep(2)
                poll = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": self._key, "taskId": task_id},
                )
                data = poll.json()
                if data.get("status") == "ready":
                    return data.get("solution", {}).get("gRecaptchaResponse")
                if data.get("status") == "failed":
                    return None
        return None


class TwoCaptchaClient(CaptchaSolver):
    name = "twocaptcha"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def is_configured(self) -> bool:
        return bool(self._key)

    async def solve_recaptcha_v2(self, *, site_key: str, page_url: str) -> str | None:
        async with httpx.AsyncClient(timeout=120.0) as client:
            submit = await client.get(
                "https://2captcha.com/in.php",
                params={
                    "key": self._key,
                    "method": "userrecaptcha",
                    "googlekey": site_key,
                    "pageurl": page_url,
                    "json": 1,
                },
            )
            data = submit.json()
            if data.get("status") != 1:
                return None
            req_id = data.get("request")
            import asyncio

            for _ in range(60):
                await asyncio.sleep(3)
                poll = await client.get(
                    "https://2captcha.com/res.php",
                    params={"key": self._key, "action": "get", "id": req_id, "json": 1},
                )
                pdata = poll.json()
                if pdata.get("status") == 1:
                    return pdata.get("request")
                if pdata.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
                    return None
        return None


def get_captcha_solvers() -> list[CaptchaSolver]:
    settings = get_settings()
    solvers: list[CaptchaSolver] = []
    cs = CapSolverClient(settings.capsolver_api_key)
    if cs.is_configured():
        solvers.append(cs)
    tc = TwoCaptchaClient(settings.twocaptcha_api_key)
    if tc.is_configured():
        solvers.append(tc)

    mode = (settings.captcha_provider or "auto").strip().lower()
    if mode == "auto":
        return solvers
    return [s for s in solvers if s.name == mode]


async def solve_recaptcha(site_key: str, page_url: str) -> tuple[str | None, str | None]:
    """Try each configured solver; return (token, solver_name)."""
    for solver in get_captcha_solvers():
        try:
            token = await solver.solve_recaptcha_v2(site_key=site_key, page_url=page_url)
            if token:
                logger.info("CAPTCHA solved via %s", solver.name)
                return token, solver.name
        except Exception:
            logger.exception("CAPTCHA solver %s failed", solver.name)
    return None, None
