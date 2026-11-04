"""Tool-bound schemas preserve real argument types and local reference integrity."""

from copy import deepcopy
from pydantic import BaseModel, TypeAdapter, create_model

from crewos.application.dtos.decision_request import ToolSchema
from crewos.application.dtos.model_decision import DocumentModelDecision
from crewos.retrieval import RetrievalRequest
from crewos.third_party.llm.document_decision_schema import build_document_decision_schema


def tool(name, arguments):
    return ToolSchema(
        name=name, description="Synthetic tool", parameters=arguments.model_json_schema()
    )


def test_native_tool_branch_uses_registered_argument_shape_without_mutating_inputs():
    original = TypeAdapter(DocumentModelDecision).json_schema()
    original_copy = deepcopy(original)
    definition = tool("search_documents", RetrievalRequest)
    before = deepcopy(definition.parameters)
    schema = build_document_decision_schema(decision_schema=original, tools=(definition,))
    (variant,) = schema["$defs"]["ToolCallDecision"]["oneOf"]
    assert variant["properties"]["name"]["const"] == "search_documents"
    arguments = variant["properties"]["arguments"]
    assert arguments["properties"]["query"]["type"] == "string"
    assert arguments["additionalProperties"] is False
    assert arguments["properties"]["top_k"]["type"] == "integer"
    assert original == original_copy and definition.parameters == before


def test_nested_argument_definitions_are_namespaced_between_tools():
    integer = create_model("Common", value=(int, ...))
    text = create_model("Common", value=(str, ...))
    first = create_model("FirstInput", item=(integer, ...))
    second = create_model("SecondInput", items=(list[text], ...))
    schema = build_document_decision_schema(
        decision_schema=TypeAdapter(DocumentModelDecision).json_schema(),
        tools=(tool("first", first), tool("second", second)),
    )
    definitions = schema["$defs"]
    assert definitions["RegisteredTool0_Common"]["properties"]["value"]["type"] == "integer"
    assert definitions["RegisteredTool1_Common"]["properties"]["value"]["type"] == "string"
    a, b = definitions["ToolCallDecision"]["oneOf"]
    assert (
        a["properties"]["arguments"]["properties"]["item"]["$ref"]
        == "#/$defs/RegisteredTool0_Common"
    )
    assert (
        b["properties"]["arguments"]["properties"]["items"]["items"]["$ref"]
        == "#/$defs/RegisteredTool1_Common"
    )


def test_no_tools_removes_tool_decisions_without_removing_document_answers():
    schema = build_document_decision_schema(
        decision_schema=TypeAdapter(DocumentModelDecision).json_schema(),
        tools=(),
    )
    assert schema["oneOf"] == [{"$ref": "#/$defs/DocumentFinalAnswerDecision"}]
    assert set(schema["discriminator"]["mapping"]) == {"document_answer"}
