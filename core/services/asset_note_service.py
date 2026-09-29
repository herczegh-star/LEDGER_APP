"""Asset note service: validated writer for asset_notes.

set_asset_note() / delete_asset_note() are the ONLY supported ways to write
or remove an asset note. Pure UI metadata: NEVER touches the `ledger` table,
NEVER builds a RawRow, and has no effect on quantity/cost_basis/WAC/realized
PnL (see core/asset_note_store.py's module docstring).
"""
from __future__ import annotations

from datetime import datetime

from core.asset_note_store import AssetNote, AssetNoteStore


def set_asset_note(db_path: str, asset: str, note: str) -> AssetNote:
    """Validate and upsert the note for *asset*.

    Raises ValueError (before any DB write) for invalid input:
      - asset empty
      - note empty (use delete_asset_note() to remove a note instead)
    """
    if not asset or not asset.strip():
        raise ValueError("asset nesmí být prázdný")
    if not note or not note.strip():
        raise ValueError(
            "note nesmí být prázdná — pro odstranění poznámky použij delete_asset_note()"
        )

    asset_uc = asset.upper().strip()
    result = AssetNote(
        asset=asset_uc,
        note=note.strip(),
        updated_at=datetime.now(),
    )

    store = AssetNoteStore(db_path)
    try:
        store.set_note(result)
    finally:
        store.close()

    return result


def delete_asset_note(db_path: str, asset: str) -> bool:
    """Delete the note for *asset*, if any. Returns True iff a note existed."""
    if not asset or not asset.strip():
        raise ValueError("asset nesmí být prázdný")

    asset_uc = asset.upper().strip()
    store = AssetNoteStore(db_path)
    try:
        return store.delete_note(asset_uc)
    finally:
        store.close()
