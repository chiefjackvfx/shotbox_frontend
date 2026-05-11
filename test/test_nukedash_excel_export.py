from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import nukedash_excel_export

try:
    import openpyxl  # noqa: F401
except ImportError:
    openpyxl = None


def sample_job() -> dict:
    return {
        "id": 1,
        "title": "Client Job",
        "timelines": [
            {
                "id": 10,
                "title": "Main/Edit:01*Long Name That Needs Trimming",
                "shots": [
                    {
                        "id": 100,
                        "title": "sho010",
                        "duration": "42",
                        "thumbnail": "",
                        "hidden": False,
                        "tasks": [
                            {
                                "id": 1,
                                "title": "Comp",
                                "status": "done",
                                "hidden": False,
                            },
                            {
                                "id": 2,
                                "title": "Paint",
                                "status": "assigned",
                                "hidden": False,
                            },
                            {
                                "id": 3,
                                "title": "Hidden cleanup",
                                "status": "done",
                                "hidden": True,
                            },
                        ],
                    },
                    {
                        "id": 101,
                        "title": "sho020",
                        "duration": "12",
                        "thumbnail": "",
                        "hidden": True,
                    },
                ],
            },
            {
                "id": 11,
                "title": "Second",
                "shots": [
                    {
                        "id": 110,
                        "title": "sho030",
                        "duration": "25",
                        "thumbnail": "/missing-thumb.jpg",
                        "hidden": False,
                    },
                ],
            },
        ],
    }


class TimelineExcelExportTests(unittest.TestCase):
    def test_sanitize_excel_sheet_title_removes_invalid_chars_and_trims(self):
        title = nukedash_excel_export.sanitize_excel_sheet_title(
            " Main/Edit:01*Name?With[Bad]CharsAndTooLong "
        )

        self.assertEqual(title, "Main_Edit_01_Name_With_Bad_Char")
        self.assertLessEqual(len(title), 31)

    def test_iter_export_shots_excludes_hidden_by_default(self):
        shots = nukedash_excel_export.iter_export_shots(sample_job()["timelines"][0])

        self.assertEqual([shot["title"] for shot in shots], ["sho010"])

    def test_iter_export_shots_includes_hidden_when_requested(self):
        shots = nukedash_excel_export.iter_export_shots(
            sample_job()["timelines"][0],
            include_hidden=True,
        )

        self.assertEqual([shot["title"] for shot in shots], ["sho010", "sho020"])

    def test_task_titles_summary_combines_titles_only_and_skips_hidden(self):
        summary = nukedash_excel_export.task_titles_summary(sample_job()["timelines"][0]["shots"][0])

        self.assertEqual(summary, "Comp\nPaint")
        self.assertNotIn("done", summary)
        self.assertNotIn("assigned", summary)
        self.assertNotIn("Hidden cleanup", summary)

    def test_task_titles_summary_handles_missing_tasks(self):
        self.assertEqual(nukedash_excel_export.task_titles_summary({"title": "sho010"}), "")

    @unittest.skipIf(openpyxl is None, "openpyxl is not installed")
    def test_workbook_creates_one_sheet_per_timeline_with_basic_shot_info(self):
        workbook = nukedash_excel_export.create_timeline_summary_workbook(sample_job())

        self.assertEqual(
            workbook.sheetnames,
            ["Main_Edit_01_Long Name That Nee", "Second"],
        )
        main = workbook["Main_Edit_01_Long Name That Nee"]
        self.assertEqual(main["A1"].value, "Thumbnail")
        self.assertEqual(main["B1"].value, "Shot")
        self.assertEqual(main["C1"].value, "Duration")
        self.assertEqual(main["D1"].value, "Tasks")
        self.assertEqual(main["B2"].value, "sho010")
        self.assertEqual(main["C2"].value, "42")
        self.assertEqual(main["D2"].value, "Comp\nPaint")
        self.assertTrue(main["D2"].alignment.wrap_text)
        self.assertIsNone(main["B3"].value)

    @unittest.skipIf(openpyxl is None, "openpyxl is not installed")
    def test_workbook_can_include_hidden_shots(self):
        workbook = nukedash_excel_export.create_timeline_summary_workbook(
            sample_job(),
            include_hidden=True,
        )

        main = workbook["Main_Edit_01_Long Name That Nee"]
        self.assertEqual(main["B2"].value, "sho010")
        self.assertEqual(main["B3"].value, "sho020")
        self.assertEqual(main["C3"].value, "12")

    @unittest.skipIf(openpyxl is None, "openpyxl is not installed")
    def test_missing_thumbnail_does_not_prevent_export_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "summary.xlsx"

            result = nukedash_excel_export.export_job_timeline_summary(
                sample_job(),
                output_path,
                base_url="http://127.0.0.1:8000",
            )

            self.assertEqual(result, output_path)
            self.assertTrue(output_path.is_file())

    @unittest.skipIf(openpyxl is None, "openpyxl is not installed")
    def test_empty_filtered_timeline_still_has_header_sheet(self):
        job = sample_job()
        job["timelines"][0]["shots"] = []

        workbook = nukedash_excel_export.create_timeline_summary_workbook(job)

        main = workbook["Main_Edit_01_Long Name That Nee"]
        self.assertEqual(main["A1"].value, "Thumbnail")
        self.assertEqual(main["B1"].value, "Shot")
        self.assertEqual(main["C1"].value, "Duration")
        self.assertEqual(main["D1"].value, "Tasks")
        self.assertIsNone(main["B2"].value)

    @unittest.skipIf(openpyxl is None, "openpyxl is not installed")
    def test_thumbnail_image_uses_importer_safe_cell_anchor(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            thumb_path = Path(tmpdir) / "thumb.png"
            Image.new("RGB", (16, 9), color=(30, 60, 90)).save(thumb_path)
            job = sample_job()
            job["timelines"][0]["shots"] = [
                {
                    "id": 100,
                    "title": "sho010",
                    "duration": "42",
                    "thumbnail": str(thumb_path),
                    "hidden": False,
                }
            ]

            workbook = nukedash_excel_export.create_timeline_summary_workbook(job)

            worksheet = workbook["Main_Edit_01_Long Name That Nee"]
            self.assertEqual(len(worksheet._images), 1)
            self.assertEqual(worksheet._images[0].anchor, "A2")


if __name__ == "__main__":
    unittest.main()
