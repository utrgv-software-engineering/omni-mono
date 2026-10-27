"""In-memory task storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass
class Task:
    """One task."""

    id: int
    title: str
    done: bool = False

    def to_dict(self) -> dict:
        """The task as JSON-ready data."""
        return asdict(self)


class Store:
    """Tasks kept in memory, newest first when listed."""

    def __init__(self) -> None:
        self._tasks: Dict[int, Task] = {}
        self._next_id = 1

    def create(self, title: str) -> Task:
        """Add a task and return it."""
        task = Task(id=self._next_id, title=title)
        self._tasks[task.id] = task
        self._next_id += 1
        return task

    def get(self, task_id: int) -> Optional[Task]:
        """The task with this id, or None."""
        return self._tasks.get(task_id)

    def all(self) -> List[Task]:
        """Every task, newest first."""
        return sorted(self._tasks.values(), key=lambda t: t.id, reverse=True)

    def count(self) -> int:
        """How many tasks there are."""
        return len(self._tasks)


def seeded(n: int = 23) -> Store:
    """A store with n numbered tasks, for the demo server and tests."""
    store = Store()
    for i in range(1, n + 1):
        store.create(f"Task {i}")
    return store
