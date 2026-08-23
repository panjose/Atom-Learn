# Third-Party Notices

AtomLearn is licensed under Apache-2.0. It depends on separately licensed
third-party software. Those components are not relicensed by AtomLearn.

The following table covers direct production, optional, release-manager, and
development dependencies declared by this repository. Exact resolved versions
and transitive dependencies vary by platform and installation profile; the
license metadata shipped by each installed distribution remains authoritative.

| Component | Purpose | Declared license |
| --- | --- | --- |
| PyYAML | YAML parsing | MIT |
| pypdf | Base PDF text extraction | BSD-3-Clause |
| python-docx | DOCX extraction | MIT |
| pdfplumber | PDF tables and layout | MIT |
| jsonschema | Contract validation | MIT |
| platformdirs | Platform state paths | MIT |
| pypdfium2 / PDFium | Optional PDF rendering for OCR | Apache-2.0 OR BSD-3-Clause, plus bundled PDFium third-party notices |
| pytesseract | Optional Tesseract adapter | Apache-2.0 |
| Pillow | Optional image handling | HPND |
| Tesseract OCR | Optional native OCR engine | Apache-2.0 |
| USearch | Optional HNSW vector index | Apache-2.0 |
| Sentence Transformers | Optional local semantic retrieval | Apache-2.0 |
| cryptography | Release signatures | Apache-2.0 OR BSD-3-Clause |
| tomli | Python 3.10 TOML compatibility | MIT |
| pytest | Development tests | MIT |
| Hypothesis | Property-based development tests | MPL-2.0 |
| Contributor Covenant 3.0 | Community code of conduct source text | CC-BY-SA-4.0 |

The signed runtime builder may redistribute third-party wheels. Their embedded
license files and notices must remain in the wheelhouse and resulting runtime.
In particular, pypdfium2 binary distributions include PDFium and dependency
notices that must be preserved when those binaries are redistributed.

Project links and current license declarations are available from the package
indexes and upstream repositories named in `pyproject.toml` and
`manager/pyproject.toml`. Please report a missing or incorrect notice through
the repository's security or issue-reporting process.

`CODE_OF_CONDUCT.md` is adapted from Contributor Covenant 3.0. Its attribution
and CC-BY-SA-4.0 source license are retained in that file.
