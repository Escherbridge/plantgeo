"""Fatal execution boundaries shared by handlers and the worker; see jobs/AGENTS.md."""


class JobExecutionAbortError(RuntimeError):
    """Stop the owning service without retrying or releasing unconfirmed external work."""
