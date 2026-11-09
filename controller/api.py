"""Resilient HTTP client for the simulator API.

Provides typed exceptions, automatic retries with exponential backoff and jitter,
explicit timeouts, idempotency enforcement, and credit ledger accounting.
"""
from __future__ import annotations

import os
import random
import sys
import time
from typing import Any, Generator

import httpx

from .store import Store


class SimApiError(Exception):
    """Base exception for all simulator API errors."""

    def __init__(self, status_code: int, code: str, detail: Any):
        super().__init__(f"HTTP {status_code} [{code}]: {detail}")
        self.status_code = status_code
        self.code = code
        self.detail = detail


class StaleBaseVersionError(SimApiError):
    """Raised when proposal or apply fails due to base_version != current_version (409)."""
    pass


class AutoApplyNotPermittedError(SimApiError):
    """Raised when auto-apply is disallowed or exceeds max_shift/dims (403)."""
    pass


class DuplicateProposalIdError(SimApiError):
    """Raised when proposal_id has already been used (409)."""
    pass


class OutOfCreditsError(SimApiError):
    """Raised when LLM or validation call exceeds credit budget (402)."""
    pass


class ApprovalLapsedError(SimApiError):
    """Raised when approved change was not applied within apply_window_hours (410)."""
    pass


class SimulatorFinishedError(SimApiError):
    """Raised when sim/advance is called after scenario completion (409)."""
    pass


