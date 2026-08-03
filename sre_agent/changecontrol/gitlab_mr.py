"""GitLab merge-request change-control backend.

Turns a :class:`ChangeSet` into a reviewable GitLab merge request. All traffic
goes through :class:`~sre_agent.connectors.http.BaseHTTPClient`, so this backend
inherits its security properties for free:

- **https-only**: a non-``https://`` ``url`` is rejected in ``__init__`` (the
  ``BaseHTTPClient`` constructor raises ``ValueError``).
- **TLS fail-closed**: a failed cert check returns ``{"error": ...}`` and is
  never retried.
- **no 4xx retry**: a 401/403/404/422 is a hard stop, not silently re-issued.

The GitLab API authenticates with a ``PRIVATE-TOKEN`` header (NOT ``Bearer``).
We hand that header to the client via its ``auth_header=(name, value)`` hook so
the token stays inside the client and never appears in a ChangeRequest, URL,
log line, or error detail.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from sre_agent.changecontrol.base import (
    ChangeControlBackend,
    ChangeRequest,
    ChangeSet,
)
from sre_agent.connectors.http import BaseHTTPClient

logger = logging.getLogger(__name__)


class GitLabMRBackend(ChangeControlBackend):
    """Open a GitLab merge request for a :class:`ChangeSet`.

    ``propose`` performs three API calls, each via the hardened client:
      1. create the source branch off ``target_branch``;
      2. commit the changeset JSON as a file on that branch;
      3. open a merge request from the source branch into ``target_branch``.

    Any error in any step (the client returning a dict with an ``"error"`` key,
    or an unexpected exception) yields a ``ChangeRequest`` with ``status="failed"``
    and a short, token-free ``detail``.
    """

    name = "gitlab_mr"

    def __init__(
        self,
        url: str,
        project: str,
        token: str,
        target_branch: str = "main",
        changes_dir: str = "changesets",
    ) -> None:
        # The encoded project path is reused in every API path. GitLab accepts a
        # URL-encoded "namespace/project" in place of a numeric :id.
        self._project_enc = quote(project, safe="")
        self._target_branch = target_branch
        self._changes_dir = changes_dir.strip("/")
        # Hand the PRIVATE-TOKEN header to the client's auth hook. The client
        # stores it privately and never embeds it in error strings; we never see
        # it again here. https-only / TLS verification is enforced by __init__.
        self._client = BaseHTTPClient(
            url,
            auth_header=("PRIVATE-TOKEN", token),
        )

    # ------------------------------------------------------------------ helpers
    def _base(self) -> str:
        return f"/api/v4/projects/{self._project_enc}"

    @staticmethod
    def _is_error(resp: object) -> bool:
        return isinstance(resp, dict) and "error" in resp

    @staticmethod
    def _short_error(step: str, resp: dict) -> str:
        """Build a short, token-free failure detail from a client error dict.

        Only the client's own ``error``/``message`` fields are echoed; these are
        already truncated and scrubbed by ``BaseHTTPClient`` and never contain
        the auth header.
        """
        err = resp.get("error", "error")
        msg = resp.get("message") or resp.get("reason") or ""
        detail = f"{step}: {err}"
        if msg:
            detail = f"{detail}: {msg}"
        return detail[:500]

    # ----------------------------------------------------------------- propose
    def propose(self, change: ChangeSet) -> ChangeRequest:
        src_branch = f"sre-agent/{change.id}"
        filepath = f"{self._changes_dir}/{change.id}.json"
        base = self._base()

        def failed(detail: str) -> ChangeRequest:
            # NEVER place the token in detail/url; it is not available here anyway.
            return ChangeRequest(
                id=change.id,
                backend=self.name,
                status="failed",
                detail=detail[:500],
            )

        try:
            # 1) create the source branch off the target branch.
            branch_resp = self._client.request(
                "POST",
                f"{base}/repository/branches",
                params={"branch": src_branch, "ref": self._target_branch},
            )
            if self._is_error(branch_resp):
                return failed(self._short_error("create_branch", branch_resp))

            # 2) commit the changeset JSON onto that branch. The file path is
            #    URL-encoded as a single path segment per the GitLab files API.
            file_resp = self._client.request(
                "POST",
                f"{base}/repository/files/{quote(filepath, safe='')}",
                data={
                    "branch": src_branch,
                    "content": change.to_json(),
                    "commit_message": f"chore(sre-agent): {change.title}",
                },
            )
            if self._is_error(file_resp):
                return failed(self._short_error("commit_file", file_resp))

            # 3) open the merge request.
            mr_resp = self._client.request(
                "POST",
                f"{base}/merge_requests",
                data={
                    "source_branch": src_branch,
                    "target_branch": self._target_branch,
                    "title": change.title,
                    "description": change.description,
                },
            )
            if self._is_error(mr_resp):
                return failed(self._short_error("open_mr", mr_resp))

            web_url = mr_resp.get("web_url") if isinstance(mr_resp, dict) else None
            return ChangeRequest(
                id=change.id,
                backend=self.name,
                status="open",
                url=web_url,
            )
        except Exception as e:  # noqa: BLE001 - never raise, never leak the token
            # Echo only the exception type to be certain no token-bearing value
            # ends up in the detail.
            logger.error("gitlab_mr propose failed: %s", type(e).__name__)
            return failed(f"unexpected error: {type(e).__name__}")


__all__ = ["GitLabMRBackend"]
