from crewos.domain.entities.task import Task

class TaskFactory:

    @staticmethod
    def analysis_task(payload: dict, agent) -> Task:
        return Task(
            name="Engineering Analysis",
            description=f"Analyze the following input and extract key points:\n{payload}",
            agent=agent,
            expected_output="Structured understanding of the input",
        )

    @staticmethod
    def processing_task(payload: dict, agent) -> Task:
        return Task(
            name="Engineering Processing",
            description=f"Using the research analysis provided in context, generate a direct and concise response. Do not include any preamble, meta-commentary, or phrases like 'Based on the input' or 'I'll generate'. Start your response directly with the answer.\n{payload}",
            agent=agent,
            expected_output="A direct, concise final response with no preamble or meta-commentary"
        )

