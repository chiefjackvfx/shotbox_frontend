from pathlib import Path
from dataclasses import dataclass
import re
import platform  # System and platform identification
import os
import subprocess
try:
    import DaVinciResolveScript as dvr_script  # DaVinci Resolve scripting API
except:pass
from PyQt6.QtWidgets import *  # UI widgets (buttons, tables, etc.)


@dataclass(frozen=True)
class ShotFileStateSnapshot:
    shot_dir: str
    nk_path: str | None = None
    nk_name: str | None = None
    nk_mtime: float | None = None
    render_path: str | None = None
    render_relpath: str | None = None
    render_display: str | None = None
    render_sequence_path: str | None = None
    render_dir: str | None = None
    render_type: str | None = None
    render_version: int | None = None
    render_mtime: float | None = None
    preview_path: str | None = None
    preview_name: str | None = None
    preview_version: int | None = None


class Folders:
    def __init__(self):
        pass

    def _shot_dir_path(self, folder) -> Path:
        return Path(self.convert_path(folder))

    def _latest_nk_in_shot_dir(self, shot_dir: Path):
        scripts = shot_dir / "scripts"
        files = list(scripts.glob("*.nk"))

        version_pattern = re.compile(r"_v(\d+)\.nk$", re.IGNORECASE)

        best_file = None
        highest_version = -1

        for file in files:
            match = version_pattern.search(file.name)
            if match:
                version = int(match.group(1))
                if version > highest_version:
                    highest_version = version
                    best_file = file

        return best_file

    def latest_nk(self, folder):
        return self._latest_nk_in_shot_dir(self._shot_dir_path(folder))
    def latest_render(self, folder):
        render_info = self.latest_render_info(folder)
        if not render_info:
            return "none", "No Render"
        return render_info.get("render_path", "none"), render_info.get("display_name", "No Render")

    def latest_render_info(self, folder):
        renders_dir = self._shot_dir_path(folder) / "renders" / "comp"

        if not renders_dir.exists():
            return None

        mov_info = self._latest_mov_render(renders_dir)
        exr_info = self._latest_exr_render(renders_dir)

        if mov_info and exr_info:
            mov_version = mov_info.get("version", -1)
            exr_version = exr_info.get("version", -1)
            return exr_info if exr_version >= mov_version else mov_info

        return mov_info or exr_info

    def _latest_mov_render(self, renders_dir: Path):
        files = list(renders_dir.glob("*.mov"))
        version_pattern = re.compile(r"_v(\d+)\.mov$", re.IGNORECASE)

        best_file = None
        highest_version = -1

        for file in files:
            match = version_pattern.search(file.name)
            if match:
                version = int(match.group(1))
                if version > highest_version:
                    highest_version = version
                    best_file = file

        if not best_file:
            return None

        try:
            mtime = best_file.stat().st_mtime
        except Exception:
            mtime = None

        return {
            "display_name": f"{best_file.name}",
            "render_path": str(best_file),
            "render_relpath": best_file.name,
            "mtime": mtime,
            "type": "mov",
            "version": highest_version,
        }

    def _latest_exr_render(self, renders_dir: Path):
        version_pattern = re.compile(r"_v(\d+)$", re.IGNORECASE)
        candidates = []

        for entry in renders_dir.iterdir():
            if not entry.is_dir():
                continue
            match = version_pattern.search(entry.name)
            if not match:
                continue
            candidates.append((int(match.group(1)), entry))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)

        for version, folder in candidates:
            seq_info = self._exr_sequence_info(renders_dir, folder, version)
            if seq_info:
                return seq_info

        return None

    def _exr_sequence_info(self, renders_dir: Path, folder: Path, version: int):
        version_token = rf"_v0*{version}"
        pattern = re.compile(version_token + r"[._-]?(\d+)\.exr$", re.IGNORECASE)
        fallback_pattern = re.compile(r"(\d+)\.exr$", re.IGNORECASE)
        first_frame_file = None
        first_frame_number = None
        last_frame_number = None
        first_frame_str = None
        latest_mtime = None
        frame_digits = None

        files = list(folder.glob("*.exr"))
        matches = []

        for file in files:
            match = pattern.search(file.name)
            if match:
                matches.append((file, match.group(1)))

        if not matches:
            for file in files:
                match = fallback_pattern.search(file.name)
                if match:
                    matches.append((file, match.group(1)))

        if not matches:
            return None

        for file, frame_str in matches:
            try:
                frame_num = int(frame_str)
            except ValueError:
                continue

            if first_frame_number is None or frame_num < first_frame_number:
                first_frame_number = frame_num
                first_frame_file = file
                first_frame_str = frame_str
            if last_frame_number is None or frame_num > last_frame_number:
                last_frame_number = frame_num

            if frame_digits is None or len(frame_str) > frame_digits:
                frame_digits = len(frame_str)

            try:
                mtime = file.stat().st_mtime
            except Exception:
                mtime = None

            if mtime is not None and (latest_mtime is None or mtime > latest_mtime):
                latest_mtime = mtime

        digits = frame_digits or 4
        if first_frame_str:
            sequence_name = re.sub(
                rf"{re.escape(first_frame_str)}(?=\.exr$)",
                "#" * len(first_frame_str),
                first_frame_file.name,
                flags=re.IGNORECASE,
            )
        else:
            sequence_name = f"{folder.name}_{'#' * digits}.exr"
        relpath = first_frame_file.relative_to(renders_dir).as_posix()

        frame_range = None
        if first_frame_number is not None and last_frame_number is not None:
            frame_range = f"{first_frame_number}-{last_frame_number}"

        display_name = f"{sequence_name} {frame_range}" if frame_range else sequence_name

        return {
            "display_name": display_name,
            "render_path": str(first_frame_file),
            "render_relpath": relpath,
            "sequence_path": str(folder / sequence_name),
            "render_dir": str(folder),
            "mtime": latest_mtime,
            "type": "exr",
            "version": version,
            "frame_range": frame_range,
        }

    def latest_preview(self, folder):
        """Find the highest version preview video in renders/precomp/previews."""
        previews_dir = self._shot_dir_path(folder) / "renders" / "precomp" / "previews"

        if not previews_dir.exists():
            return None, None

        files = list(previews_dir.glob("*.mp4"))

        try:
            candidates = []
            for file in files:
                version = self._preview_version_from_name(file.name)
                if version is None:
                    continue
                try:
                    modified_time = file.stat().st_mtime
                except Exception:
                    modified_time = 0.0
                candidates.append((version, modified_time, self._is_legacy_preview_name(file.name), file))

            if candidates:
                candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3].name.lower()))
                best_file = candidates[0][3]
                return str(best_file), str(best_file.name)
            return None, None
        except:
            return None, None

    def _preview_version_from_name(self, file_name: str):
        match = re.search(r"_v(\d+)(?:_preview)?\.mp4$", file_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def scan_shot_file_state(self, folder) -> ShotFileStateSnapshot:
        shot_dir = self._shot_dir_path(folder)
        latest_nk = self._latest_nk_in_shot_dir(shot_dir)
        render_info = self.latest_render_info(shot_dir)
        preview_path, preview_name = self.latest_preview(shot_dir)
        preview_version = (
            self._preview_version_from_name(preview_name) if preview_name else None
        )

        nk_path = str(latest_nk) if latest_nk else None
        nk_name = latest_nk.name if latest_nk else None
        try:
            nk_mtime = latest_nk.stat().st_mtime if latest_nk else None
        except Exception:
            nk_mtime = None

        return ShotFileStateSnapshot(
            shot_dir=str(shot_dir),
            nk_path=nk_path,
            nk_name=nk_name,
            nk_mtime=nk_mtime,
            render_path=render_info.get("render_path") if render_info else None,
            render_relpath=render_info.get("render_relpath") if render_info else None,
            render_display=render_info.get("display_name") if render_info else None,
            render_sequence_path=render_info.get("sequence_path") if render_info else None,
            render_dir=render_info.get("render_dir") if render_info else None,
            render_type=render_info.get("type") if render_info else None,
            render_version=render_info.get("version") if render_info else None,
            render_mtime=render_info.get("mtime") if render_info else None,
            preview_path=str(preview_path) if preview_path else None,
            preview_name=preview_name,
            preview_version=preview_version,
        )

    def _is_legacy_preview_name(self, file_name: str) -> bool:
        return file_name.lower().endswith("_preview.mp4")

    def conform(self, clip):
        print(clip)
        
    def push2dvr(self, clip, matte=False):
        """
        Push a clip to DaVinci Resolve media pool.
        
        Returns:
            bool: True if successful, False if failed
        """
        clip = self.convert_path(clip)
        shotname = str(os.path.basename(clip)[:6])
        print(f"clip: {clip}")
        
        if clip == "none":
            return False

        projectManager = None
        resolve = None
        
        try:
            resolve = dvr_script.scriptapp("Resolve")
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error connecting to Resolve:\n{e}\nIs Resolve open?")
            return False
        
        if not resolve:
            QMessageBox.critical(None, "Error", "Could not connect to DaVinci Resolve.\nIs Resolve open?")
            return False
            
        try:
            projectManager = resolve.GetProjectManager()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error finding Resolve project:\n{e}\nIs Resolve open?\nTurn on external scripting in preferences, general")
            return False
            
        if not projectManager:
            QMessageBox.critical(None, "Error", "Could not get Project Manager.\nIs Resolve open?")
            return False
            
        try:
            project = projectManager.GetCurrentProject()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error finding Resolve project:\n{e}\nIs Resolve open?\nTurn on external scripting in preferences, general")
            return False
            
        if not project:
            QMessageBox.critical(None, "Error", "No project is currently open in Resolve.")
            return False
            
        try:
            mediaStorage = resolve.GetMediaStorage()
            mediaPool = project.GetMediaPool()
            rootFolder = mediaPool.GetRootFolder()
            bins = rootFolder.GetSubFolderList()
            
            vfxBin = None
            for bin in bins:
                binName = bin.GetName()
                if binName == "vfx" or binName == "VFX":
                    vfxBin = bin
                    
            if vfxBin == None:
                vfxBin = mediaPool.AddSubFolder(rootFolder, "VFX")
                
            shotBin = vfxBin
            subbins = vfxBin.GetSubFolderList()
            shotBinDupe = False
            
            for bin in subbins:
                binName = bin.GetName()
                if binName == shotname:
                    shotbin1 = bin
                    shotBinDupe = True
                    
            if shotBinDupe:
                shotBin = shotbin1
            else:
                shotBin = mediaPool.AddSubFolder(vfxBin, shotname)

            mediaPool.SetCurrentFolder(shotBin)
            mediaPool.DeleteClips(clip)
            mediaClip = None
            if isinstance(clip, str):
                if os.path.isdir(clip):
                    mediaClip = mediaPool.ImportMedia(clip)
                elif "#" in clip or re.search(r"%0\d+d", clip):
                    mediaClip = mediaStorage.AddItemListToMediaPool([clip])
                    if not mediaClip and "#" in clip:
                        match = re.search(r"(#+)(?=\.exr$)", clip, re.IGNORECASE)
                        if match:
                            digits = len(match.group(1))
                            printf_path = re.sub(
                                r"(#+)(?=\.exr$)",
                                f"%0{digits}d",
                                clip,
                                flags=re.IGNORECASE,
                            )
                            mediaClip = mediaStorage.AddItemListToMediaPool([printf_path])

            if not mediaClip:
                mediaClip = mediaPool.ImportMedia(clip)

            if not mediaClip:
                QMessageBox.critical(None, "Error", f"Failed to import media:\n{clip}")
                return False

            for f in mediaClip:
                f.SetClipColor("Lime")
                
            if matte:
                listmatte = [matte]
                mediaStorage.AddClipMattesToMediaPool(mediaClip[0], listmatte)
            
            # Success!
            return True
            
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error importing to Resolve:\n{e}")
            return False
    

    def convert_path(self, file_path: str) -> str:
        """Convert between Linux/macOS and Windows project paths."""
        system = platform.system()
        file_path = str(file_path) if file_path is not None else ""
        if not file_path:
            return file_path

        def _normalize_slashes(path: str) -> str:
            return path.replace("\\", "/")

        def _pick_existing_root(candidates: list[str]) -> str:
            for candidate in candidates:
                try:
                    if os.path.exists(candidate):
                        return candidate
                except Exception:
                    continue
            return candidates[0] if candidates else ""

        normalized = _normalize_slashes(file_path)

        linux_roots = ["/mnt/projects/PROJECTS", "/Volumes/projects/PROJECTS"]
        windows_roots = ["Z:/PROJECTS"]

        if system == "Windows":
            for root in linux_roots:
                if normalized.startswith(root):
                    win_root = _pick_existing_root(windows_roots)
                    suffix = normalized[len(root):]
                    return os.path.normpath(win_root + suffix)
            return file_path

        if system in ("Linux", "Darwin"):
            posix_roots = ["/Volumes/projects/PROJECTS"] if system == "Darwin" else linux_roots
            for root in windows_roots:
                if normalized.lower().startswith(root.lower()):
                    posix_root = _pick_existing_root(posix_roots)
                    suffix = normalized[len(root):]
                    return os.path.normpath(posix_root + suffix)
            return file_path

        return file_path
    def openFileLocation(self, file):
        file = self.convert_path(file)
        if os.path.isfile(file):
            file = os.path.dirname(file)
            #print(file)
        if platform.system() == "Windows":
            os.startfile(file)
            #print(file)
        elif platform.system() == "Darwin":
            subprocess.call(["open", file])
        else:
            subprocess.call(["xdg-open", file])
            #print(file)

    def open_file(self, path):
        system = platform.system()
        #path = self.convert_path(path)
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Linux":
            #print(path)
            subprocess.Popen(["xdg-open", path])
        else:
            print(path)
            os.startfile(path)
