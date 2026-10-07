"""Async client for the ClinicalTrials.gov Data API v2 with pagination, retries, and caching."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from app.ctgov.query_builder import build_params
from app.schemas.plan import CohortFilters

logger = logging.getLogger(__name__)


class CTGovError(Exception):
    """Raised when the upstream API fails in a way we cannot recover from."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class FetchResult:
    studies: list[dict]
    total_count: int
    truncated: bool
    requests: list[str] = field(default_factory=list)
    from_cache: bool = False


class CTGovClient:
    """Thin, well-behaved wrapper: one method, bounded output, explicit errors."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str = "https://clinicaltrials.gov/api/v2",
        page_size: int = 1000,
        max_retries: int = 3,
        cache_ttl_seconds: int = 600,
    ):
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._page_size = max(1, min(page_size, 1000))
        self._max_retries = max_retries
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, FetchResult]] = {}

    async def fetch_studies(self, filters: CohortFilters, max_trials: int) -> FetchResult:
        """Fetch up to ``max_trials`` studies matching ``filters``, following pagination."""
        params = build_params(filters, page_size=min(self._page_size, max_trials))
        cache_key = json.dumps({"p": params, "n": max_trials}, sort_keys=True)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            result = cached[1]
            return FetchResult(result.studies, result.total_count, result.truncated, list(result.requests), True)

        studies: list[dict] = []
        requests: list[str] = []
        total = 0
        page_token: str | None = None
        while True:
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
            payload, url = await self._get("/studies", page_params)
            requests.append(url)
            total = int(payload.get("totalCount", total) or 0)
            studies.extend(payload.get("studies", []))
            page_token = payload.get("nextPageToken")
            if not page_token or len(studies) >= max_trials:
                break

        studies = studies[:max_trials]
        result = FetchResult(studies=studies, total_count=total, truncated=len(studies) < total, requests=requests)
        self._cache[cache_key] = (time.monotonic() + self._cache_ttl, result)
        return result

    async def _get(self, path: str, params: dict[str, str]) -> tuple[dict, str]:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.get(url, params=params)
            except httpx.HTTPError as exc:  # network-level failure, retry
                last_error = exc
                await asyncio.sleep(0.5 * 2**attempt)
                continue

            if response.status_code == 200:
                try:
                    return response.json(), str(response.url)
                except ValueError as exc:
                    raise CTGovError(f"ClinicalTrials.gov returned non-JSON content: {exc}") from exc

            if response.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                await asyncio.sleep(0.5 * 2**attempt)
                continue

            # 4xx errors from this API are plain text explaining what was wrong with the query.
            raise CTGovError(
                f"ClinicalTrials.gov request failed ({response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )

        raise CTGovError(f"ClinicalTrials.gov unreachable after retries: {last_error}")
