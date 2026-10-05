"""Background execution abstraction.

The API never runs geometry inside the request: it submits `run_generation(job_id)` to a
TaskQueue. The MVP uses an in-process thread pool; a Celery/Redis implementation only needs
to provide `submit` (e.g. `generate_task.delay(job_id)`).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class TaskQueue(ABC):
    @abstractmethod
    def submit(self, fn: Callable[..., None], *args) -> None: ...

    def shutdown(self) -> None:
        return None


class ThreadPoolTaskQueue(TaskQueue):
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="puzzle-worker")

    def submit(self, fn: Callable[..., None], *args) -> None:
        future = self._pool.submit(fn, *args)
        future.add_done_callback(lambda f: f.exception() and logger.error("task crashed", exc_info=f.exception()))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class InlineTaskQueue(TaskQueue):
    """Runs tasks synchronously (tests, CLI)."""

    def submit(self, fn: Callable[..., None], *args) -> None:
        fn(*args)
