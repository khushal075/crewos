"""Provider-neutral decision failure classification."""


class DecisionResponseError(RuntimeError):
    pass


class DecisionRefusedError(DecisionResponseError):
    pass
