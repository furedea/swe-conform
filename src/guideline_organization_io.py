"""Validated filesystem boundaries for guideline organization."""

import csv
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import guideline_organization_model

_GUIDELINE_COLUMNS = frozenset({"repository", "revision", "file", "github_url"})
_REPOSITORY_COLUMNS = frozenset({"repository"})


def selected_repositories(path: Path) -> frozenset[str]:
    """Load a non-empty, unique repository list."""
    rows = csv_rows(path, required_columns=_REPOSITORY_COLUMNS)
    repositories: set[str] = set()
    for row in rows:
        repository = row["repository"].strip()
        if not repository:
            msg = f"repository must not be empty in {path}"
            raise ValueError(msg)
        if repository in repositories:
            msg = f"duplicate repository in {path}: {repository}"
            raise ValueError(msg)
        repositories.add(repository)
    if not repositories:
        msg = f"repository list is empty: {path}"
        raise ValueError(msg)
    return frozenset(repositories)


def source_documents(
    path: Path,
    *,
    selected: frozenset[str],
    source_root: Path,
) -> tuple[guideline_organization_model.SourceDocument, ...]:
    """Resolve every accepted guideline file for selected repositories."""
    rows = csv_rows(path, required_columns=_GUIDELINE_COLUMNS)
    selected_rows = [row for row in rows if row["repository"].strip() in selected]
    present = {row["repository"].strip() for row in selected_rows}
    missing = selected.difference(present)
    if missing:
        msg = f"selected repositories have no accepted guideline files: {sorted(missing)!r}"
        raise ValueError(msg)
    ordered = sorted(
        selected_rows,
        key=lambda row: (
            row["repository"].strip().casefold(),
            row["revision"].strip(),
            row["file"].strip(),
        ),
    )
    identities: set[tuple[str, str, str]] = set()
    documents: list[guideline_organization_model.SourceDocument] = []
    root = source_root.resolve()
    for index, row in enumerate(ordered, start=1):
        repository = row["repository"].strip()
        revision = row["revision"].strip()
        file = row["file"].strip()
        identity = repository, revision, file
        if identity in identities:
            msg = f"duplicate accepted guideline file: {identity!r}"
            raise ValueError(msg)
        identities.add(identity)
        local_path = _source_path(root, file)
        documents.append(
            guideline_organization_model.SourceDocument(
                source_id=f"source-{index:04d}",
                repository=repository,
                revision=revision,
                file=file,
                github_url=row["github_url"].strip(),
                local_path=local_path,
                content=local_path.read_text(encoding="utf-8"),
            ),
        )
    return tuple(documents)


def manifest_sources(path: Path) -> dict[str, guideline_organization_model.SourceDocument]:
    """Reload source documents and verify their preparation fingerprints."""
    required = frozenset({"source_id", "repository", "revision", "file", "github_url", "local_path", "sha256"})
    rows = csv_rows(path, required_columns=required)
    sources: dict[str, guideline_organization_model.SourceDocument] = {}
    for row in rows:
        source_id = row["source_id"].strip()
        if not source_id or source_id in sources:
            msg = f"invalid or duplicate source_id in {path}: {source_id!r}"
            raise ValueError(msg)
        local_path = Path(row["local_path"])
        if not local_path.is_file():
            msg = f"manifest source file does not exist: {local_path}"
            raise FileNotFoundError(msg)
        source = guideline_organization_model.SourceDocument(
            source_id=source_id,
            repository=row["repository"].strip(),
            revision=row["revision"].strip(),
            file=row["file"].strip(),
            github_url=row["github_url"].strip(),
            local_path=local_path,
            content=local_path.read_text(encoding="utf-8"),
        )
        if source.sha256 != row["sha256"].strip():
            msg = f"source content changed after extraction preparation: {local_path}"
            raise ValueError(msg)
        sources[source_id] = source
    if not sources:
        msg = f"source manifest is empty: {path}"
        raise ValueError(msg)
    return sources


def write_source_manifest(
    path: Path,
    documents: tuple[guideline_organization_model.SourceDocument, ...],
) -> None:
    """Persist stable source identities and content fingerprints."""
    fieldnames = (
        "source_id",
        "repository",
        "revision",
        "file",
        "github_url",
        "local_path",
        "sha256",
        "size_bytes",
        "line_count",
    )
    rows = tuple(
        {
            "source_id": document.source_id,
            "repository": document.repository,
            "revision": document.revision,
            "file": document.file,
            "github_url": document.github_url,
            "local_path": str(document.local_path),
            "sha256": document.sha256,
            "size_bytes": len(document.content.encode()),
            "line_count": len(document.content.split("\n")),
        }
        for document in documents
    )
    write_csv(path, rows, fieldnames=fieldnames)


def csv_rows(path: Path, *, required_columns: frozenset[str]) -> tuple[dict[str, str], ...]:
    """Read a potentially large CSV after validating its header."""
    csv.field_size_limit(sys.maxsize)
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        missing = required_columns.difference(reader.fieldnames or ())
        if missing:
            msg = f"{path} is missing required columns: {', '.join(sorted(missing))}"
            raise ValueError(msg)
        return tuple(dict(row) for row in reader)


def jsonl_objects(path: Path) -> tuple[Mapping[str, object], ...]:
    """Read JSON objects from a JSONL artifact."""
    if not path.is_file():
        msg = f"JSONL input does not exist: {path}"
        raise FileNotFoundError(msg)
    return tuple(
        cast(Mapping[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def json_object(path: Path) -> Mapping[str, object]:
    """Read one JSON object."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"{path} must contain a JSON object"
        raise ValueError(msg)
    return cast(dict[str, object], document)


def write_jsonl(path: Path, documents: tuple[Mapping[str, object], ...]) -> None:
    """Write deterministic JSONL."""
    value = "".join(f"{json.dumps(document, ensure_ascii=True, sort_keys=True)}\n" for document in documents)
    path.write_text(value, encoding="utf-8")


def write_csv(
    path: Path,
    rows: tuple[Mapping[str, object], ...],
    *,
    fieldnames: tuple[str, ...] | None = None,
) -> None:
    """Write CSV with an explicit header when rows may be empty."""
    columns = fieldnames or (tuple(rows[0]) if rows else ())
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, document: Mapping[str, object]) -> None:
    """Write deterministic indented JSON."""
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    path.write_text(value, encoding="utf-8")


def require_new_output_directory(path: Path) -> None:
    """Refuse to overwrite an existing experiment."""
    if path.exists():
        msg = f"organization output directory already exists: {path}"
        raise FileExistsError(msg)


def numbered_content(content: str) -> str:
    """Attach stable one-based line identifiers to source content."""
    return "\n".join(f"L{line_number}\t{line}" for line_number, line in enumerate(content.split("\n"), start=1))


def file_sha256(path: Path) -> str:
    """Return a file fingerprint."""
    return sha256(path.read_bytes())


def sha256(content: bytes) -> str:
    """Return a byte-string fingerprint."""
    return hashlib.sha256(content).hexdigest()


def _source_path(source_root: Path, file: str) -> Path:
    relative = Path(file)
    if relative.is_absolute() or ".." in relative.parts:
        msg = f"guideline file must be relative to source root: {file}"
        raise ValueError(msg)
    path = (source_root / relative).resolve()
    if not path.is_relative_to(source_root):
        msg = f"guideline file escapes source root: {file}"
        raise ValueError(msg)
    if not path.is_file():
        msg = f"accepted guideline file does not exist: {path}"
        raise FileNotFoundError(msg)
    return path
