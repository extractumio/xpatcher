"""Thread pool for concurrent agent execution (v2). v1 uses sequential execution."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

T = TypeVar("T")


class AgentPool:
    """Manages concurrent agent execution with semaphore-based limiting."""

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers

    def execute_sequential(self, tasks: list[Callable[[], T]]) -> list[T]:
        """v1: Execute tasks sequentially."""
        return [task() for task in tasks]

    def execute_parallel(self, tasks: list[Callable[[], T]]) -> list[T]:
        """v2: Execute tasks in parallel with thread pool."""
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(task): i for i, task in enumerate(tasks)}
            result_map = {}
            for future in as_completed(futures):
                idx = futures[future]
                result_map[idx] = future.result()
            results = [result_map[i] for i in range(len(tasks))]
        return results
