"""Race-safe naming and staged, recoverable replacement of an EXR sequence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import errno
import hashlib
import json
import os
import re
import shutil
import uuid


MANIFEST = "_shotbox_job.json"
LEGACY_SIDECARS = (MANIFEST, "_shotbox_source.png", "_shotbox_matte.png")


def job_storage_root() -> Path:
    """Keep job records outside results, beside the app or in an explicit location."""
    override = os.environ.get("SHOTBOX_JOB_DIR")
    if override:
        return Path(override).expanduser().resolve()
    portable = os.environ.get("SHOTBOX_PORTABLE_DIR")
    root = Path(portable).expanduser().resolve() if portable else Path(__file__).resolve().parents[1]
    return root / "logs" / "jobs"


def validate_suffix(suffix: str) -> None:
    if (not isinstance(suffix, str) or not suffix.strip("_").strip() or suffix != suffix.strip()
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', suffix)):
        raise ValueError("Use a non-empty filename-safe task suffix, such as _roto.")


def _parts(source: Path, suffix: str) -> tuple[str, int, int]:
    validate_suffix(suffix)
    found = re.fullmatch(r"(.+)_v(\d+)", source.name, re.IGNORECASE)
    stem, number, width = (found[1], int(found[2]), len(found[2])) if found else (source.name, 1, 3)
    return stem + "_" + suffix.lstrip("_"), number, width


def output_target(source: Path, suffix: str, version: int | None = None) -> Path:
    source = Path(source).resolve()
    stem, number, width = _parts(source, suffix)
    target = source.with_name(f"{stem}_v{number if version is None else version:0{width}d}")
    if target.resolve() == source:
        raise ValueError("Output folder must differ from the source folder.")
    return target


def _next_target(source: Path, suffix: str) -> Path:
    target = output_target(source, suffix)
    lock = _lock_path(target)
    if not target.exists() and not target.is_symlink() and not lock.exists() and not lock.is_symlink():
        return target
    stem, number, _ = _parts(source, suffix)
    pattern = re.compile(re.escape(stem) + r"_v(\d+)", re.IGNORECASE)
    versions = [number]
    siblings = list(source.parent.iterdir())
    if lock.parent != source.parent and lock.parent.is_dir():
        siblings.extend(lock.parent.iterdir())
    for sibling in siblings:
        name = sibling.name
        if name.startswith(".") and name.endswith(".shotbox-lock"):
            name = name[1:-len(".shotbox-lock")]
        found = pattern.fullmatch(name)
        if found:
            versions.append(int(found[1]))
    return output_target(source, suffix, max(versions) + 1)


def _lock_path(target: Path) -> Path:
    temporary = _temporary_root()
    if temporary is None:
        return target.with_name(f".{target.name}.shotbox-lock")
    if temporary.is_relative_to(target.resolve()):
        raise ValueError("Temporary storage must be outside the output folder; change SHOTBOX_TEMP_DIR.")
    # Keep identical output names in different shots independent, while sharing
    # locks between worker processes using the same frontend cache.
    parent = os.path.normcase(str(target.parent.resolve()))
    key = hashlib.sha256(os.fsencode(parent)).hexdigest()
    return temporary / "export-locks" / key / f".{target.name}.shotbox-lock"


def _temporary_root() -> Path | None:
    override = os.environ.get("SHOTBOX_TEMP_DIR")
    return Path(override).expanduser().resolve() if override else None


def preview_output(source: Path, suffix: str = "_roto", existing: str = "version-up") -> Path:
    if existing not in ("version-up", "overwrite"):
        raise ValueError("Existing-folder behaviour must be 'version-up' or 'overwrite'.")
    source = Path(source).resolve()
    return _next_target(source, suffix) if existing == "version-up" else output_target(source, suffix)


def frame_filename(folder: Path, number: int, padding: int = 4) -> str:
    if not isinstance(number, int) or number < 0:
        raise ValueError("First output frame must be a non-negative integer.")
    if not isinstance(padding, int) or not 1 <= padding <= 8:
        raise ValueError("Frame-number padding must be between 1 and 8.")
    return f"{folder.name}.{number:0{padding}d}.exr"


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _move_file(source: Path, destination: Path) -> None:
    """Move an export or backup even when the cache is on another drive."""
    try:
        source.replace(destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    # The caller has reserved the destination and saved any previous contents.
    # Keep the source until copying succeeds, and discard incomplete copies.
    try:
        shutil.copy2(source, destination)
        source.unlink()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


@dataclass
class ExportSession:
    output_dir: Path
    stage_dir: Path
    lock_dir: Path | None
    existing: str
    new_folder: bool
    details: dict
    job_dir: Path
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def manifest_path(self) -> Path:
        return self.job_dir / MANIFEST

    @classmethod
    def reserve(cls, source: Path, suffix: str, existing: str, details: dict | None = None):
        source = Path(source).resolve()
        preview_output(source, suffix, existing)  # Validate before touching disk.
        lock = None
        new = False
        if existing == "version-up":
            while True:
                target = _next_target(source, suffix)
                lock = _lock_path(target)
                lock.parent.mkdir(parents=True, exist_ok=True)
                try:
                    lock.mkdir(exist_ok=False)
                except FileExistsError:
                    continue
                if target.exists() or target.is_symlink():
                    shutil.rmtree(lock)
                    continue
                # The lock reserves the name; generation writes only to staging.
                new = True
                break
        else:
            target = output_target(source, suffix)
            lock = _lock_path(target)
            lock.parent.mkdir(parents=True, exist_ok=True)
            try:
                lock.mkdir(exist_ok=False)
            except FileExistsError as exc:
                raise RuntimeError(f"Another overwrite job holds {lock}. If a previous process was forcibly stopped, inspect its partial output and remove this lock before retrying.") from exc
            try:
                if target.is_symlink():
                    raise ValueError(f"Refusing to overwrite a symlinked output folder: {target}")
                new = not target.exists()
                if not new and not target.is_dir():
                    raise ValueError(f"Output path is not a directory: {target}")
            except BaseException:
                shutil.rmtree(lock)
                raise
        job_id = uuid.uuid4().hex
        temporary = _temporary_root()
        stage = (temporary / "exports" / job_id if temporary else
                 target.with_name(f".shotbox-partial-{target.name}-{job_id}"))
        job_dir = job_storage_root() / job_id
        try:
            if job_dir.resolve().is_relative_to(target.resolve()):
                raise ValueError("Job storage must be outside the output folder; change SHOTBOX_JOB_DIR.")
            job_dir.mkdir(parents=True, exist_ok=False)
            stage.mkdir(parents=True)
            reserved_details = {**(details or {}), "output_is_provisional": False, "output_preview": str(target)}
            session = cls(target, stage, lock, existing, new, reserved_details, job_dir)
            session.mark("running")
            return session
        except BaseException:
            for empty in (stage, job_dir):
                if empty is not None:
                    try:
                        empty.rmdir()
                    except OSError:
                        pass
            if lock:
                shutil.rmtree(lock, ignore_errors=True)
            raise

    def mark(self, status: str, error: str | None = None, **extra) -> None:
        manifest = {**self.details, "status": status, "output_dir": str(self.output_dir),
                    "job_dir": str(self.job_dir), "stage_dir": str(self.stage_dir),
                    "lock_dir": str(self.lock_dir) if self.lock_dir else None, "pid": os.getpid(),
                    "updated_utc": datetime.now(timezone.utc).isoformat(), **extra}
        if error:
            manifest["error"] = error
        _write_json(self.manifest_path, manifest)

    def commit(self, names: list[str]) -> None:
        """Install only named EXRs; restore original results if any commit step fails."""
        if self._closed:
            raise RuntimeError("Export session is already closed.")
        pattern = re.compile(re.escape(self.output_dir.name) + r"\.\d+\.exr", re.IGNORECASE)
        backup = self.stage_dir / "backup"
        previous_export = self.job_dir / "previous-export"
        replaced: list[tuple[Path, Path]] = []
        installed: list[Path] = []
        created_output = False
        try:
            if not names or any(not isinstance(name, str) or not pattern.fullmatch(name) for name in names):
                raise ValueError("Commit requires explicitly named numbered EXRs belonging to this output sequence.")
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError("Duplicate EXR names in export commit.")
            payloads = [self.stage_dir / name for name in names]
            for prepared in payloads:
                if not prepared.is_file() or prepared.is_symlink():
                    raise ValueError(f"Missing or non-regular staged EXR: {prepared}")
            expected = {os.path.normcase(name) for name in names}
            self.mark("ready")
            backup.mkdir()
            self.mark("committing")
            if self.new_folder:
                # Recheck ownership at publication: an external process may have
                # created this path while inference was running. Never merge it.
                self.output_dir.mkdir(exist_ok=False)
                created_output = True
            elif self.output_dir.is_symlink() or not self.output_dir.is_dir():
                raise ValueError(f"Output folder changed before publication: {self.output_dir}")
            stale = [p for p in self.output_dir.iterdir()
                     if p.is_file() and pattern.fullmatch(p.name) and os.path.normcase(p.name) not in expected]
            for prepared in payloads:
                destination = self.output_dir / prepared.name
                if destination.exists() or destination.is_symlink():
                    if not destination.is_file() or destination.is_symlink():
                        raise ValueError(f"Cannot replace non-file output: {destination}")
                    previous = backup / prepared.name
                    _move_file(destination, previous)
                    replaced.append((destination, previous))
                _move_file(prepared, destination)
                installed.append(destination)
            # Only after all new images were successfully installed may old tail frames move.
            for previous in stale:
                saved = backup / previous.name
                _move_file(previous, saved)
                replaced.append((previous, saved))
            if self.existing == "overwrite":
                legacy = [self.output_dir / name for name in LEGACY_SIDECARS]
                legacy = [path for path in legacy if path.is_file() and not path.is_symlink()]
                if legacy:
                    previous_export.mkdir()
                for previous in legacy:
                    # Preserve legacy metadata separately from working backups.
                    shutil.copy2(previous, previous_export / previous.name)
                    saved = backup / previous.name
                    _move_file(previous, saved)
                    replaced.append((previous, saved))
            self.mark("complete", frames=names, frame_count=len(names))
        except BaseException as exc:
            for destination in reversed(installed):
                if destination.exists():
                    _move_file(destination, self.stage_dir / destination.name)
            for destination, saved in reversed(replaced):
                if saved.exists():
                    _move_file(saved, destination)
            if created_output:
                try:
                    self.output_dir.rmdir()
                except OSError:
                    pass  # Preserve any file added by another process.
            shutil.rmtree(previous_export, ignore_errors=True)
            try:
                self.mark("failed", str(exc))
            except Exception as manifest_error:
                exc.add_note(f"Could not update the job manifest: {manifest_error}")
            self.close()
            raise
        # Once the complete manifest is installed, cleanup cannot undo the successful export.
        shutil.rmtree(self.stage_dir, ignore_errors=True)
        self.close()

    def fail(self, error: BaseException, cancelled: bool = False) -> None:
        try:
            self.mark("cancelled" if cancelled else "failed", str(error))
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.lock_dir and self.lock_dir.exists():
            shutil.rmtree(self.lock_dir, ignore_errors=True)
