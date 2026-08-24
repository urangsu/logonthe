from enum import Enum

class TaskStatus(Enum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
