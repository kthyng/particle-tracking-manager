
import datetime
import logging
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field


# Enum for "log_level"
class LogLevelEnum(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class LoggerMethods(BaseModel):
    """Methods for loggers."""

    log_level: LogLevelEnum = Field(LogLevelEnum.INFO, description="Log verbosity", ptm_level=3)

    def close_loggers(self, logger):
        """Close and remove all handlers from the logger."""
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    def setup_logger(self, logfile_name: str) -> logging.Logger:
        """Setup logger."""

        logger = logging.getLogger()

        if logger.handlers:
            self.close_loggers(logger)
            
        logger.setLevel(getattr(logging, self.log_level))

        # Add handlers from the main logger to the OpenDrift logger if not already added
        
        # Create file handler to save log to file
        # logfile_name = pathlib.Path(output_file).stem + ".log"
        file_handler = logging.FileHandler(logfile_name)
        fmt = "%(asctime)s %(levelname)-7s %(name)s.%(module)s.%(funcName)s:%(lineno)d: %(message)s"
        datefmt = '%Y-%m-%d %H:%M:%S'
        formatter = logging.Formatter(fmt, datefmt)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Create stream handler
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        
        logger.info("Particle tracking manager simulation.")
        logger.info(f"Log filename: {logfile_name}")
        return logger

    def merge_with_opendrift_log(self, logger: logging.Logger) -> None:
        """Merge the OpenDrift logger with the main logger."""
        for logger_name in logging.root.manager.loggerDict:
            if logger_name.startswith("opendrift"):
                od_logger = logging.getLogger(logger_name)
                if od_logger.handlers:
                    self.close_loggers(od_logger)

                # Add handlers from the main logger to the OpenDrift logger
                for handler in logger.handlers:
                    od_logger.addHandler(handler)
                od_logger.setLevel(logger.level)
                od_logger.propagate = False