class SimClient:
    """Client for simulator HTTP API with retry, jitter, and typed error handling."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8700",
        timeout: float = 60.0,
        max_retries: int = 3,
        store: Store | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.store = store
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                if resp.status_code < 400:
                    return resp

                # Handle specific domain error codes
                self._handle_error_status(resp)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt == self.max_retries - 1:
                    raise
                # Exponential backoff with full jitter
                sleep_s = (0.2 * (2**attempt)) + random.uniform(0.05, 0.15)
                time.sleep(sleep_s)

        if last_exc:
            raise last_exc
        raise RuntimeError("Unreachable request loop exit")

    def _handle_error_status(self, resp: httpx.Response) -> None:
        status = resp.status_code
        try:
            body = resp.json()
            code = body.get("error", "unknown")
            detail = body.get("detail", body)
        except Exception:
            code = "http_error"
            detail = resp.text

        if status == 409 and code == "stale_base_version":
            raise StaleBaseVersionError(status, code, detail)
        if status == 409 and code == "duplicate_proposal_id":
            raise DuplicateProposalIdError(status, code, detail)
        if status == 409 and code == "finished":
            raise SimulatorFinishedError(status, code, detail)
        if status == 403 and code == "auto_apply_not_permitted":
            raise AutoApplyNotPermittedError(status, code, detail)
        if status == 402 and code == "out_of_credits":
            raise OutOfCreditsError(status, code, detail)
        if status == 410 and code == "approval_lapsed":
            raise ApprovalLapsedError(status, code, detail)

        raise SimApiError(status, code, detail)

    # --------------------------------------------------------------------------
    # Simulator State & Advancement
    # --------------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health").json()

    def get_state(self) -> dict[str, Any]:
        return self._request("GET", "/sim/state").json()

    def advance(self, minutes: int = 15) -> dict[str, Any]:
        """Advances simulation by minutes (must be multiple of tick_minutes)."""
        return self._request("POST", "/sim/advance", json_body={"minutes": minutes}).json()

    # --------------------------------------------------------------------------
    # Events & Chunks
    # --------------------------------------------------------------------------

    def list_chunks(self, kind: str = "events", after: int = -1) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/events/chunks",
            params={"kind": kind, "after": after},
        ).json()

    def stream_chunk_lines(self, chunk_id: int) -> Generator[str, None, None]:
        """Streams chunk NDJSON lines one by one to avoid loading entire chunks into RAM."""
        url = f"{self.base_url}/events/chunks/{chunk_id}"
        with self._client.stream("GET", url) as stream_resp:
            if stream_resp.status_code != 200:
                self._handle_error_status(stream_resp)
            for line in stream_resp.iter_lines():
                line = line.strip()
                if line:
                    yield line

    # --------------------------------------------------------------------------
    # Workflows & Configs
    # --------------------------------------------------------------------------

    def get_workflows(self) -> list[dict[str, Any]]:
        return self._request("GET", "/workflows").json()

    def get_workflow(self, wid: str) -> dict[str, Any]:
        return self._request("GET", f"/workflows/{wid}").json()

    def get_config(self, wid: str) -> dict[str, Any]:
        return self._request("GET", f"/workflows/{wid}/config").json()

    def get_config_history(self, wid: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/workflows/{wid}/config/history").json()

    # --------------------------------------------------------------------------
    # Governance: Approvals & Auto-Apply
    # --------------------------------------------------------------------------

    def request_approval(self, body: dict[str, Any]) -> dict[str, Any]:
        """Submits a proposal to the human approver via POST /approvals."""
        pid = body.get("proposal_id")
        res = self._request(
            "POST",
            "/approvals",
            json_body=body,
            idempotency_key=pid,
        ).json()
        if os.environ.get("ENABLE_TEST_CRASH_HOOKS") == "1" and os.environ.get("TEST_CRASH_ON_SUBMIT") == "1":
            os.environ["TEST_CRASH_ON_SUBMIT"] = "0"
            print("\n[TEST CRASH] Process killed immediately between submit and recording...", file=sys.stderr)
            import os as _os; _os._exit(42)
        return res

    def list_approvals(
        self,
        workflow_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if workflow_id:
            params["workflow_id"] = workflow_id
        if status:
            params["status"] = status
        return self._request("GET", "/approvals", params=params).json()

    def get_approval(self, aid: str) -> dict[str, Any]:
        return self._request("GET", f"/approvals/{aid}").json()

    def apply_approval(self, wid: str, approval_id: str) -> dict[str, Any]:
        """Applies an approved proposal via POST /workflows/{wid}/apply."""
        res = self._request(
            "POST",
            f"/workflows/{wid}/apply",
            json_body={"approval_id": approval_id},
        ).json()
        if os.environ.get("ENABLE_TEST_CRASH_HOOKS") == "1" and os.environ.get("TEST_CRASH_ON_APPLY") == "1":
            os.environ["TEST_CRASH_ON_APPLY"] = "0"
            print("\n[TEST CRASH] Process killed immediately between apply and recording...", file=sys.stderr)
            import os as _os; _os._exit(43)
        return res

    def auto_apply(self, wid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Applies configuration directly via PUT /workflows/{wid}/config (if policy allows)."""
        pid = body.get("proposal_id")
        return self._request(
            "PUT",
            f"/workflows/{wid}/config",
            json_body=body,
            idempotency_key=pid,
        ).json()

    # --------------------------------------------------------------------------
    # LLM & Synthetic Validation (Credit-Bearing)
    # --------------------------------------------------------------------------

    def suggest_fixes(
        self,
        workflow_id: str,
        problem: str,
        sim_minute: int = 0,
        sim_ts: str = "",
        tenant_id: str = "",
    ) -> dict[str, Any]:
        """Requests LLM prompt fix suggestions (Costs 3 credits)."""
        res = self._request(
            "POST",
            "/llm/suggest-fixes",
            json_body={"workflow_id": workflow_id, "problem": problem},
        ).json()

        if self.store:
            rem = float(res.get("credits_remaining", 0.0))
            self.store.record_credit_spend(
                sim_minute=sim_minute,
                endpoint="/llm/suggest-fixes",
                cost=3.0,
                credits_remaining=rem,
                purpose=f"Prompt fix suggestion for {workflow_id}: {problem[:40]}",
                tenant_id=tenant_id,
                ts=sim_ts,
            )
        return res

    def run_validation(
        self,
        workflow_id: str,
        prompt_version: str,
        n: int = 200,
        sim_minute: int = 0,
        sim_ts: str = "",
        tenant_id: str = "",
    ) -> dict[str, Any]:
        """Runs synthetic prompt safety validation (Costs ceil(n/100) credits)."""
        import math
        cost = math.ceil(n / 100)
        res = self._request(
            "POST",
            "/validation/runs",
            json_body={"workflow_id": workflow_id, "prompt_version": prompt_version, "n": n},
        ).json()

        if self.store:
            rem = float(res.get("credits_remaining", 0.0))
            self.store.record_credit_spend(
                sim_minute=sim_minute,
                endpoint="/validation/runs",
                cost=float(cost),
                credits_remaining=rem,
                purpose=f"Validation of {prompt_version} (n={n}) for {workflow_id}",
                tenant_id=tenant_id,
                ts=sim_ts,
            )
        return res
