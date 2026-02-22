"""Central logging configuration for Ledger App.

Call configure_logging() exactly once at startup (ledger_app/__main__.py)
before importing any other module that uses logging.
"""
import logging
import os
from logging.handlers import RotatingFileHandler


def configure_logging(log_dir: str = "logs", level: int = logging.INFO) -> None:
    """Configure root logger: stderr console + rotating file handler.

    Idempotent — safe to call multiple times (skips if handlers already set).

    Log file: <log_dir>/ledgerapp.log  (1 MB × 5 backups, UTF-8)
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (stderr)
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler
    os.makedirs(log_dir, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(log_dir, "ledgerapp.log"),
        maxBytes=1 * 1024 * 1024,  # 1 MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)
