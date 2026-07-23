"""
Shared bounded thread pool for agent (LLM) execution.

Starlette runs sync `def` routes and BackgroundTasks on its own default
threadpool (~40 workers), shared by every route in the app. A ~15s agent
call previously occupied one of those workers per in-flight request, and
Slack's BackgroundTasks-dispatched process_event() competed for the same
pool — so a burst of HTTP + Slack traffic could saturate request handling
generally, not just agent throughput.

This executor is deliberately separate and bounded to
AGENT_EXECUTION_CONCURRENCY: both api/routes/query.py (via SSE) and
api/routes/slack.py submit agent work here instead. It's a capacity cap,
not a per-user throttle — callers queue rather than get rejected, so it
doesn't reintroduce the rate-limiting feature that was deliberately removed
(see SPEC.md FR-6) and left out of this pass.
"""

from concurrent.futures import ThreadPoolExecutor

from core.config import settings

agent_executor = ThreadPoolExecutor(
    max_workers=settings.AGENT_EXECUTION_CONCURRENCY,
    thread_name_prefix="agent-exec",
)
