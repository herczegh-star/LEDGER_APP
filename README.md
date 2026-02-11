# Ledger App – MVP

Investiční portfolio tracker nad `unified_format_raw`.

## Filozofie

Aplikace je nástroj pro čtení a vytváření tokového ledgeru,
nikoli pro jeho interpretaci; pravda je v datech, nikoli v kódu.

## Struktura projektu

```
ledger_app/
├── main.py              # Entry point
├── core/
│   ├── model.py         # RawRow datový model
│   ├── validator.py     # Syntaktická validace
│   └── ledger_store.py  # SQLite append-only DB
├── io_module/
│   └── raw_loader.py    # Načítání *_raw.xlsm / .csv
├── ui/
│   └── terminal.py      # Terminálové UI (nahraditelné Fletem)
└── test_mvp.py          # Testy MVP kritérií
```

## Spuštění

```bash
python main.py [cesta_k_databazi.db]
```

## MVP kritéria (splněno ✓)

- ✓ Načtu RAW soubory → vidím všechny toky
- ✓ Načtu je znovu → nic nepřibyde (deduplikace)
- ✓ Přidám ručně tok → objeví se v timeline
- ✓ Udělám chybu → opravím přes REVERSAL
- ✓ Vše ostatní se vždy přepočítá

## Závislosti

- Python 3.10+
- openpyxl (pro čtení .xlsm)
- SQLite (součást Pythonu)
- Flet (pro GUI – zatím nahrazeno terminálovým UI)
