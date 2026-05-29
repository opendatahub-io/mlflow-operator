"""Shared MLflow upgrade scenario state for the 3.10 compatibility suite."""

from __future__ import annotations

EXPERIMENT_RUNS_STATE = {
    "experiment_name": "upgrade-exp-static-runs",
    "runs": [
        {
            "run_name": "upgrade-run-static-1",
            "params": {
                "optimizer": "adam",
                "learning_rate": "0.001",
                "batch_size": "32",
            },
            "metrics": {
                "loss": 0.125,
                "accuracy": 0.987,
            },
            "artifact_file": "run-1-summary.txt",
            "artifact_content": "static artifact content for run 1",
        },
        {
            "run_name": "upgrade-run-static-2",
            "params": {
                "optimizer": "sgd",
                "learning_rate": "0.010",
                "batch_size": "64",
            },
            "metrics": {
                "loss": 0.250,
                "accuracy": 0.975,
            },
            "artifact_file": "run-2-summary.txt",
            "artifact_content": "static artifact content for run 2",
        },
    ],
}

TRACE_STATE = {
    "experiment_name": "upgrade-exp-static-traces",
    "sessions": [
        {
            "session_id": "upgrade-session-1",
            "user": "upgrade-user-1",
            "traces": [
                {
                    "trace_name": "upgrade-trace-1-1",
                    "inputs": {
                        "message": "trace session 1 message 1",
                        "attachment_name": "session-1-trace-1.txt",
                    },
                    "outputs": {"result": "trace session 1 result 1"},
                    "attachment_content": "trace attachment content session 1 trace 1",
                },
                {
                    "trace_name": "upgrade-trace-1-2",
                    "inputs": {"message": "trace session 1 message 2"},
                    "outputs": {"result": "trace session 1 result 2"},
                },
                {
                    "trace_name": "upgrade-trace-1-3",
                    "inputs": {"message": "trace session 1 message 3"},
                    "outputs": {"result": "trace session 1 result 3"},
                },
            ],
        },
        {
            "session_id": "upgrade-session-2",
            "user": "upgrade-user-2",
            "traces": [
                {
                    "trace_name": "upgrade-trace-2-1",
                    "inputs": {
                        "message": "trace session 2 message 1",
                        "attachment_name": "session-2-trace-1.txt",
                    },
                    "outputs": {"result": "trace session 2 result 1"},
                    "attachment_content": "trace attachment content session 2 trace 1",
                },
                {
                    "trace_name": "upgrade-trace-2-2",
                    "inputs": {"message": "trace session 2 message 2"},
                    "outputs": {"result": "trace session 2 result 2"},
                },
                {
                    "trace_name": "upgrade-trace-2-3",
                    "inputs": {"message": "trace session 2 message 3"},
                    "outputs": {"result": "trace session 2 result 3"},
                },
            ],
        },
    ],
}

REGISTERED_MODELS_STATE = {
    "experiment_name": "upgrade-exp-static-models",
    "models": [
        {
            "name": "upgrade-registered-model-1",
            "versions": [
                {
                    "description": "upgrade model 1 version 1",
                    "run_name": "upgrade-model-1-run-1",
                },
                {
                    "description": "upgrade model 1 version 2",
                    "run_name": "upgrade-model-1-run-2",
                },
            ],
        },
        {
            "name": "upgrade-registered-model-2",
            "versions": [
                {
                    "description": "upgrade model 2 version 1",
                    "run_name": "upgrade-model-2-run-1",
                },
                {
                    "description": "upgrade model 2 version 2",
                    "run_name": "upgrade-model-2-run-2",
                },
            ],
        },
    ],
}

PROMPTS_STATE = {
    "prompts": [
        {
            "name": "upgrade-prompt-1",
            "description": "upgrade prompt 1",
            "versions": [
                {
                    "template": "Summarize this text in {{sentences}} sentences.",
                    "description": "upgrade prompt 1 version 1",
                },
                {
                    "template": "Summarize this text in exactly {{sentences}} concise sentences.",
                    "description": "upgrade prompt 1 version 2",
                },
            ],
        },
        {
            "name": "upgrade-prompt-2",
            "description": "upgrade prompt 2",
            "versions": [
                {
                    "template": [
                        {"role": "system", "content": "You are a careful assistant."},
                        {"role": "user", "content": "Answer this: {{question}}"},
                    ],
                    "description": "upgrade prompt 2 version 1",
                },
                {
                    "template": [
                        {"role": "system", "content": "You are a precise assistant."},
                        {"role": "user", "content": "Answer this clearly: {{question}}"},
                    ],
                    "description": "upgrade prompt 2 version 2",
                },
            ],
        },
    ],
}
