#!/usr/bin/env python3
"""Testy CLI: subprocess volání hlavních příkazů."""
import sys
import os
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_ROOT, "test_cli.db")
EXPORT_FILE = os.path.join(PROJECT_ROOT, "test_cli_export.csv")


def cleanup():
    for f in [DB, EXPORT_FILE]:
        if os.path.exists(f):
            os.remove(f)


def run_cli(*args):
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, "cli.py"), "--db", DB] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    return result


def test_init():
    """TEST 1: init vytvoří DB a zobrazí stav."""
    cleanup()
    result = run_cli("init")
    assert result.returncode == 0, f"returncode={result.returncode}, stderr={result.stderr}"
    assert os.path.exists(DB), "DB soubor nebyl vytvořen"
    assert "0" in result.stdout
    print(f"  init: DB created, exit 0")


def test_trade():
    """TEST 2: trade vytvoří 2 řádky."""
    cleanup()
    result = run_cli(
        "trade", "--type", "BUY", "--asset", "BTC",
        "--asset-amount", "0.1", "--currency", "EUR",
        "--currency-amount", "5000", "--venue", "kraken",
    )
    assert result.returncode == 0, f"returncode={result.returncode}, stderr={result.stderr}"
    assert "2" in result.stdout

    bal = run_cli("balances")
    assert bal.returncode == 0
    assert "BTC" in bal.stdout
    assert "EUR" in bal.stdout
    print(f"  trade: 2 rows, balances verified")


def test_export():
    """TEST 3: export vytvoří soubor."""
    cleanup()
    run_cli(
        "add", "--type", "FEE", "--asset", "EUR", "--amount", "-10",
        "--currency", "EUR", "--venue", "kraken",
    )
    result = run_cli("export", "--format", "csv", "--output", EXPORT_FILE)
    assert result.returncode == 0, f"returncode={result.returncode}, stderr={result.stderr}"
    assert os.path.exists(EXPORT_FILE), "Export soubor nebyl vytvořen"

    with open(EXPORT_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 2, f"Expected 2 lines (header+1 row), got {len(lines)}"
    print(f"  export: CSV created, {len(lines)-1} data row(s)")


def main():
    tests = [
        ("TEST 1: CLI init", test_init),
        ("TEST 2: CLI trade", test_trade),
        ("TEST 3: CLI export", test_export),
    ]

    for name, test_fn in tests:
        print(f"\n{name}")
        try:
            test_fn()
            print("  PASS")
        except AssertionError as e:
            print(f"  FAIL: {e}")
            cleanup()
            raise

    cleanup()
    print("\n" + "=" * 60)
    print(f"  ALL {len(tests)} CLI TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
