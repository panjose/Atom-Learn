#!/usr/bin/env python3
"""Versioned layout-preserving intermediate representation for ingested documents."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from atomlearn import iso
from core_paths import CORE_ROOT


EXTRACTOR_VERSION = "document-ir-v1"
SCHEMA_PATH = CORE_ROOT / "assets" / "schemas" / "document-ir.schema.json"


class DocumentIRError(RuntimeError):
    """A source could not be represented by the public Document IR contract."""


def _schema_errors(value: dict[str, Any]) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    ]


def require_valid(document: dict[str, Any]) -> None:
    errors = _schema_errors(document)
    if errors:
        raise DocumentIRError("Document IR is invalid:\n- " + "\n- ".join(errors))
    block_ids = [item["block_id"] for item in document["blocks"]]
    if len(block_ids) != len(set(block_ids)):
        raise DocumentIRError("Document IR contains duplicate block IDs")
    known = set(block_ids)
    for block in document["blocks"]:
        if block["parent_id"] is not None and block["parent_id"] not in known:
            raise DocumentIRError(f"Document IR block {block['block_id']} has a missing parent")
        for field in ("caption_block_id",):
            reference = block.get(field)
            if reference is not None and reference not in known:
                raise DocumentIRError(f"Document IR block {block['block_id']} has a missing {field}")
        for reference in block.get("adjacent_block_ids", []):
            if reference not in known:
                raise DocumentIRError(f"Document IR block {block['block_id']} has a missing adjacent block")
        if block["kind"] == "table" and block.get("table_structure") is None:
            raise DocumentIRError(f"Document IR table block {block['block_id']} is missing table structure")


def _page(locator: str) -> int | None:
    match = re.search(r"\bpage\s+(\d+)\b", locator, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _method(suffix: str, locator: str) -> str:
    lowered = locator.casefold()
    if "[ocr]" in lowered:
        return "ocr"
    if suffix == ".pdf":
        return "pdfplumber" if "table" in lowered else "pdf_text"
    if suffix == ".docx":
        return "docx_xml"
    if suffix in {".html", ".htm"}:
        return "html_dom"
    if suffix in {".json", ".yaml", ".yml", ".csv"}:
        return "structured"
    return "text"


def _confidence(method: str) -> float:
    return {
        "ocr": 0.7,
        "pdf_text": 0.9,
        "pdfplumber": 0.9,
        "docx_xml": 1.0,
        "html_dom": 0.95,
        "structured": 1.0,
        "text": 1.0,
        "harness_vision": 0.6,
    }[method]


def _block_id(source_id: str, source_revision: int, reading_order: int, kind: str, locator: str, text: str) -> str:
    payload = f"{source_id}|{source_revision}|{reading_order}|{kind}|{locator}|{text}"
    return "block-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in stripped[1:-1].split("|")]
        if cells and not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            rows.append(cells)
    return rows


def _table_structure(text: str) -> dict[str, Any] | None:
    """Keep a compact row/column model beside the rendered table text."""
    rows = _table_rows(text)
    if not rows:
        return None
    width = max(len(row) for row in rows)
    header_rows = 1 if len(rows) > 1 else 0
    return {
        "row_count": len(rows),
        "column_count": width,
        "header_rows": header_rows,
        "rows": [
            {
                "row_index": row_index,
                "cells": [
                    {
                        "column_index": column_index,
                        "text": cell,
                        "row_span": 1,
                        "column_span": 1,
                        "is_header": row_index <= header_rows,
                    }
                    for column_index, cell in enumerate(row, start=1)
                ],
            }
            for row_index, row in enumerate(rows, start=1)
        ],
    }


def _segments(text: str, forced_kind: str | None = None) -> list[tuple[str, str]]:
    if forced_kind:
        return [(forced_kind, text.strip())]
    lines = text.splitlines()
    result: list[tuple[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        value = "\n".join(buffer).strip()
        if value:
            kind = "list" if all(re.match(r"^\s*(?:[-*+] |\d+[.)] )", line) for line in buffer if line.strip()) else "paragraph"
            result.append((kind, value))
        buffer = []

    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("|"):
            flush()
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            result.append(("table", "\n".join(table_lines).strip()))
            continue
        buffer.append(lines[index])
        index += 1
    flush()
    return result or [("paragraph", text.strip())]


def build_document_ir(
    *,
    source_id: str,
    source_revision: int,
    title: str,
    uri: str,
    suffix: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Normalize extractor sections into stable hierarchical blocks."""
    blocks: list[dict[str, Any]] = []
    reading_order = 0
    heading_by_context: dict[tuple[str, int | None], str] = {}

    def append_block(
        kind: str,
        text: str,
        section: str,
        locator: str,
        method: str,
        *,
        parent_id: str | None,
        page: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        nonlocal reading_order
        reading_order += 1
        block_id = _block_id(source_id, source_revision, reading_order, kind, locator, text)
        block = {
                "block_id": block_id,
                "kind": kind,
                "parent_id": parent_id,
                "page": page,
                "bbox": None,
                "reading_order": reading_order,
                "section": section,
                "locator": locator,
                "extraction_method": method,
                "confidence": _confidence(method),
                "text": text,
                "caption_block_id": None,
                "crop_hash": None,
                "adjacent_block_ids": [],
                "table_structure": None,
                "review_status": "not_required",
                "numeric_status": "not_applicable",
            }
        if metadata:
            for key in (
                "caption_block_id", "crop_hash", "adjacent_block_ids", "table_structure",
                "review_status", "numeric_status", "bbox",
            ):
                if key in metadata:
                    block[key] = metadata[key]
        if kind in {"table", "figure", "image", "ocr_text"} and block["review_status"] == "not_required":
            block["review_status"] = "proposed" if method in {"ocr", "harness_vision"} else "reviewed"
        if kind in {"figure", "image", "ocr_text"} and block["numeric_status"] == "not_applicable":
            block["numeric_status"] = "proposal" if method in {"ocr", "harness_vision"} else "unreviewed"
        blocks.append(block)
        return block_id

    for section_index, item in enumerate(sections, start=1):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        locator = str(item.get("locator") or f"section {section_index}")
        section = str(item.get("section") or title)
        method = str(item.get("extraction_method") or _method(suffix.lower(), locator))
        page = item.get("page") if isinstance(item.get("page"), int) else _page(locator)
        forced_kind = item.get("kind")
        if forced_kind not in {"heading", "paragraph", "list", "table", "formula", "figure", "image", "ocr_text"}:
            lowered = locator.casefold()
            forced_kind = "ocr_text" if "[ocr]" in lowered else "formula" if "formula" in lowered else "table" if "table" in lowered else None
        body = text
        parent_heading: str | None = None
        if section != "Document":
            heading_text = re.sub(r"^#{1,6}\s+", "", section).strip()
            context = (heading_text, page)
            if heading_text and context not in heading_by_context:
                heading_by_context[context] = append_block(
                    "heading", heading_text, section, f"{locator}, heading", method,
                    parent_id=None, page=page,
                )
            parent_heading = heading_by_context.get(context)
            body = re.sub(r"^#{1,6}\s+[^\n]+\n?", "", body).strip() or text
        table_structures = iter(item.get("table_structures") or [])
        for kind, segment in _segments(body, forced_kind):
            metadata = {
                key: item[key]
                for key in (
                    "bbox", "crop_hash", "caption_block_id", "adjacent_block_ids",
                    "review_status", "numeric_status",
                )
                if key in item
            }
            if kind == "table":
                metadata["table_structure"] = item.get("table_structure") or next(table_structures, None) or _table_structure(segment)
            block_id = append_block(
                kind, segment, section, locator, method,
                parent_id=parent_heading, page=page, metadata=metadata,
            )
            if kind == "table":
                for row_index, row in enumerate(_table_rows(segment), start=1):
                    for column_index, cell in enumerate(row, start=1):
                        if cell:
                            append_block(
                                "cell", cell, section,
                                f"{locator}, row {row_index}, column {column_index}", method,
                                parent_id=block_id, page=page,
                                metadata={"review_status": blocks[-1].get("review_status", "not_required")},
                            )
    if not blocks:
        raise DocumentIRError(f"Source {source_id} produced no Document IR blocks")
    # Keep nearby prose discoverable when a layout extractor does not provide an explicit relation.
    for index, block in enumerate(blocks):
        if block["kind"] not in {"figure", "image", "table"} or block["adjacent_block_ids"]:
            continue
        adjacent: list[str] = []
        for candidate in blocks[index - 1 :: -1]:
            if candidate["section"] == block["section"] and candidate["page"] == block["page"] and candidate["kind"] not in {"heading", "cell"}:
                adjacent.append(candidate["block_id"])
                break
        for candidate in blocks[index + 1 :]:
            if candidate["section"] == block["section"] and candidate["page"] == block["page"] and candidate["kind"] not in {"heading", "cell"}:
                adjacent.append(candidate["block_id"])
                break
        block["adjacent_block_ids"] = adjacent
    canonical_text = "\n".join(block["text"] for block in blocks if block["kind"] != "cell")
    document = {
        "kind": "atomlearn.document-ir",
        "schema_version": 1,
        "source_id": source_id,
        "source_revision": source_revision,
        "title": title,
        "uri": uri,
        "extractor_version": EXTRACTOR_VERSION,
        "content_sha256": "sha256:" + hashlib.sha256(canonical_text.encode("utf-8")).hexdigest(),
        "created_at": iso(),
        "blocks": blocks,
    }
    require_valid(document)
    return document


def retrieval_sections(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return child retrieval units while preserving their IR identities."""
    require_valid(document)
    return [
        {
            "locator": block["locator"],
            "section": block["section"],
            "text": block["text"],
            "block_ids": [block["block_id"]],
        }
        for block in document["blocks"]
        if block["kind"] not in {"heading", "cell", "figure", "image"}
    ]


def marked_text(document: dict[str, Any]) -> str:
    """Create transient text that lets downstream parsers retain block ownership."""
    require_valid(document)
    return "\n".join(
        f"[document-ir-block:{block['block_id']}]\n{block['text']}"
        for block in document["blocks"]
        if block["kind"] not in {"heading", "cell", "figure", "image"}
    )
