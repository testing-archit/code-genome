import pytest
from code_genome_analyzers import analyze_source
from code_genome_analyzers.python import analyze_python

SOURCE = '''"""Billing service."""
from __future__ import annotations

import os, json as j
from .models import Invoice
from ..shared import money
from pkg import *


class InvoiceService:
    def total(self, invoice):
        if invoice.items and not invoice.void or invoice.forced:
            return sum(i.amount for i in invoice.items if i.amount)
        return 0

    def _audit(self):
        return requests.post("https://audit.example.com/log", json={})


@cache
def format_total(value):  # comment only
    return money.format(value) if value else "-"


def _private():
    pass
'''


def test_python_facts_are_extracted_deterministically() -> None:
    analysis = analyze_source("billing/service.py", SOURCE)
    assert analysis.language == "python"
    assert analysis.analyzer_version == "tree-sitter-python@0.1.0"
    # `from __future__ import ...` is a compiler directive, not a dependency.
    assert [(item.module, item.names) for item in analysis.imports] == [
        ("json", ("json",)),
        ("os", ("os",)),
        (".models", ("Invoice",)),
        ("..shared", ("money",)),
        ("pkg", ("*",)),
    ]
    assert [(item.name, item.kind, item.exported) for item in analysis.symbols] == [
        ("InvoiceService", "class", True),
        ("total", "method", True),
        ("_audit", "method", False),
        ("format_total", "function", True),
        ("_private", "function", False),
    ]
    assert [item.name for item in analysis.exports] == ["InvoiceService", "format_total"]
    calls = {(item.receiver, item.callee): item.url_host for item in analysis.calls}
    assert calls[("requests", "post")] == "audit.example.com"
    assert ("money", "format") in calls and (None, "sum") in calls
    # if + and + or + comprehension filter + conditional expression = 5 decisions.
    assert analysis.metrics.complexity == 6
    assert analysis.metrics.functions == 4
    assert analysis.diagnostics == ()
    assert analyze_source("billing/service.py", SOURCE) == analysis


def test_broken_python_reports_diagnostics_and_paths_are_validated() -> None:
    assert analyze_python("a.py", "def broken(:\n    pass\n").diagnostics
    with pytest.raises(ValueError):
        analyze_python("../escape.py", "x = 1")
    with pytest.raises(ValueError):
        analyze_python("not_python.ts", "x = 1")
