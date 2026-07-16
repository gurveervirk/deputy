import logging
import os
import tempfile
from pathlib import Path
from deputy.logger import init_logging, get_logger, _reset_logging

def setup_method():
    _reset_logging()

def test_init_logging_creates_log_file():
    _reset_logging()
    with tempfile.TemporaryDirectory() as tmp:
        log_file = os.path.join(tmp, "deputy.log")
        init_logging(level="DEBUG", log_file=log_file)
        assert os.path.exists(log_file)

def test_init_logging_logs_at_correct_level():
    _reset_logging()
    with tempfile.TemporaryDirectory() as tmp:
        log_file = os.path.join(tmp, "deputy.log")
        init_logging(level="WARNING", log_file=log_file)

        logger = get_logger("test")
        logger.info("should not appear")
        logger.warning("should appear")

        content = Path(log_file).read_text()
        assert "should appear" in content
        assert "should not appear" not in content

def test_init_logging_idempotent():
    _reset_logging()
    with tempfile.TemporaryDirectory() as tmp:
        log_file = os.path.join(tmp, "deputy.log")
        init_logging(level="DEBUG", log_file=log_file)
        init_logging(level="ERROR", log_file=log_file)

        logger = get_logger("test")
        logger.debug("still visible because first call set DEBUG")

        content = Path(log_file).read_text()
        assert "still visible" in content

def test_get_logger_returns_deputy_child():
    _reset_logging()
    logger = get_logger("foo.bar")
    assert logger.name == "deputy.foo.bar"
    assert isinstance(logger, logging.Logger)

def test_init_logging_creates_parent_dir():
    _reset_logging()
    with tempfile.TemporaryDirectory() as tmp:
        log_file = os.path.join(tmp, "subdir", "deep", "deputy.log")
        init_logging(level="DEBUG", log_file=log_file)
        assert os.path.exists(log_file)
