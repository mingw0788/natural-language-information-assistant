from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from openpyxl import load_workbook

from info_assistant.domain.models import TaskField, TaskSpec
from info_assistant.services.export_service import ExportService
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


def make_export_service(tmp_path: Path) -> tuple[ExportService, str, str]:
    database = Database(tmp_path / "export.sqlite3")
    database.migrate()
    task = Repository(database).create_task(
        TaskSpec(
            raw_input="查产品价格",
            fields=[TaskField(name="价格")],
            required_conditions=[],
            assumptions=["未指定地区，不推断地区"],
        )
    )
    return ExportService(database), task.id, task.latest_version.id


def sample_records() -> list[dict]:
    return [
        {
            "entity_id": "90071992547409931234",
            "entity": "=HYPERLINK(\"https://evil.example\")",
            "status": "confirmed",
            "reasons": ["有精确来源摘录"],
            "observations": [
                {
                    "field": "价格",
                    "value": "+cmd|' /C calc'!A0",
                    "status": "confirmed",
                    "unit": None,
                    "period": "每月",
                    "origin": "source",
                    "evidence": [
                        {
                            "quote": "价格：+cmd|' /C calc'!A0",
                            "url": "https://example.test/pricing",
                            "title": "Example pricing",
                        }
                    ],
                }
            ],
        }
    ]


def test_csv_escapes_formula_text_and_includes_trace_metadata(tmp_path: Path) -> None:
    service, task_id, version_id = make_export_service(tmp_path)

    artifact = service.export(
        task_id=task_id,
        task_version_id=version_id,
        run_id="run-123",
        spec=TaskSpec(raw_input="查产品价格", assumptions=["未指定地区，不推断地区"]),
        records=sample_records(),
        format="csv",
        view="confirmed",
    )

    content = artifact.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    assert rows[0]["entity"].startswith("'=")
    assert rows[0]["value"].startswith("'+")
    assert rows[0]["task_version_id"] == version_id
    assert rows[0]["view"] == "confirmed"
    assert "未指定地区，不推断地区" in rows[0]["conditions_and_limits"]


def test_xlsx_uses_text_cells_and_formula_safety(tmp_path: Path) -> None:
    service, task_id, version_id = make_export_service(tmp_path)
    artifact = service.export(
        task_id=task_id,
        task_version_id=version_id,
        run_id=None,
        spec=TaskSpec(raw_input="查产品价格"),
        records=sample_records(),
        format="xlsx",
    )

    workbook = load_workbook(io.BytesIO(artifact.content), data_only=False)
    cell = workbook["结果"]["A2"]
    value_index = [cell.value for cell in workbook["结果"][1]].index("entity") + 1
    entity = workbook["结果"].cell(row=2, column=value_index)
    assert cell.data_type == "s"
    assert str(workbook["结果"].cell(row=2, column=1).value) == task_id
    assert entity.data_type == "s"
    assert entity.value.startswith("'=")
    assert workbook["元信息"]["B2"].value == version_id


def test_json_and_markdown_are_auditable_and_export_is_recorded(tmp_path: Path) -> None:
    service, task_id, version_id = make_export_service(tmp_path)
    kwargs = {
        "task_id": task_id,
        "task_version_id": version_id,
        "run_id": "run-xyz",
        "spec": TaskSpec(raw_input="查产品价格", assumptions=["时间未指定"]),
        "records": sample_records(),
        "format": "json",
        "view": "all",
    }

    artifact = service.export(**kwargs)
    payload = json.loads(artifact.content)
    assert payload["metadata"]["task_id"] == task_id
    assert payload["metadata"]["run_id"] == "run-xyz"
    assert payload["metadata"]["assumptions"] == ["时间未指定"]
    assert payload["records"][0]["observations"][0]["evidence"][0]["url"]

    markdown = service.export(**{**kwargs, "format": "markdown"}).content.decode("utf-8")
    assert "任务版本" in markdown
    assert "来源摘录" in markdown
    assert "\|" in markdown
    with service.database.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM exports").fetchone()[0]
    assert count == 2
