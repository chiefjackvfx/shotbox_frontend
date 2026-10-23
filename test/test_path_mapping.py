from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest import mock


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import path_mapping


class PathMappingTests(unittest.TestCase):
    def test_comparison_handles_windows_drive_case_quotes_and_extended_paths(self):
        paths = (
            r'"z:\projects\C2255_Bamboo_HR\VFX\BAMBOO_HR\bam050\scripts\bam050.nk"',
            "/Volumes/projects/PROJECTS/C2255_Bamboo_HR/VFX/BAMBOO_HR/bam050/scripts/bam050.nk",
            r"\\?\Z:\PROJECTS\C2255_Bamboo_HR\VFX\BAMBOO_HR\bam050\scripts\bam050.nk",
        )
        self.assertEqual(len({path_mapping.comparison_path(path, system="Windows") for path in paths}), 1)

    def test_comparison_unifies_linux_mount_aliases_without_folding_case(self):
        paths = (
            "/Volumes/projects/PROJECTS/Job/shot010/script.nk",
            "/mnt/projects/PROJECTS/Job/shot010/script.nk",
            r"Z:\PROJECTS\Job\shot010\script.nk",
        )
        self.assertEqual(len({path_mapping.comparison_path(path, system="Linux") for path in paths}), 1)
        self.assertNotEqual(
            path_mapping.comparison_path(paths[0], system="Linux"),
            path_mapping.comparison_path(paths[0].replace("Job", "job"), system="Linux"),
        )

    def test_unc_comparison_accepts_extended_windows_prefix(self):
        self.assertEqual(
            path_mapping.comparison_path(r"\\?\UNC\server\share\Shot\script.nk", system="Windows"),
            path_mapping.comparison_path(r"\\SERVER\SHARE\shot\script.nk", system="Windows"),
        )

    def test_unc_and_mapped_project_drive_compare_equally(self):
        with mock.patch.object(path_mapping, "_windows_network_root", side_effect=lambda drive: (
            "//server/projects" if drive.upper() == "Z:" else None
        )):
            self.assertEqual(
                path_mapping.comparison_path(r"\\server\projects\PROJECTS\Job\shot010\script.nk", system="Windows"),
                path_mapping.comparison_path(r"Z:\PROJECTS\Job\shot010\script.nk", system="Windows"),
            )
            self.assertNotEqual(
                path_mapping.comparison_path(r"\\server\projects\PROJECTS-archive\Job\script.nk", system="Windows"),
                path_mapping.comparison_path(r"Z:\PROJECTS\Job\script.nk", system="Windows"),
            )

    def test_windows_network_drive_resolution_handles_success_and_disconnection(self):
        import ctypes
        def connected(drive, buffer, length):
            self.assertEqual(drive, "Z:")
            buffer.value = r"\\server\projects"
            return 0
        with mock.patch.object(path_mapping.os, "name", "nt"), \
                mock.patch.object(ctypes, "WinDLL", create=True) as library:
            api = library.return_value.WNetGetConnectionW
            api.side_effect = connected
            self.assertEqual(path_mapping._windows_network_root("Z:"), "//server/projects")
            api.side_effect = None
            api.return_value = 2250  # ERROR_NOT_CONNECTED
            self.assertIsNone(path_mapping._windows_network_root("Z:"))

    def test_maps_expansion_b_from_windows_to_linux_and_darwin(self):
        source = r"X:\Projects B\Job One\shot010\plate.mov"
        expected = "/Volumes/Expansion_B/Projects B/Job One/shot010/plate.mov"

        self.assertEqual(path_mapping.convert_path(source, system="Linux"), expected)
        self.assertEqual(path_mapping.convert_path(source, system="Darwin"), expected)

    def test_maps_expansion_b_from_posix_to_windows(self):
        source = "/Volumes/Expansion_B/Projects B/Job One/shot010/plate.mov"

        self.assertEqual(
            path_mapping.convert_path(source, system="Windows"),
            r"X:\Projects B\Job One\shot010\plate.mov",
        )

    def test_maps_root_only_and_ignores_trailing_separator(self):
        self.assertEqual(
            path_mapping.convert_path("X:/Projects B/", system="Linux"),
            "/Volumes/Expansion_B/Projects B",
        )
        self.assertEqual(
            path_mapping.convert_path(
                "/Volumes/Expansion_B/Projects B/",
                system="Windows",
            ),
            r"X:\Projects B",
        )

    def test_windows_roots_are_case_insensitive(self):
        self.assertEqual(
            path_mapping.convert_path(
                "x:/projects b/Job/plate.mov",
                system="Linux",
            ),
            "/Volumes/Expansion_B/Projects B/Job/plate.mov",
        )

    def test_root_matching_does_not_match_similarly_named_folders(self):
        source = "X:/Projects Backup/Job/plate.mov"
        self.assertEqual(path_mapping.convert_path(source, system="Linux"), source)

    def test_expansion_b_never_uses_the_z_project_mapping(self):
        with mock.patch.object(path_mapping.os.path, "exists", return_value=True):
            result = path_mapping.convert_path(
                "X:/Projects B/Job/plate.mov",
                system="Linux",
            )

        self.assertEqual(
            result,
            "/Volumes/Expansion_B/Projects B/Job/plate.mov",
        )

    def test_existing_z_mapping_keeps_linux_root_preference(self):
        with mock.patch.object(
            path_mapping.os.path,
            "exists",
            side_effect=lambda path: path == "/Volumes/projects/PROJECTS",
        ):
            result = path_mapping.convert_path(
                "Z:/PROJECTS/Job/plate.mov",
                system="Linux",
            )

        self.assertEqual(result, "/Volumes/projects/PROJECTS/Job/plate.mov")

    def test_existing_z_mapping_falls_back_to_mnt_on_linux(self):
        with mock.patch.object(path_mapping.os.path, "exists", return_value=False):
            result = path_mapping.convert_path(
                "Z:/PROJECTS/Job/plate.mov",
                system="Linux",
            )

        self.assertEqual(result, "/mnt/projects/PROJECTS/Job/plate.mov")

    def test_existing_posix_mapping_converts_to_z_on_windows(self):
        self.assertEqual(
            path_mapping.convert_path(
                "/Volumes/projects/PROJECTS/Job/plate.mov",
                system="Windows",
            ),
            r"Z:\PROJECTS\Job\plate.mov",
        )

    def test_paths_already_valid_for_target_and_unmapped_paths_are_unchanged(self):
        expansion_path = "/Volumes/Expansion_B/Projects B/Job/plate.mov"
        unrelated_path = "/srv/archive/Job/plate.mov"

        self.assertEqual(
            path_mapping.convert_path(expansion_path, system="Linux"),
            expansion_path,
        )
        self.assertEqual(
            path_mapping.convert_path(unrelated_path, system="Linux"),
            unrelated_path,
        )
        self.assertEqual(path_mapping.convert_path("", system="Linux"), "")
        self.assertEqual(path_mapping.convert_path(None, system="Linux"), "")


if __name__ == "__main__":
    unittest.main()
