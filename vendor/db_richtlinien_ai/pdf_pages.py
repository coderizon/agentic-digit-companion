from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageRecord:
    id: str
    source_path: str
    source_file: str
    page: int
    text: str
    char_count: int


def list_pdfs(raw_dir: Path) -> list[Path]:
    return sorted(
        (path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name.lower(),
    )


def pdf_page_count(pdf_path: Path) -> int:
    result = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"Could not determine page count for {pdf_path}")


def extract_pdf_text(pdf_path: Path) -> list[str]:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    return result.stdout.split("\f")


def extract_page_records(pdf_path: Path, root_dir: Path) -> list[PageRecord]:
    expected_pages = pdf_page_count(pdf_path)
    pages = extract_pdf_text(pdf_path)

    if len(pages) < expected_pages:
        pages.extend([""] * (expected_pages - len(pages)))

    records: list[PageRecord] = []
    for index in range(expected_pages):
        page_number = index + 1
        text = pages[index].strip()
        relative_path = pdf_path.relative_to(root_dir)
        source_stem = pdf_path.stem.replace(" ", "_")
        records.append(
            PageRecord(
                id=f"{source_stem}:p{page_number}",
                source_path=str(relative_path),
                source_file=pdf_path.name,
                page=page_number,
                text=text,
                char_count=len(text),
            )
        )
    return records


def write_jsonl(records: list[PageRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent,
                                         prefix=output_path.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            for record in records:
                handle.write(json.dumps(record if isinstance(record, dict) else asdict(record), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]

