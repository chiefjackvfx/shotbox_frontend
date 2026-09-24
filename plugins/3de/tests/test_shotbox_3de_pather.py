import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location(
    "shotbox_3de_pather", Path(__file__).resolve().parents[1] / "shotbox_3de_pather.py"
)
pather = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pather)


class UVTexturePathTests(unittest.TestCase):
    def make_tde(self, texture):
        # Restrict the mock to actual API names so nonexistent APIs cannot pass.
        tde = Mock(spec=[
            "getCurrentCamera", "getPGroupList", "getPGroupName",
            "get3DModelList", "get3DModelName", "get3DModelFilepath",
            "get3DModelUVTextureMap", "set3DModelUVTextureMap",
        ])
        tde.getCurrentCamera.return_value = None
        tde.getPGroupList.return_value = ["pg"]
        tde.getPGroupName.return_value = "Group"
        tde.get3DModelList.return_value = ["model"]
        tde.get3DModelName.return_value = "Model"
        tde.get3DModelFilepath.return_value = "<primitive>"
        tde.get3DModelUVTextureMap.return_value = texture
        return tde

    def test_uv_texture_paths_swap_both_directions_without_changing_tile_number(self):
        windows = r"Z:\job\textures\body diffuse.1001.exr"
        linux = "/Volumes/projects/job/textures/body diffuse.1001.exr"
        for source, expected in ((windows, linux), (linux, windows)):
            with self.subTest(source=source):
                tde = self.make_tde(source)
                result = pather.run_path_swap(tde)
                tde.get3DModelUVTextureMap.assert_called_once_with("pg", "model")
                tde.set3DModelUVTextureMap.assert_called_once_with("pg", "model", expected)
                self.assertEqual(result["texture_updates"], ["Group / Model"])
                self.assertEqual(result["texture_skips"], [])

    def test_empty_and_unknown_paths_are_not_written(self):
        for texture in (None, "", "<primitive>", "/unmapped/texture.png"):
            with self.subTest(texture=texture):
                tde = self.make_tde(texture)
                result = pather.run_path_swap(tde)
                tde.set3DModelUVTextureMap.assert_not_called()
                self.assertEqual(result["texture_updates"], [])
                if texture == "/unmapped/texture.png":
                    self.assertEqual(result["texture_skips"], ["Group / Model [unknown root]"])

    def test_missing_uv_api_is_supported_without_calling_nonexistent_functions(self):
        tde = Mock(spec=[])
        self.assertIsNone(pather.get_model_texture(tde, "pg", "model"))
        self.assertFalse(pather.set_model_texture(tde, "pg", "model", "texture.png"))


if __name__ == "__main__":
    unittest.main()
