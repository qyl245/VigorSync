"""HTTP client for the loopback-only offline analysis worker."""

from __future__ import annotations

from typing import Any

import httpx


class OfflineWorkerError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class OfflineClient:
    def __init__(self, base_url: str, internal_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Internal-Token": internal_token}
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                timeout=timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise OfflineWorkerError(
                "offline_worker_unavailable",
                f"Offline Worker is unavailable: {exc}",
            ) from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
                error = payload.get("error") or {}
                code = error.get("code") or payload.get("detail")
                message = error.get("message") or payload.get("detail")
            except Exception:
                code = "offline_worker_error"
                message = response.text
            raise OfflineWorkerError(
                str(code or "offline_worker_error"),
                str(message or "Offline Worker request failed"),
                status_code=response.status_code,
            )
        return response

    async def enqueue(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/internal/v1/jobs",
            json=payload,
            timeout=10.0,
        )
        return response.json()

    async def get_job(self, job_id: str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"/api/v1/jobs/{job_id}",
            timeout=3.0,
        )
        return response.json()

    async def health(self) -> dict[str, Any]:
        response = await self._request(
            "GET",
            "/api/v1/health",
            timeout=2.0,
        )
        return response.json()

    async def get_result(self, job_id: str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"/api/v1/jobs/{job_id}/result",
            timeout=10.0,
        )
        return response.json()

    async def cancel(self, job_id: str) -> None:
        await self._request(
            "POST",
            f"/api/v1/jobs/{job_id}/cancel",
            timeout=5.0,
        )

    async def get_report(self, job_id: str) -> tuple[bytes, str]:
        response = await self._request(
            "GET",
            f"/api/v1/jobs/{job_id}/feedback-report",
            timeout=20.0,
        )
        return response.content, response.headers.get(
            "content-type",
            "text/html; charset=utf-8",
        )

    async def get_media(self, job_id: str) -> tuple[bytes, str]:
        response = await self._request(
            "GET",
            f"/api/v1/jobs/{job_id}/media",
            timeout=60.0,
        )
        return response.content, response.headers.get(
            "content-type",
            "video/webm",
        )

    async def get_frame(self, job_id: str, frame_name: str) -> tuple[bytes, str]:
        response = await self._request(
            "GET",
            f"/api/v1/jobs/{job_id}/frames/{frame_name}",
            timeout=20.0,
        )
        return response.content, response.headers.get(
            "content-type",
            "image/png",
        )

    async def close(self) -> None:
        await self._client.aclose()
