from __future__ import annotations

from dataclasses import dataclass, field

LECTURE, PRACTICE, LAB = "lecture", "practice", "lab"
GYM = "GYM"


@dataclass
class Course:
    program: str
    name: str
    trimester: int
    credits: int
    hours: dict[str, int]  # weekly academic hours per kind (lecture/practice/lab)
    online: bool
    lecture_first: bool
    venue: str = ""  # "" = regular rooms, "GYM" = sports hall


@dataclass
class Group:
    code: str
    program: str
    year: int
    headcount: int


@dataclass
class Room:
    code: str
    capacity: int
    rtype: str  # L lecture hall, P practice room, K computer lab
    floor: int


@dataclass
class Session:
    """One weekly class block that must get a start slot (and a room if offline)."""

    id: str
    program: str
    year: int
    course: str
    kind: str
    groups: list[str]
    size: int
    duration: int  # 1 or 2 consecutive academic hours
    online: bool
    venue: str
    component: str  # same component = same course/kind/groups; blocks go on different days
    lecture_first: bool

    @property
    def unit(self) -> str:
        return f"{self.program}-{self.year}"


@dataclass
class Dataset:
    courses: list[Course]
    groups: list[Group]
    rooms: list[Room]
    slot_labels: dict[int, str]  # hour index -> "08:00-08:50"
    issues: list[dict] = field(default_factory=list)
