"""Exercise tool registration, policy, tenant scope, and model-visible observations."""

import json
from unittest.mock import Mock
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from pydantic import ValidationError
import pytest

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.use_cases.search_documents import SearchDocumentsHandler
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.core.agent_setup import build_agent_loop
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.ingestion import DocumentInput, StoredDocumentVersion, build_sqlite_document_store
from crewos.retrieval import RetrievalCapacityExceeded, RetrievalRequest, RetrievalResult


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = build_sqlite_document_store(tmp_path / "docs.sqlite3")
    monkeypatch.setattr("crewos.core.retrieval_setup.get_document_store", lambda: store)
    return store


def save(store, text, tenant="a"):
    return store.create_version(
        StoredDocumentVersion(
            document_id=uuid4(),
            tenant_id=tenant,
            version=1,
            document=DocumentInput(title="Runbook", text=text, source_ref="runbook:webhooks"),
            chunking=build_character_chunking().chunk(
                ChunkingRequest(text=text, options=ChunkingOptions(chunk_size=1000, overlap=0))
            ),
        )
    )


def context(permissions=("document.search", "document.read")):
    return ToolExecutionContext(tenant_id="a", permissions=frozenset(permissions))


def test_default_agent_exposes_search_and_receives_exact_passages(store):
    own = save(store, "Retry webhook timeout with backoff.")
    save(store, "webhook timeout private customer secrets", tenant="b")
    model = Mock(spec=BaseChatModel)
    model.invoke.side_effect = [
        AIMessage(
            content=json.dumps(
                {
                    "type": "tool_call",
                    "name": "search_documents",
                    "arguments": {"query": "webhook timeout", "top_k": 1},
                }
            )
        ),
        AIMessage(content='{"type":"final_answer","answer":"Search completed."}'),
    ]
    task_reader = Mock()
    loop = build_agent_loop(model=model, reader=task_reader)
    answer = loop.run(
        messages=[DecisionMessage(role="user", content="Find timeout guidance")], context=context()
    )
    assert answer.answer == "Search completed."
    history = json.loads(model.invoke.call_args.args[0][1].content)["messages"]
    observation = json.loads(history[-1]["content"])
    assert history[-1]["role"] == "tool"
    assert observation["name"] == "search_documents"
    assert observation["result"]["strategy"] == "bm25_v1"
    (hit,) = observation["result"]["passages"]
    assert hit["document_id"] == str(own.document_id)
    assert hit["document_version"] == 1
    assert hit["chunk"]["text"] == own.document.text
    assert hit["source_ref"] == "runbook:webhooks"
    assert "private customer secrets" not in json.dumps(history)
    task_reader.read.assert_not_called()


@pytest.mark.parametrize("permissions", [(), ("document.search",), ("document.read",)])
def test_denied_search_does_not_open_database(monkeypatch, permissions):
    database = Mock(side_effect=AssertionError("Must not open before authorization"))
    monkeypatch.setattr("crewos.core.retrieval_setup.get_document_store", database)
    dispatcher = build_tool_dispatcher(reader=Mock())
    database.assert_not_called()
    with pytest.raises(PolicyDeniedError):
        dispatcher.dispatch(
            name="search_documents", arguments={"query": "timeout"}, context=context(permissions)
        )
    database.assert_not_called()


def test_tool_schema_excludes_authority_and_rejects_forged_claims(store):
    dispatcher = build_tool_dispatcher(reader=Mock())
    schema = next(s for s in dispatcher.tool_schemas() if s.name == "search_documents")
    assert set(schema.parameters["properties"]) == {"query", "top_k"}
    assert dispatcher.replay_safe("search_documents") is True
    with pytest.raises(ValidationError):
        dispatcher.dispatch(
            name="search_documents",
            arguments={"query": "timeout", "tenant_id": "b"},
            context=context(),
        )


def test_empty_matches_and_storage_failures_are_distinct(store, monkeypatch):
    dispatcher = build_tool_dispatcher(reader=Mock())
    result = dispatcher.dispatch(
        name="search_documents", arguments={"query": "timeout"}, context=context()
    )
    assert result.passages == ()
    monkeypatch.setattr(
        "crewos.core.retrieval_setup.get_document_store",
        Mock(side_effect=OSError("private storage failure")),
    )
    with pytest.raises(OSError):
        dispatcher.dispatch(
            name="search_documents", arguments={"query": "timeout"}, context=context()
        )


def test_agent_search_rejects_large_passage_output(store):
    save(store, "timeout " * 1100)
    with pytest.raises(RetrievalCapacityExceeded):
        build_tool_dispatcher(reader=Mock()).dispatch(
            name="search_documents",
            arguments={"query": "timeout", "top_k": 20},
            context=context(),
        )


def test_handler_includes_json_encoding_in_observation_limit():
    retrieval = Mock()
    result = Mock(spec=RetrievalResult)
    result.model_dump_json.return_value = "é" * 16001
    retrieval.search.return_value = result
    with pytest.raises(RetrievalCapacityExceeded, match="serialized"):
        SearchDocumentsHandler(retrieval=retrieval).execute(
            arguments=RetrievalRequest(query="timeout"), context=context()
        )
