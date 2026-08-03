"""Tests for the GitLab MR change-control backend. No network.

A fake HTTP client records every ``request(...)`` call and replays canned
responses, so the three-step ``propose`` flow can be asserted offline.
"""
from __future__ import annotations

import pytest

from sre_agent.changecontrol.base import ChangeItem, ChangeRequest, ChangeSet
from sre_agent.changecontrol.gitlab_mr import GitLabMRBackend

TOKEN = "glpat-SUPERSECRETTOKEN"  # noqa: S105 - test fixture, asserted never to leak


class FakeClient:
    """Records calls and returns queued canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # list of (method, path, data, params)

    def request(self, method, path, data=None, params=None):
        self.calls.append((method, path, data, params))
        return self._responses.pop(0)


def _make_backend(responses, *, project="group/sub/repo"):
    backend = GitLabMRBackend(
        url="https://gitlab.example.com",
        project=project,
        token=TOKEN,
        target_branch="main",
        changes_dir="changesets",
    )
    backend._client = FakeClient(responses)
    return backend


def _changeset(cid="cs-42"):
    return ChangeSet(
        id=cid,
        title="restart nginx on web-01",
        description="drift remediation",
        items=[ChangeItem(operation="restart", target="web-01")],
    )


# ----------------------------------------------------------------- happy path
def test_propose_issues_three_calls_in_order_and_returns_open():
    responses = [
        {"name": "sre-agent/cs-42"},                       # branch ok
        {"file_path": "changesets/cs-42.json"},            # file ok
        {"web_url": "https://gl/x/-/merge_requests/1", "iid": 1},  # MR ok
    ]
    backend = _make_backend(responses)
    change = _changeset("cs-42")

    cr = backend.propose(change)

    assert isinstance(cr, ChangeRequest)
    assert cr.status == "open"
    assert cr.url == "https://gl/x/-/merge_requests/1"
    assert cr.backend == "gitlab_mr"
    assert cr.id == "cs-42"

    calls = backend._client.calls
    assert len(calls) == 3

    # 1) branch creation, with the right source branch + ref via params.
    m0, p0, d0, q0 = calls[0]
    assert m0 == "POST"
    assert p0.endswith("/repository/branches")
    assert q0 == {"branch": "sre-agent/cs-42", "ref": "main"}

    # 2) file commit, urlencoded filepath + correct body.
    m1, p1, d1, q1 = calls[1]
    assert m1 == "POST"
    assert "/repository/files/" in p1
    # filepath "changesets/cs-42.json" is URL-encoded as a single segment.
    assert p1.endswith("changesets%2Fcs-42.json")
    assert d1["branch"] == "sre-agent/cs-42"
    assert d1["content"] == change.to_json()
    assert d1["commit_message"] == "chore(sre-agent): restart nginx on web-01"

    # 3) merge request.
    m2, p2, d2, q2 = calls[2]
    assert m2 == "POST"
    assert p2.endswith("/merge_requests")
    assert d2 == {
        "source_branch": "sre-agent/cs-42",
        "target_branch": "main",
        "title": "restart nginx on web-01",
        "description": "drift remediation",
    }


def test_project_path_is_url_encoded():
    responses = [
        {"name": "b"},
        {"file_path": "f"},
        {"web_url": "https://gl/x/-/merge_requests/1"},
    ]
    backend = _make_backend(responses, project="group/sub/repo")
    backend.propose(_changeset())

    for _, path, _, _ in backend._client.calls:
        # The slashes inside the project path must be encoded as %2F,
        # leaving only the literal API path slashes.
        assert "group%2Fsub%2Frepo" in path
        assert "/group/sub/repo/" not in path


# --------------------------------------------------------------- failure path
def test_failing_mr_step_returns_failed_and_never_leaks_token():
    responses = [
        {"name": "sre-agent/cs-42"},                 # branch ok
        {"file_path": "changesets/cs-42.json"},      # file ok
        {"error": "HTTP 422", "message": "branch already has an MR"},  # MR fails
    ]
    backend = _make_backend(responses)

    cr = backend.propose(_changeset("cs-42"))

    assert cr.status == "failed"
    assert cr.url is None
    assert "422" in cr.detail
    # The token must never appear anywhere in the result.
    assert TOKEN not in cr.detail
    assert TOKEN not in (cr.url or "")
    assert TOKEN not in repr(cr)


def test_failing_branch_step_short_circuits():
    responses = [
        {"error": "HTTP 403", "message": "forbidden"},  # branch fails
    ]
    backend = _make_backend(responses)

    cr = backend.propose(_changeset())

    assert cr.status == "failed"
    assert "403" in cr.detail
    assert TOKEN not in cr.detail
    # Only one call was made; file + MR steps were skipped.
    assert len(backend._client.calls) == 1


def test_failing_file_step_short_circuits():
    responses = [
        {"name": "b"},                                 # branch ok
        {"error": "tls_verification_failed", "reason": "bad cert"},  # file fails
    ]
    backend = _make_backend(responses)

    cr = backend.propose(_changeset())

    assert cr.status == "failed"
    assert "tls_verification_failed" in cr.detail
    assert len(backend._client.calls) == 2


def test_exception_in_client_yields_failed_without_token():
    class BoomClient:
        calls = []

        def request(self, *a, **k):
            raise RuntimeError(f"boom with {TOKEN}")

    backend = _make_backend([])
    backend._client = BoomClient()

    cr = backend.propose(_changeset())

    assert cr.status == "failed"
    # Only the exception type is echoed, so the token-bearing message can't leak.
    assert TOKEN not in cr.detail
    assert "RuntimeError" in cr.detail


# ----------------------------------------------------------------- https-only
def test_rejects_non_https_url():
    with pytest.raises(ValueError):
        GitLabMRBackend(
            url="http://gitlab.example.com",
            project="group/repo",
            token=TOKEN,
        )
