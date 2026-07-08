"""Core data structures: Listing (a job) and the structured resume profile."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict


@dataclass
class Listing:
    """A normalized job posting from any source."""

    title: str
    company: str
    description: str
    url: str = ""
    location: str = ""
    remote: bool = False
    salary: str = ""
    source: str = ""
    posted_date: str = ""
    fetched_at: str = ""

    @property
    def id(self) -> str:
        """Stable id for dedup: canonical URL if present, else a hash of
        company+title+location+description.
        """
        basis = self.url.strip().lower() if self.url else (
            f"{self.company}|{self.title}|{self.location}|{self.description}".lower()
        )
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d


@dataclass
class ExperienceItem:
    company: str = ""
    title: str = ""
    dates: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class ResumeProfile:
    """Structured resume — the canonical object the whole pipeline edits/reads."""

    name: str = ""
    contact: dict = field(default_factory=dict)
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    experience: list[ExperienceItem] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    raw_text: str = ""
    # Targeting captured during `setup`
    targets: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ResumeProfile":
        exp = [
            ExperienceItem(**{k: v for k, v in e.items()
                              if k in ExperienceItem.__dataclass_fields__})
            for e in d.get("experience", [])
        ]
        d = {**d, "experience": exp}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
