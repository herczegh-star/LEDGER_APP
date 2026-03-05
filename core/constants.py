"""Core constants — single source of truth for domain enumerations."""

TRADE_TYPES = ("BUY", "SELL", "TRANSFER", "FEE", "REVERSAL", "STAKING")

# Health-check severity levels (match health_service.ERROR / WARNING)
HEALTH_ERROR   = "error"
HEALTH_WARNING = "warning"
