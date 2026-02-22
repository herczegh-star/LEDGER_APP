#!/usr/bin/env python3
"""Ledger App – entry point.

Default: launches the Flet desktop UI.
Pass --cli to use the terminal UI instead.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "--cli" in sys.argv:
    from cli.terminal import main
    main()
else:
    from ui.app_flet import run_ui
    run_ui()
