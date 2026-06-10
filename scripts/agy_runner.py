"""
scripts/agy_runner.py — Shared AGY model runner with cascade and quota detection.

Model strategy:
  TIER 1 (Deep analysis): Claude Sonnet → Gemini Pro fallback
  TIER 2 (Routine tasks): Gemini Flash only, never Claude
  NEVER: Claude Opus, GPT-OSS (too expensive / shares pool)

Quota detection: agy returns quota error text → catch and cascade.
Path resolution: env var → which → known path → fail loudly.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

TIER1_CASCADE = [
    "Claude Sonnet 4.6 (Thinking)",
    "Gemini 3.1 Pro (High)",
    "Gemini 3.1 Pro (Low)",
]

TIER2_CASCADE = [
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.1 Pro (Low)",
]

FORBIDDEN_MODELS = [
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
]

QUOTA_ERROR_PATTERNS = [
    "quota exceeded",
    "rate limit",
    "too many requests",
    "429",
    "quota has been exhausted",
    "resource exhausted",
    "refreshes in",
    "quota available: 0",
    "session limit",
    "usage limit",
]


def get_agy_bin() -> str:
    env_path = os.environ.get("GEMINI_BIN", "").strip()
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    which_path = shutil.which("agy")
    if which_path:
        return which_path

    known_paths = [
        "/home/ubuntu/tools/antigravity/agy",
        "/usr/local/bin/agy",
        "/opt/antigravity/agy",
    ]
    for path in known_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    raise RuntimeError(
        "agy binary not found. Set GEMINI_BIN=/path/to/agy in .env "
        "or ensure agy is in PATH. Cannot proceed without AGY."
    )


def is_quota_error(output: str) -> bool:
    if not output:
        return False
    lower = output.lower()
    return any(pattern in lower for pattern in QUOTA_ERROR_PATTERNS)


def run_agy(
    prompt: str,
    model: str,
    timeout_seconds: int = 120,
    add_dirs: Optional[list] = None,
    input_data: Optional[str] = None,
    skip_permissions: bool = True,
) -> Optional[str]:
    agy_bin = get_agy_bin()
    cmd = [agy_bin, "--model", model, "--print", prompt]
    if skip_permissions:
        cmd.insert(2, "--dangerously-skip-permissions")

    if add_dirs:
        for d in add_dirs:
            cmd.extend(["--add-dir", d])

    logger.debug("Running agy: model=%s, prompt_len=%d", model, len(prompt))

    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ},
        )

        output = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            logger.warning("agy exited %d: %s", result.returncode, stderr[:200])
            if is_quota_error(stderr):
                return None
            return None

        if is_quota_error(output):
            logger.warning("Quota exhausted for model: %s", model)
            return None

        if not output:
            logger.warning("agy returned empty output for model: %s", model)
            return None

        logger.debug("agy success: model=%s, output_len=%d", model, len(output))
        return output

    except subprocess.TimeoutExpired:
        logger.warning("agy timed out after %ds: model=%s", timeout_seconds, model)
        return None
    except FileNotFoundError:
        logger.error("agy binary not found at: %s", agy_bin)
        return None
    except Exception as e:
        logger.error("agy unexpected error: %s", e)
        return None


def run_with_cascade(
    prompt: str,
    tier: int = 2,
    timeout_seconds: int = 120,
    add_dirs: Optional[list] = None,
    input_data: Optional[str] = None,
    task_name: str = "unknown",
) -> Optional[str]:
    cascade = TIER1_CASCADE if tier == 1 else TIER2_CASCADE

    for model in cascade:
        if model in FORBIDDEN_MODELS:
            logger.error("FORBIDDEN model attempted: %s — skipping", model)
            continue

        logger.info("Trying model: %s (task=%s, tier=%d)", model, task_name, tier)
        result = run_agy(prompt, model, timeout_seconds, add_dirs, input_data)

        if result is not None:
            logger.info("Success with model: %s (task=%s)", model, task_name)
            return result

        logger.warning("Model failed/quota: %s — trying next in cascade", model)
        time.sleep(1)

    logger.error(
        "ALL models in Tier %d cascade exhausted for task: %s. "
        "Cascade attempted: %s. Skipping task.",
        tier, task_name, cascade,
    )
    return None


def run_watchman(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=2, timeout_seconds=60,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="watchman")


def run_log_review(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=2, timeout_seconds=120,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="log_review")


def run_trade_coach(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=1, timeout_seconds=180,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="trade_coach")


def run_ceo_summary(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=1, timeout_seconds=180,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="ceo_summary")


def run_auditor(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=1, timeout_seconds=240,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="auditor")


def run_weekly_patterns(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=1, timeout_seconds=300,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="weekly_patterns")


def run_data_integrity(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=1, timeout_seconds=240,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="data_integrity")


def run_premarket_brief(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    cascade = [
        "Gemini 3.5 Flash (Medium)",
        "Gemini 3.5 Flash (High)",
        "Gemini 3.1 Pro (Low)",
    ]
    for model in cascade:
        result = run_agy(prompt, model, timeout_seconds=120,
                         add_dirs=add_dirs, input_data=input_data)
        if result:
            logger.info("premarket_brief success: %s", model)
            return result
        time.sleep(1)
    logger.error("premarket_brief: all models failed")
    return None


def run_metrics(prompt: str, input_data: Optional[str] = None, add_dirs=None) -> Optional[str]:
    return run_with_cascade(prompt, tier=2, timeout_seconds=60,
                            add_dirs=add_dirs, input_data=input_data,
                            task_name="metrics")
