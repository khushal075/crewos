import sys
import logging
import json
from datetime import datetime
from crewos.core.config import settings

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "logger": record.name
        }

        # If structured dict passed
        if isinstance(record.msg, dict):
            log_record.update(record.msg)

        if record.exc_info:
            log_record['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_record)


def get_logger(name: str = "crewai-platform") -> logging.Logger:
    """
    Return a configured JSON logger.
    Each module should call : get_logger(__name__)
    :param name:
    :return:
    """
    logger = logging.getLogger(name)

    # prevent duplicate handlers
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, settings.LOGGING_LEVEL, 'INFO'))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger

