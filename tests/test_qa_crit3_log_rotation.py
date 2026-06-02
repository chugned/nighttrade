"""QA-CRIT-3 (nighttrade variant) — log files use RotatingFileHandler."""

import logging

from nighttrade.runtime import add_file_logging


def test_add_file_logging_attaches_rotating_handler(tmp_path):
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler):
            root.removeHandler(h)
    log_path = tmp_path / "nighttrade.log"
    add_file_logging(str(log_path), max_bytes=1024, backup_count=3)
    matched = [
        h
        for h in root.handlers
        if isinstance(h, RotatingFileHandler) and h.baseFilename == str(log_path.resolve())
    ]
    assert matched, "expected a RotatingFileHandler attached"
    rh = matched[0]
    assert rh.maxBytes == 1024
    assert rh.backupCount == 3
    root.removeHandler(rh)
    rh.close()
