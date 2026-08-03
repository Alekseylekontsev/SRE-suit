"""GitOps change control — propose risky changes as reviewable artifacts.

Operations classified ``PR_ONLY`` (high blast radius) are NOT executed directly
against the control plane. Instead the executor builds a :class:`ChangeSet` and
hands it to a :class:`ChangeControlBackend`, which opens a reviewable change
request (a file in a GitOps repo, or a GitLab merge request). Human approval +
CI then happen out-of-band on that request (design principle P5).
"""
from .base import (
    ChangeControlBackend,
    ChangeItem,
    ChangeRequest,
    ChangeSet,
    FileChangeControlBackend,
)

__all__ = [
    "ChangeItem",
    "ChangeSet",
    "ChangeRequest",
    "ChangeControlBackend",
    "FileChangeControlBackend",
]
