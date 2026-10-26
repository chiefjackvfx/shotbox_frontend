"""Read-only migration aliases keep saved legacy script references usable."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke import preferences
from shotbox_nuke.cache import atomic_write_json, read_manifest, resolve_manifest_path, validate_cache
from shotbox_nuke.protocol import ProtocolError
import test_nuke_published_cache as published_cache_tests

completed = published_cache_tests.completed
project_precomp = published_cache_tests.project_precomp
published_receipt = published_cache_tests.published_receipt


@pytest.fixture
def alias_setup(published_receipt, tmp_path, monkeypatch):
    old = (tmp_path / "legacy precomp/integration.json").resolve()
    cache_root = tmp_path / "local receipt cache"
    prefs = {"application_root": str(tmp_path / "application"), "cache_root": str(cache_root)}
    monkeypatch.setattr(preferences, "load", lambda: prefs)
    alias = cache_root / "aliases" / (hashlib.sha256(str(old).encode("utf-8")).hexdigest() + ".json")
    value = {"schema_version": 1, "original": str(old), "manifest": str(published_receipt)}
    return SimpleNamespace(old=old, root=cache_root, path=alias, value=value, receipt=published_receipt, prefs=prefs)


def test_old_reference_resolves_to_published_receipt_without_writing(alias_setup, tmp_path):
    state = alias_setup
    atomic_write_json(state.path, state.value)
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    assert resolve_manifest_path(state.old) == state.receipt
    assert read_manifest(state.old, require_complete=True)["storage"]["mode"] == "published"
    result = validate_cache(state.old)
    assert result["valid"]
    assert result["manifest_path"] == str(state.receipt)
    assert result["attempt_root"] == str(state.receipt.parent)
    assert not state.old.exists()
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_alias_key_uses_canonical_absolute_original_path(alias_setup):
    state = alias_setup
    atomic_write_json(state.path, state.value)
    reference = state.old.parent / "unused/../integration.json"
    assert resolve_manifest_path(reference) == state.receipt


def test_existing_direct_manifest_bypasses_preferences_and_aliases(alias_setup, monkeypatch):
    state = alias_setup
    monkeypatch.setattr(preferences, "load", lambda: pytest.fail("Existing manifest consulted alias preferences"))
    assert resolve_manifest_path(state.receipt) == state.receipt
    assert validate_cache(state.receipt)["valid"]


def test_missing_alias_does_not_create_cache_or_change_reference(alias_setup):
    state = alias_setup
    assert resolve_manifest_path(state.old) == state.old
    assert not state.root.exists()
    with pytest.raises(ProtocolError, match="Cannot read integration manifest"):
        read_manifest(state.old)
    assert not state.root.exists()


def test_default_alias_root_is_application_cache(alias_setup):
    state = alias_setup
    state.prefs["cache_root"] = ""
    root = Path(state.prefs["application_root"]) / ".shotbox-cache"
    alias = root / "aliases" / state.path.name
    atomic_write_json(alias, state.value)
    assert resolve_manifest_path(state.old) == state.receipt
    assert not state.root.exists()


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(schema_version=2),
    lambda value: value.update(schema_version=True),
    lambda value: value.update(original="/different/original.json"),
    lambda value: value.pop("original"),
    lambda value: value.update(manifest="relative/receipt.json"),
    lambda value: value.update(manifest=""),
    lambda value: value.update(manifest=None),
    lambda value: value.update(manifest="/bad\x00path"),
])
def test_invalid_alias_is_rejected(alias_setup, mutate):
    state = alias_setup
    mutate(state.value)
    atomic_write_json(state.path, state.value)
    with pytest.raises(ProtocolError):
        resolve_manifest_path(state.old)


@pytest.mark.parametrize("content", ["{broken json", "[]", "null", " " * (64 * 1024 + 1)])
def test_malformed_or_oversized_alias_is_rejected(alias_setup, content):
    state = alias_setup
    state.path.parent.mkdir(parents=True)
    state.path.write_text(content)
    with pytest.raises(ProtocolError):
        resolve_manifest_path(state.old)


def test_missing_receipt_is_rejected_without_alias_recursion(alias_setup):
    state = alias_setup
    intermediate = state.old.parent / "second missing manifest.json"
    state.value["manifest"] = str(intermediate)
    atomic_write_json(state.path, state.value)
    second_alias = state.path.parent / (hashlib.sha256(str(intermediate).encode("utf-8")).hexdigest() + ".json")
    atomic_write_json(second_alias, {"schema_version": 1, "original": str(intermediate), "manifest": str(state.receipt)})
    with pytest.raises(ProtocolError, match="cannot be chained"):
        resolve_manifest_path(state.old)


def test_alias_receipt_still_undergoes_normal_manifest_validation(alias_setup):
    state = alias_setup
    manifest = json.loads(state.receipt.read_text())
    manifest["precomp"]["state"] = "incomplete"
    atomic_write_json(state.receipt, manifest)
    atomic_write_json(state.path, state.value)
    with pytest.raises(ProtocolError, match="complete"):
        validate_cache(state.old)
