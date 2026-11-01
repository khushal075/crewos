"""Public ingestion behavior, compatibility, and module dependency boundaries."""

import ast
from importlib import import_module
from pathlib import Path

import pytest

from crewos.ingestion import (
    DocumentInput,
    DocumentParseError,
    DocumentPreparationService,
    build_plain_text_preparation,
)


def test_public_setup_prepares_a_document_with_configured_source_limit():
    service = build_plain_text_preparation(max_source_bytes=5)
    document = service.prepare(content=b"Logs", title="Incident", source_ref="incident:1")
    assert isinstance(document, DocumentInput)
    assert document.text == "Logs"
    assert document.source_ref == "incident:1"
    with pytest.raises(DocumentParseError):
        service.prepare(content=b"Too large", title="Incident")


def test_public_service_accepts_an_independent_parser_strategy():
    class FixtureParser:
        def parse(self, content: bytes) -> str:
            return "Extracted incident text"

    document = DocumentPreparationService(FixtureParser()).prepare(
        content=b"fixture", title="Incident"
    )
    assert document.text == "Extracted incident text"


COMPATIBILITY_MODULES = {
    "crewos.application.dtos.document_input": ("DocumentInput",),
    "crewos.application.dtos.text_chunk": ("TextChunk",),
    "crewos.application.interfaces.document_parser": (
        "DocumentParseError",
        "DocumentParserInterface",
    ),
    "crewos.application.services.document_preparation": ("DocumentPreparationService",),
    "crewos.infrastructure.plain_text_document_parser": ("PlainTextDocumentParser",),
}


@pytest.mark.parametrize("path,names", COMPATIBILITY_MODULES.items())
def test_legacy_imports_reexport_the_same_objects(path, names):
    canonical = import_module(
        "crewos.ingestion.adapters.plain_text"
        if "PlainTextDocumentParser" in names
        else "crewos.ingestion"
    )
    legacy = import_module(path)
    for name in names:
        assert getattr(legacy, name) is getattr(canonical, name)


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level, tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0, ()


def test_other_modules_use_the_public_ingestion_boundary():
    root = Path(__file__).resolve().parents[1] / "crewos"
    violations = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[0] == "ingestion":
            continue
        module_name = "crewos." + ".".join(relative.with_suffix("").parts)
        if module_name in COMPATIBILITY_MODULES:
            continue
        for module, level, names in imports(path):
            # Reject relative imports outside ingestion rather than silently
            # missing a bypass of the public boundary.
            if level:
                violations.append(f"{relative}: use absolute imports for boundary checks")
            elif module.startswith("crewos.ingestion."):
                violations.append(f"{relative}: private ingestion import {module}")
            elif module == "crewos.ingestion" and any(
                name
                in {
                    "adapters",
                    "bootstrap",
                    "contracts",
                    "document_parser",
                    "document_store",
                    "document_ingestion_facade",
                    "document_processing_coordinator",
                    "service",
                }
                for name in names
            ):
                violations.append(f"{relative}: imports private ingestion module")
    assert not violations, "\n".join(violations)


def test_ingestion_core_does_not_import_adapters_or_other_platform_modules():
    root = Path(__file__).resolve().parents[1] / "crewos" / "ingestion"
    allowed = {
        "crewos.ingestion.contracts",
        "crewos.ingestion.document_parser",
        "crewos.ingestion.document_store",
        "crewos.chunking",
    }
    violations = []
    for filename in ("contracts.py", "document_parser.py", "document_store.py", "service.py"):
        for module, level, names in imports(root / filename):
            if level or (module.startswith("crewos") and module not in allowed):
                violations.append(f"{filename}: unexpected dependency {module}")
    assert not violations, "\n".join(violations)


def test_processing_coordinator_has_no_policy_or_transport_dependencies():
    root = Path(__file__).resolve().parents[1] / "crewos" / "ingestion"
    allowed = {
        "crewos.ingestion.contracts",
        "crewos.ingestion.document_store",
        "crewos.ingestion.service",
        "crewos.chunking",
    }
    for module, level, names in imports(root / "document_processing_coordinator.py"):
        assert not level
        assert not module.startswith("crewos") or module in allowed


def test_ingestion_facade_has_no_parser_chunker_or_database_dependencies():
    root = Path(__file__).resolve().parents[1] / "crewos" / "ingestion"
    allowed = {
        "crewos.ingestion.contracts",
        "crewos.ingestion.document_processing_coordinator",
        "crewos.application.dtos.tool_execution_context",
        "crewos.application.services.policy_executor",
    }
    for module, level, names in imports(root / "document_ingestion_facade.py"):
        assert not level
        assert not module.startswith("crewos") or module in allowed


def test_processing_coordinator_is_not_exported_as_a_public_authorized_entry_point():
    import crewos.ingestion as ingestion

    assert "DocumentIngestionFacade" in ingestion.__all__
    assert "DocumentProcessingCoordinator" not in ingestion.__all__
    assert not hasattr(ingestion, "DocumentProcessingCoordinator")
