"""Tests for ConfigFxProvider – reads EUR/CZK from ledger.ini."""
import os
import tempfile
from decimal import Decimal

from ledger_engine.fx_provider import ConfigFxProvider


def test_reads_rate_from_ini():
    """ConfigFxProvider returns Decimal from [fx] eur_to_czk."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ini", delete=False, encoding="utf-8"
    ) as f:
        f.write("[fx]\neur_to_czk = 24.85\n")
        path = f.name
    try:
        provider = ConfigFxProvider(config_path=path)
        assert provider.get_eur_to_czk("2026-01-01") == Decimal("24.85")
        assert provider.get_eur_to_czk("2025-06-15") == Decimal("24.85")
    finally:
        os.unlink(path)


def test_fallback_default_when_config_missing():
    """ConfigFxProvider falls back to Decimal('25') if config file absent."""
    provider = ConfigFxProvider(config_path="/nonexistent/path.ini")
    assert provider.get_eur_to_czk("2026-01-01") == Decimal("25")
