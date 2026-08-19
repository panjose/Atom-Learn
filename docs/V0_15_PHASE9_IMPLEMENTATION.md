# AtomLearn v0.15 Phase 9 Implementation

Phase 9 closes the figure/table evidence boundary between extraction, retrieval, and research synthesis.

## Delivered

- Document IR tables now retain a deterministic row/column model with header flags and span fields alongside the rendered text and cell blocks.
- Figure, image, table, and OCR blocks expose optional `bbox`, `crop_hash`, `caption_block_id`, adjacent block IDs, `review_status`, and `numeric_status` metadata.
- OCR and harness-vision numeric observations default to `proposal`; they cannot silently become confirmed quantitative evidence.
- Research evidence locators validate current source revisions, referenced block kinds, caption/crop identity, and figure/table review status.
- Quantitative claims without a current Document IR block locator, or with rejected/proposed numeric evidence, fail the completion gate with an actionable review/abstention message.

## Verification

- `tests/test_document_ir.py` covers table structure, proposal metadata, and invalid caption references.
- `tests/test_research.py` covers stale/unreviewed quantitative evidence rejection.
- Existing RAG and research suites remain green; the new fields are optional for legacy locators and old source revisions remain readable.

## Boundary

This phase does not claim automatic chart understanding or human verification. Vision/OCR extraction remains a proposal-producing adapter. A harness or reviewer must provide the crop, locator, and confirmation before quantitative claims can enter a completed paper or cross-paper synthesis.
