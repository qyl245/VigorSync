"""HTTP client for the local Live Engine."""

from __future__ import annotations

from typing import Any

import httpx


class LiveEngineError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class LiveClient:
    def __init__(self, base_url: str, internal_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = (
            {"X-Internal-Token": internal_token}
            if internal_token
            else {}
        )
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        timeout = kwargs.pop("timeout", 30.0)
        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                timeout=timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise LiveEngineError(
                "live_engine_unavailable",
                f"Live Engine is unavailable: {exc}",
            ) from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
                error = payload.get("error") or {}
                message = error.get("message") or payload.get("detail")
                code = error.get("code") or "live_engine_error"
            except Exception:
                message = response.text
                code = "live_engine_error"
            raise LiveEngineError(
                str(code),
                str(message or "Live Engine request failed"),
                status_code=response.status_code,
            )
        return response.json()

    async def start(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/v1/sessions/{session_id}/start",
            json=payload,
        )

    async def pause(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/v1/sessions/{session_id}/pause",
        )

    async def resume(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/v1/sessions/{session_id}/resume",
        )

    async def stop(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/v1/sessions/{session_id}/stop",
        )

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/health", timeout=1.0)

    async def close(self) -> None:
        await self._client.aclose()
