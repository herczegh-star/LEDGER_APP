#!/usr/bin/env python3
"""Ledger App – entry point.

Default: launches the Flet desktop UI.
Pass --cli to use the terminal UI instead.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "--cli" in sys.argv:
    from ui.terminal import main
    main()
else:
    import flet as ft
    from ui.app_flet import main
    ft.run(main)
