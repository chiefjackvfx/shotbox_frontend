"""Native synthetic EXR-only publication, cleanup, reload and checksum checks."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, node, precomp, precomp_paths, preferences, readback, render, storage  # noqa: E402
from smoke import close, fixture  # noqa: E402


def make_staged(group, jobs, prefix="staged_precomp"):
    path = fixture(group, jobs, 1001, 1003)
    data = json.loads(path.read_text())
    data["capture"]["source_files"] = [cache.file_record(path.parent / ("source_v001/source.%04d.exr" % frame), path.parent)
                                         for frame in range(1, 4)]
    data["precomp"] = precomp.publish(path.parent, data, prefix + ".%04d.exr")
    cache.atomic_write_json(path, data)
    return path, data


def assert_final_only(directory, data):
    expected = {directory / record["path"] for record in data["precomp"]["files"]}
    assert set(directory.iterdir()) == expected
    assert len(expected) == 3 and all(path.is_file() and path.suffix == ".exr" for path in expected)


def render_output(upstream, directory, label):
    pattern = directory / (label + ".%04d.exr")
    write = nuke.nodes.Write(inputs=[upstream])
    try:
        write["file_type"].setValue("exr")
        write["file"].setValue(str(pattern))
        capture.configure_write(write, channels="all")
        render.farm_preflight(write=write, start=1001, end=1003)
        nuke.execute(write, 1001, 1003, 1, views=["main"], continueOnError=False)
    finally:
        nuke.delete(write)
    assert all(Path(str(pattern) % frame).is_file() for frame in range(1001, 1004))
    return str(pattern)


def legacy_alias_case(group, jobs, base, verification):
    source_path, source_data = make_staged(group, jobs, "legacy_precomp_v01")
    legacy = base / "legacy-project/renders/precomp/legacy_precomp_v01"
    shutil.copytree(source_path.parent, legacy)

    def remap(value):
        if isinstance(value, str):
            return str(legacy) + value[len(str(source_path.parent)):] if value.startswith(str(source_path.parent)) else value
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        if isinstance(value, list):
            return [remap(item) for item in value]
        return value

    for path in legacy.rglob("*.json"):
        cache.atomic_write_json(path, remap(json.loads(path.read_text())))
    old_manifest = legacy / "integration.json"
    legacy_data = json.loads(old_manifest.read_text())
    legacy_data["worker_sha256"] = cache.sha256_file(legacy / "worker.json")
    cache.atomic_write_json(old_manifest, legacy_data)
    assert cache.validate_cache(old_manifest)["valid"]
    # This duplicate was created solely to build a previous-layout fixture;
    # it was never committed as a published job. Remove only our test copy.
    assert jobs.resolve() in source_path.parent.resolve().parents
    shutil.rmtree(source_path.parent)
    node.activate(group, old_manifest)
    readback.install(group, old_manifest)
    script = base / "legacy-saved.nk"
    nuke.scriptSaveAs(str(script), overwrite=True)
    hashes = {path.name: cache.sha256_file(path) for path in legacy.glob("*.exr")}
    migration = storage.migrate_legacy(old_manifest, jobs)
    receipt = Path(migration["receipt"])
    assert migration["exrs_unchanged"] and not old_manifest.exists()
    assert set(legacy.iterdir()) == {legacy / name for name in hashes}
    assert hashes == {path.name: cache.sha256_file(path) for path in legacy.glob("*.exr")}
    assert cache.resolve_manifest_path(old_manifest) == receipt
    assert cache.validate_cache(old_manifest)["valid"]
    nuke.scriptOpen(str(script))
    group = nuke.toNode("CleanSynthetic")
    assert group is not None
    assert cache.validate_cache(group["sb_active_manifest"].value())["valid"]
    assert group.node("SBPrecomp")["file"].value() == str(legacy / legacy_data["precomp"]["pattern"])
    shotbox_nuke.reload_result(group, old_manifest)
    nuke.frame(1001)
    close(group.sample("red", 5, 5), 0.25)
    close(group.sample("alpha", 5, 5), -0.1)
    render_output(group, verification, "legacy-alias-reopened")
    return {"legacy_script": str(script), "old_manifest": str(old_manifest), "receipt": str(receipt),
            "files_sha256": hashes, "migration": migration}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-clean-precomp-host.json"))
    parser.add_argument("--directory", type=Path, default=Path("test-runs/nuke-clean-precomp-host"))
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="case-", dir=args.directory.resolve()))
    report = {"host": capture.host_identity(), "fixture": "native synthetic source/mattes; no inference", "passed": []}
    original_preferences = preferences.load
    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and not nuke.GUI
        shotbox_nuke.register()
        jobs = base / "cache"
        prefs = original_preferences()
        prefs["cache_root"] = str(jobs)
        preferences.load = lambda: dict(prefs)
        nuke.root()["first_frame"].setValue(1001)
        nuke.root()["last_frame"].setValue(1003)
        nuke.root()["format"].setValue(nuke.addFormat("16 12 1 ShotBoxSmoke").name())
        script = base / "shot/nuke/clean.nk"
        script.parent.mkdir(parents=True)
        nuke.scriptSaveAs(str(script), overwrite=True)
        source = nuke.nodes.Constant(name="CleanOriginal")
        source["format"].setValue("ShotBoxSmoke")
        source["color"].setValue([0.25, 0.5, 0.75, 0.9])
        nuke.Layer("cleanaux", ["cleanaux.depth"])
        add = nuke.nodes.AddChannels(inputs=[source])
        add["channels"].setValue("cleanaux")
        auxiliary = nuke.nodes.Expression(inputs=[add])
        auxiliary["channel0"].setValue("cleanaux.depth")
        auxiliary["expr0"].setValue("42")
        group = node.create()
        group.setName("CleanSynthetic")
        group.setInput(0, auxiliary)
        group["sb_precomp_name"].setValue("clean_subject")
        downstream = nuke.nodes.NoOp(name="CleanDownstream", inputs=[group])
        receipts = []
        previous_hashes = {}
        for index in (1, 2):
            path, data = make_staged(group, jobs)
            target = precomp_paths.allocate(script, "clean_subject")
            pattern = Path(str(target["pattern"]).replace("####", "%04d")).name
            data = storage.commit(path.parent, data, output_directory=target["directory"], output_pattern=pattern)
            storage.cleanup(path.parent, data)
            assert set(path.parent.iterdir()) == {path}
            assert_final_only(target["directory"], data)
            assert not list(path.parent.rglob("*.exr")) and not list(path.parent.rglob("*.png"))
            assert data["storage"]["mode"] == "published"
            assert cache.validate_cache(path, checksums=True)["valid"]
            node.activate(group, path)
            read = readback.install(group, path)
            assert downstream.input(0) is read
            if index == 1:
                identity = read["sb_read_id"].value()
            else:
                assert read["sb_read_id"].value() == identity
            nuke.frame(1001)
            close(read.sample("red", 5, 5), 0.25)
            close(read.sample("alpha", 5, 5), -0.1)
            close(read.sample("cleanaux.depth", 5, 5), 42)
            close(group.sample("red", 5, 5), 0.25)
            close(group.sample("alpha", 5, 5), -0.1)
            group["sb_output"].setValue("Matte only")
            close(group.sample("red", 5, 5), -0.1)
            close(group.sample("green", 5, 5), -0.1)
            group["sb_output"].setValue("Original RGB + generated alpha")
            read["reload"].execute()
            shotbox_nuke.reload_result(group, path)
            render.validate_node(group)
            assert all(cache.sha256_file(previous) == digest for previous, digest in previous_hashes.items())
            previous_hashes.update({str(target["directory"] / record["path"]): record["sha256"] for record in data["precomp"]["files"]})
            receipts.append({"receipt": str(path), "output_directory": str(target["directory"]),
                             "files_sha256": {str(target["directory"] / record["path"]): record["sha256"] for record in data["precomp"]["files"]}})
        report["passed"].append("two native publications produce EXR-only version folders, private job cleanup leaves only receipts and previous files remain unchanged")
        report["passed"].append("compact receipt validates after source/matte/backend deletion; native Read, Group, matte-only view and explicit Reload retain original RGB, generated alpha and auxiliary depth")
        final_file = Path(next(iter(receipts[-1]["files_sha256"])))
        original = final_file.read_bytes()
        corrupt = bytearray(original)
        corrupt[len(corrupt) // 2] ^= 1
        final_file.write_bytes(corrupt)
        try:
            try:
                cache.validate_cache(path, checksums=True)
            except Exception as exc:
                assert "checksum" in str(exc).lower(), str(exc)
            else:
                raise AssertionError("Wrong EXR bytes of unchanged size were accepted")
        finally:
            final_file.write_bytes(original)
        assert cache.validate_cache(path, checksums=True)["valid"]
        report["passed"].append("same-size corruption of a published EXR is rejected by the retained checksum receipt")
        os.environ["SHOTBOX_PYTHON"] = "/deliberately-unavailable-shotbox-python"
        verification = base / "verification"
        verification.mkdir()
        read_render = render_output(read, verification, "read")
        group_render = render_output(group, verification, "group")
        nuke.scriptSave()
        nuke.scriptOpen(str(script))
        group = nuke.toNode("CleanSynthetic")
        read = readback.find(group)
        assert read is not None and read["sb_read_id"].value() == identity
        assert cache.validate_cache(group["sb_active_manifest"].value(), checksums=True)["valid"]
        shotbox_nuke.reload_result(group)
        render_output(group, verification, "reopened")
        assert not any(name.startswith(("torch", "shotbox_matte", "PyQt")) for name in sys.modules)
        report["passed"].append("save/reopen and explicit farm preflight/render work after cleanup with external interpreter unavailable and no inference imports")
        migration = legacy_alias_case(group, jobs, base, verification)
        report["passed"].append("a saved legacy script referring to the removed public integration.json reopens, previews, Reloads and renders through its external alias/receipt with every original final EXR unchanged")
        report.update(status="passed", script=str(script), versions=receipts, read_render=read_render, group_render=group_render, legacy_migration=migration)
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        preferences.load = original_preferences
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_CLEAN_PRECOMP_HOST_PASS " + str(args.report))


if __name__ == "__main__":
    main()
