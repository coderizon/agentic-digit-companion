"""Erstellt die persönliche Excel-Liste aus dem gespeicherten Inhalt einer WebUI-Notiz."""
import hashlib
import json
import re
import textwrap
from copy import copy
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook


FILENAME = "Prüfvermerk-Kleineschholz.xlsx"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEMPLATE = Path(__file__).with_name("pruefvermerk.xlsx")


def paragraphs(node):
    children = node.get("content") or []
    if node.get("type") in ("paragraph", "heading", "codeBlock"):
        pieces = []
        source = False
        for child in children:
            pieces.append("\n" if child.get("type") == "hardBreak" else child.get("text", ""))
            source |= any(mark.get("type") == "link" for mark in child.get("marks", []))
        yield "".join(pieces).strip(), source
    else:
        for child in children:
            yield from paragraphs(child)


def note_rows(note):
    content = (note.get("data") or {}).get("content") or {}
    document = content.get("json")
    if isinstance(document, dict) and document.get("type") == "doc":
        blocks = paragraphs(document)
    else:
        # Older notes may only have their Markdown representation.
        blocks = []
        for line in (content.get("md") or "").splitlines():
            link = re.fullmatch(r"\[([^\]]+)\]\([^)]+\)", line.strip())
            blocks.append((link[1] if link else line.strip(), bool(link)))
    rows = []
    for text, source in blocks:
        if not text:
            continue
        field, colon, value = text.partition(":")
        if colon and len(field) < 60 and value.strip():
            rows.append((field.strip(), value.strip()))
        else:
            field = "Quelle" if source else ("Einordnung" if not rows else "Notiz")
            rows.append((field, text))
    if not rows:
        raise ValueError("Der gespeicherte Prüfvermerk enthält keinen Text.")
    return rows


def workbook_bytes(title, rows):
    book = load_workbook(TEMPLATE)
    sheet = book["Prüfvermerk"]
    sheet["A2"] = title
    sheet["A2"].data_type = "s"
    formats = [copy(sheet[address]._style) for address in ("A5", "B5")]
    for index, pair in enumerate(rows, start=5):
        lines = 1
        for column, value in enumerate(pair, start=1):
            if len(value) > 32767:
                raise ValueError("Ein Eintrag ist zu lang für eine Excel-Zelle.")
            cell = sheet.cell(index, column, value)
            cell._style = formats[column - 1]
            # Keep user-edited text literal, including a leading '='.
            cell.data_type = "s"
            width = 21 if column == 1 else 75
            lines = max(lines, sum(max(1, len(textwrap.wrap(line, width))) for line in value.splitlines()))
        sheet.row_dimensions[index].height = min(409, max(27, 9 + 17 * lines))
    sheet.auto_filter.ref = f"A4:B{sheet.max_row}"
    sheet.sheet_view.showGridLines = False
    sheet.print_options.horizontalCentered = True
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "4:4"
    book.properties.creator = "DIGIT Companion"
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def export_file(client, note):
    """Use only the caller's file library. The caller holds the per-note write lock."""
    rows = note_rows(note)
    revision = json.dumps(["xlsx-v1", note["id"], note["title"], rows], ensure_ascii=False)
    stamp = hashlib.sha256(revision.encode()).hexdigest()
    params = {"filename": FILENAME, "limit": 50, "skip": 0}
    while True:
        response = client.get("files/user/search", params=params)
        response.raise_for_status()
        items = response.json()
        for item in items:
            metadata = (item.get("meta") or {}).get("data") or {}
            if item["user_id"] == note["user_id"] and metadata.get("digit_export") == stamp:
                return item["id"]
        if len(items) < params["limit"]:
            break
        params["skip"] += len(items)
    # Unindexed personal upload. Access to the file is checked again at download.
    response = client.post("files/", params={"process": "false"},
                           data={"metadata": json.dumps({"digit_export": stamp})},
                           files={"file": (FILENAME, workbook_bytes(note["title"], rows), MIME)})
    response.raise_for_status()
    return response.json()["id"]
