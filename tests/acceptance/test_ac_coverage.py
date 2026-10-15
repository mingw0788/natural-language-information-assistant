from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
MATRIX = ROOT / "docs" / "acceptance-matrix.md"


def test_acceptance_matrix_lists_every_requirement_once() -> None:
    text = MATRIX.read_text(encoding="utf-8-sig")
    actual = re.findall(r"^\| ((?:FR-\d{3}|NFR-\d{2}|AC-\d{2})) \|", text, re.MULTILINE)
    expected = (
        {f"FR-{number:03}" for number in range(1, 37)}
        | {f"NFR-{number:02}" for number in range(1, 11)}
        | {f"AC-{number:02}" for number in range(1, 37)}
    )

    assert set(actual) == expected
    assert len(actual) == len(expected)


def test_out_of_scope_p1_requirements_are_marked_deferred() -> None:
    text = MATRIX.read_text(encoding="utf-8-sig")
    deferred = {"FR-028", "FR-029", "FR-034", "FR-035", "FR-036"} | {
        f"AC-{number:02}" for number in range(30, 35)
    }

    for requirement_id in deferred:
        row = next(line for line in text.splitlines() if line.startswith(f"| {requirement_id} |"))
        assert "延期" in row
