"""Unit test for the shared bounded agent-execution pool (core/executor.py)."""

from concurrent.futures import ThreadPoolExecutor

from core.config import settings
from core.executor import agent_executor


def test_agent_executor_is_bounded_to_configured_concurrency():
    assert isinstance(agent_executor, ThreadPoolExecutor)
    assert agent_executor._max_workers == settings.AGENT_EXECUTION_CONCURRENCY
