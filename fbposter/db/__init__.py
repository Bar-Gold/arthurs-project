"""SQLite storage.

The database is the source of truth for queue state, not just a place to keep
things between runs. Each group's outcome is committed as it happens, so a
crash or restart resumes a batch rather than re-posting to groups it already
hit -- a duplicate post is the worst failure this project has.
"""

from .connection import Database
from .models import Group, Schedule, Task, TaskTarget, Template

__all__ = ["Database", "Group", "Schedule", "Task", "TaskTarget", "Template"]
