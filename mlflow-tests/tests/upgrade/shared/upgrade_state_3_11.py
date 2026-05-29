"""Shared MLflow upgrade scenario state for 3.11-only additions."""

from __future__ import annotations

TRACE_STATE = {
    "experiment_name": "upgrade311-exp-attachment-traces",
    "sessions": [
        {
            "session_id": "upgrade311-session-1",
            "user": "upgrade311-user-1",
            "traces": [
                {
                    "trace_name": "upgrade311-trace-1-1",
                    "inputs": {
                        "message": "upgrade311 trace session 1 message 1",
                        "attachment_name": "upgrade311-session-1-trace-1.txt",
                    },
                    "outputs": {"result": "upgrade311 trace session 1 result 1"},
                    "attachment_content": "upgrade311 trace attachment content session 1 trace 1",
                },
                {
                    "trace_name": "upgrade311-trace-1-2",
                    "inputs": {"message": "upgrade311 trace session 1 message 2"},
                    "outputs": {"result": "upgrade311 trace session 1 result 2"},
                },
            ],
        },
        {
            "session_id": "upgrade311-session-2",
            "user": "upgrade311-user-2",
            "traces": [
                {
                    "trace_name": "upgrade311-trace-2-1",
                    "inputs": {
                        "message": "upgrade311 trace session 2 message 1",
                        "attachment_name": "upgrade311-session-2-trace-1.txt",
                    },
                    "outputs": {"result": "upgrade311 trace session 2 result 1"},
                    "attachment_content": "upgrade311 trace attachment content session 2 trace 1",
                },
                {
                    "trace_name": "upgrade311-trace-2-2",
                    "inputs": {"message": "upgrade311 trace session 2 message 2"},
                    "outputs": {"result": "upgrade311 trace session 2 result 2"},
                },
            ],
        },
    ],
}
