"""Render an approved tailored resume to .docx (the format you upload/edit)."""
from __future__ import annotations

import re
from pathlib import Path

from . import config


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).strip("_")[:60]


def out_dir(company: str, title: str) -> Path:
    d = config.TAILORED_DIR / f"{_safe(company)}_{_safe(title)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_docx(text: str, path: Path) -> None:
    import docx

    document = docx.Document()
    for block in text.split("\n"):
        document.add_paragraph(block)
    document.save(str(path))


def render(company: str, title: str, resume_text: str) -> dict:
    """Write the resume as docx. Returns the written path."""
    d = out_dir(company, title)
    write_docx(resume_text, d / "resume.docx")
    return {"resume_docx": str(d / "resume.docx")}
