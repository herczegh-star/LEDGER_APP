"""Shared pytest fixtures for all tests.

Auto-patches the module-level CNB FX provider so tests never hit HTTP.
Any test that needs a specific rate can override _cnb via monkeypatch.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import core.services.ui_facade as _facade


class _FixedRateProvider:
    """Test double: always returns Decimal('25') for any date."""

    def get_eur_czk(self, d: date):
        return Decimal("25"), d


@pytest.fixture(autouse=True)
def _patch_cnb(monkeypatch):
    """Replace the CNB HTTP provider with a fixed-rate stub in every test."""
    monkeypatch.setattr(_facade, "_cnb", _FixedRateProvider())
