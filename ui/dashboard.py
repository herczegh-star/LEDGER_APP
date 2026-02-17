#!/usr/bin/env python3
"""Flet Dashboard v1: read-only zobrazení ledgeru."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft
from decimal import Decimal
from core.config import load_config
from core.service import LedgerService


def main(page: ft.Page):
    cfg = load_config()
    svc = LedgerService(cfg["db_path"])

    page.title = "Ledger App"
    page.window.width = 960
    page.window.height = 640
    page.padding = 20

    # --- Status bar ---
    status_text = ft.Text("", size=12, color=ft.Colors.GREY_600)

    def update_status():
        count = svc.count()
        warnings = svc.diagnostics()
        parts = [f"{count} rows"]
        if warnings:
            parts.append(f"{len(warnings)} warnings")
        status_text.value = " | ".join(parts)

    # --- Balances tab ---
    balances_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ASSET", weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("BALANCE", weight=ft.FontWeight.BOLD), numeric=True),
        ],
        rows=[],
        column_spacing=40,
    )

    def load_balances():
        balances = svc.asset_balances()
        balances_table.rows.clear()
        for asset, balance in sorted(balances.items()):
            is_negative = balance < 0
            balances_table.rows.append(
                ft.DataRow(cells=[
                    ft.DataCell(ft.Text(asset)),
                    ft.DataCell(ft.Text(
                        str(balance),
                        color=ft.Colors.RED if is_negative else None,
                        weight=ft.FontWeight.BOLD if is_negative else None,
                    )),
                ])
            )

    # --- Timeline tab ---
    timeline_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("TIMESTAMP", weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("TYPE", weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("ASSET", weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("AMOUNT", weight=ft.FontWeight.BOLD), numeric=True),
            ft.DataColumn(ft.Text("CURRENCY", weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("VENUE", weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("NOTE", weight=ft.FontWeight.BOLD)),
        ],
        rows=[],
        column_spacing=20,
    )

    def load_timeline():
        rows = svc.timeline()
        timeline_table.rows.clear()
        for r in rows:
            note = r.note or ""
            if len(note) > 25:
                note = note[:22] + "..."
            timeline_table.rows.append(
                ft.DataRow(cells=[
                    ft.DataCell(ft.Text(r.timestamp.strftime("%Y-%m-%d %H:%M"), size=12)),
                    ft.DataCell(ft.Text(r.type, size=12)),
                    ft.DataCell(ft.Text(r.asset, size=12)),
                    ft.DataCell(ft.Text(str(r.amount), size=12,
                        color=ft.Colors.RED if r.amount < 0 else ft.Colors.GREEN,
                    )),
                    ft.DataCell(ft.Text(r.currency, size=12)),
                    ft.DataCell(ft.Text(r.venue, size=12)),
                    ft.DataCell(ft.Text(note, size=12)),
                ])
            )

    # --- Tab content ---
    balances_content = ft.Column(
        [ft.Text("Asset Balances", size=20, weight=ft.FontWeight.BOLD),
         ft.Divider(),
         balances_table],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    timeline_content = ft.Column(
        [ft.Text("Timeline", size=20, weight=ft.FontWeight.BOLD),
         ft.Divider(),
         timeline_table],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # --- Tabs ---
    tabs = ft.Tabs(
        selected_index=0,
        tabs=[
            ft.Tab(text="Balances", content=balances_content),
            ft.Tab(text="Timeline", content=timeline_content),
        ],
        expand=True,
    )

    def on_tab_change(e):
        if tabs.selected_index == 0:
            load_balances()
        else:
            load_timeline()
        update_status()
        page.update()

    tabs.on_change = on_tab_change

    # --- Initial load ---
    load_balances()
    update_status()

    page.add(tabs, ft.Divider(), status_text)


if __name__ == "__main__":
    ft.app(target=main)
