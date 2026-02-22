#!/usr/bin/env python3
"""Ledger App – legacy entry-point shim.

Prefer the module form:
    python -m ledger_app
    python -m ledger_app --cli

This file exists for backward compatibility with direct invocation.
"""
import os
import sys

# Ensure project root is on sys.path for direct invocation (not needed when
# running as an installed package or via `python -m ledger_app`).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ledger_app.__main__ import main

main()
