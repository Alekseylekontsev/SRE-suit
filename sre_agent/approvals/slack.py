"""Slack approval backend (primary channel).

This is a minimal integration that posts a batch summary to a Slack
Incoming Webhook URL and returns basic approval tokens via a simple
response model. It does not implement Slack interactive components in this
sketch to keep dependencies light.
"""
from __future__ import annotations

import json
import urllib.request
from urllib.error import URLError

from .base import Batch


def post_to_slack(webhook_url: str, payload: dict) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201, 204)
    except URLError:
        return False


def notify_batch(batch: Batch, webhook_url: str) -> bool:
    message = {
        "text": f"SRE Batch {batch.batch_id} requires approvals for {len(batch.actions)} actions",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Batch {batch.batch_id}*"}},
        ],
    }
    # Add per-action details
    for a in batch.actions:
        message["blocks"].append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"• {a.id}: {a.name} -> {a.target} (status: {a.status})",
                },
            }
        )
    return post_to_slack(webhook_url, message)
