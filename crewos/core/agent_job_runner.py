"""Execute a registered backend-created agent job; queue transport is separate."""

from crewos.application.dtos.agent_run_job import AgentRunJob
from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.core.agent_setup import build_agent_loop
from crewos.core.tenant import get_tool_execution_context, tenant_context
from crewos.infrastructure.task_ownership import get_task_store
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider


def execute_agent_job(payload: object) -> FinalAnswerDecision:
    """Validate and verify ownership before model construction or inference.

    Publisher identity depends on broker access controls, not this DTO. Context
    uses worker-loaded server configuration, never payload permission claims.
    """
    job = AgentRunJob.model_validate(payload)
    store = get_task_store()
    store.require_owner(str(job.agent_run_id), job.tenant_id)
    context = get_tool_execution_context(tenant_id=job.tenant_id)
    with tenant_context(context.tenant_id):
        model = OllamaLLMProvider().get()
        loop = build_agent_loop(
            model=model,
            reader=CeleryTaskStatusReader(store),
            max_decisions=job.max_decisions,
        )
        return loop.run(
            messages=[DecisionMessage(role="user", content=job.message)],
            context=context,
            agent_run_id=job.agent_run_id,
        )
