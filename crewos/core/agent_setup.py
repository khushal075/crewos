"""Assemble an agent loop without performing inference or task reads."""

from langchain_core.language_models.chat_models import BaseChatModel

from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.use_cases.agent_loop import AgentLoop
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.infrastructure.logging import get_logger
from crewos.third_party.llm.json_decision_adapter import JsonDecisionAdapter


logger = get_logger("crewos.agent")


def build_agent_loop(
    *,
    model: BaseChatModel,
    reader: TaskStatusReaderInterface,
    max_decisions: int = 5,
) -> AgentLoop:
    """Caller supplies configured dependencies and per-run inputs separately.

    Model configuration and authentication are not performed here. The loop
    derives model-visible tool schemas from its dispatcher registrations. Construction does not invoke the model or the reader.
    """
    return AgentLoop(
        provider=JsonDecisionAdapter(model),
        dispatcher=build_tool_dispatcher(reader=reader),
        max_decisions=max_decisions,
        logger=logger,
    )
