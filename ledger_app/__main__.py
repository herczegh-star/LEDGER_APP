"""Ledger App – module entry point.

Usage:
    python -m ledger_app            # Launch desktop UI
    python -m ledger_app --cli      # Launch CLI mode
    python -m ledger_app --help     # Show this help
"""
import sys


def main() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: python -m ledger_app [--cli] [--help]")
        print("  (no args)   Launch Flet desktop UI")
        print("  --cli       Launch terminal CLI mode")
        return

    # Configure logging before anything else so all modules get handlers.
    from core.logging_setup import configure_logging
    configure_logging()

    import logging
    logger = logging.getLogger(__name__)

    from ledger_app import __version__
    logger.info("Starting Ledger App v%s", __version__)

    from core.config import get_resolved_config_path
    logger.info("Using config: %s", get_resolved_config_path())

    if "--cli" in sys.argv:
        from cli.terminal import main as _cli_main
        _cli_main()
    else:
        from ui.app_flet import run_ui
        run_ui()


if __name__ == "__main__":
    main()
