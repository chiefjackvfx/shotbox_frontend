"""Actual Nuke native Read installation; synthetic mattes, no inference."""
import argparse
import json
from pathlib import Path
import sys
import traceback

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, node, precomp, readback  # noqa: E402
from smoke import close, fixture  # noqa: E402


def complete_fixture(group, directory, suffix):
    manifest_path = fixture(group, directory, 1001, 1003)
    manifest = json.loads(manifest_path.read_text())
    manifest["capture"]["source_files"] = [cache.file_record(manifest_path.parent / ("source_v001/source.%04d.exr" % frame), manifest_path.parent)
                                             for frame in range(1, 4)]
    manifest["precomp"] = precomp.publish(manifest_path.parent, manifest, "synthetic_%s.%%04d.exr" % suffix)
    cache.atomic_write_json(manifest_path, manifest)
    node.activate(group, manifest_path)
    return manifest_path


def managed_reads():
    return [candidate for candidate in nuke.allNodes(group=nuke.root(), recurseGroups=True)
            if candidate.Class() == "Read" and candidate.knob("sb_read_owner") is not None]


def check_read(group, read, manifest_path, parent):
    assert read.Class() == "Read"
    assert read.fullName().rpartition(".")[0] == parent
    assert read["raw"].value() and read["first"].value() == 1001 and read["last"].value() == 1003
    assert read["sb_read_owner"].value() == group["sb_uuid"].value()
    assert read["sb_read_manifest"].value() == str(manifest_path)
    assert group["sb_output_read"].value() == read.fullName()
    assert group["sb_output_read_id"].value() == read["sb_read_id"].value()
    assert group.node(read.name()) is None, "Read must be outside the generating Group"
    nuke.frame(1001)
    close(read.sample("red", 5, 5), 0.25)
    close(read.sample("alpha", 5, 5), -0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-readback-host.json"))
    parser.add_argument("--directory", type=Path, default=Path("test-runs/nuke-readback-host"))
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = {"host": capture.host_identity(), "fixture": "native synthetic source/matte; no inference", "passed": []}
    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and not nuke.GUI
        shotbox_nuke.register()
        nuke.root()["first_frame"].setValue(1001)
        nuke.root()["last_frame"].setValue(1003)
        nuke.root()["format"].setValue(nuke.addFormat("16 12 1 ShotBoxSmoke").name())
        original = nuke.nodes.Constant(name="ReadbackOriginal")
        original["format"].setValue("ShotBoxSmoke")
        original["color"].setValue([0.25, 0.5, 0.75, 0.9])
        group = node.create()
        group.setName("SyntheticReadback")
        group.setInput(0, original)
        consumer = nuke.nodes.Merge2(name="BothInputSlots", inputs=[group, group])
        artist = nuke.nodes.NoOp(name="ArtistChangedConnection", inputs=[group])
        first = complete_fixture(group, directory, "v01")
        read = readback.install(group, first)
        check_read(group, read, first, "")
        assert consumer.input(0) is read and consumer.input(1) is read and artist.input(0) is read
        assert readback.find(group) is read
        assert readback.install(group, first) is read and len(managed_reads()) == 1
        report["passed"].append("native standalone Read is created in root network with raw RGBA, original timeline range and every existing downstream input slot rewired")
        first_identity = read["sb_read_id"].value()
        read.setName("ArtistRenamedRead")
        read.setXYpos(330, 420)
        assert readback.find(group) is read
        first_hashes = {str(path): cache.sha256_file(path) for path in first.parent.glob("synthetic_v01.*.exr")}
        artist.setInput(0, original)
        second = complete_fixture(group, directory, "v02")
        assert readback.install(group, second) is read
        check_read(group, read, second, "")
        assert read["sb_read_id"].value() == first_identity
        assert read.name() == "ArtistRenamedRead" and (read.xpos(), read.ypos()) == (330, 420)
        assert artist.input(0) is original
        assert all(cache.sha256_file(path) == digest for path, digest in first_hashes.items())
        report["passed"].append("regeneration reuses the owned Read, preserves its artist-edited name/position, updates its sequence, preserves previous EXRs and respects artist-changed connections")
        nuke.delete(read)
        assert readback.find(group) is None
        replacement = readback.install(group, second)
        check_read(group, replacement, second, "")
        assert replacement["sb_read_id"].value() != first_identity and len(managed_reads()) == 1
        report["passed"].append("deleting the external Read allows safe recreation on the next result installation")
        wrapper = nuke.nodes.Group(name="NestedParent", inputs=[original])
        wrapper.begin()
        try:
            inlet = nuke.nodes.Input(name="NestedInput")
            nested = node.create()
            nested.setName("NestedReadback")
            nested.setInput(0, inlet)
            outlet = nuke.nodes.Output(inputs=[nested])
            nested_manifest = complete_fixture(nested, directory, "nested")
            nested_read = readback.install(nested, nested_manifest)
            check_read(nested, nested_read, nested_manifest, "NestedParent")
            assert outlet.input(0) is nested_read
        finally:
            wrapper.end()
        assert len(managed_reads()) == 2, [candidate.fullName() for candidate in managed_reads()]
        report["passed"].append("a generating Group inside another Group gets its standalone Read in that parent network and reconnects the parent Output")
        # Native output rendering proves the external Read is independently usable.
        output = directory / "standalone.%04d.exr"
        write = nuke.nodes.Write(name="StandaloneReadWrite", inputs=[replacement])
        write["file_type"].setValue("exr")
        write["file"].setValue(str(output))
        capture.configure_write(write, channels="rgba")
        nuke.execute(write, 1001, 1003, 1, views=["main"], continueOnError=False)
        assert all(Path(str(output) % frame).is_file() for frame in range(1001, 1004))
        script = directory / "readback.nk"
        nuke.scriptSaveAs(str(script), overwrite=True)
        names = sorted(candidate.fullName() for candidate in managed_reads())
        nuke.scriptOpen(str(script))
        assert sorted(candidate.fullName() for candidate in managed_reads()) == names
        assert readback.find(nuke.toNode("SyntheticReadback")) is not None
        assert readback.find(nuke.toNode("NestedParent.NestedReadback")) is not None
        report["passed"].append("native standalone Read renders all selected frames and save/reopen preserves both root/nested Reads without duplicates")
        report.update(status="passed", script=str(script), standalone_pattern=str(output), reads=names)
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_READBACK_HOST_PASS " + str(args.report))


if __name__ == "__main__":
    main()
