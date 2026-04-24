# page_xmlTools.py
"""
XML Tools Page for ShotBox - Consolidated version with all dependencies.
Parses XML timelines and creates Nuke shot folders/scripts.
"""

# Standard Library Imports
import os
import sys
import time
import re
import datetime
import shutil
import platform
import subprocess
import getpass
import urllib.parse
import xml.etree.ElementTree as ET

# PyQt6 Imports
from PyQt6 import uic
from PyQt6 import QtWidgets, QtGui
from PyQt6.QtGui import QFont, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QToolButton, QLabel,
    QLineEdit, QComboBox, QCheckBox, QSpinBox, QSlider, QMenu,
    QFileDialog, QMessageBox, QDialog, QProgressBar, QSpacerItem, QSizePolicy
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QThread, pyqtSignal, QUrl

# Third-Party Library Imports
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    print("opencv-python not installed. Thumbnail generation will be disabled. Run: pip install opencv-python")

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False
    print("pyperclip not installed. Clipboard functions will be disabled. Run: pip install pyperclip")

try:
    from PIL import Image as pillow_image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Pillow not installed. Some image functions will be disabled. Run: pip install Pillow")


# Get script directory for relative paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# =============================================================================
# Loading Dialog
# =============================================================================
class LoadingDialogXML(QDialog):
    """A dialog box with a progress bar to show the progress of a long-running task."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Loading")
        self.setFixedSize(800, 100)

        self.progressbar = QProgressBar(self)
        self.progressbar.setGeometry(10, 10, 790, 80)
        
        self.details = QLabel(self)
        self.details.setText("")
        
        layout = QVBoxLayout(self)
        layout.addWidget(self.progressbar)
        layout.addWidget(self.details)


# =============================================================================
# UI Tools Mixin
# =============================================================================
class UIToolsMixin:
    """UI utility functions mixed into other classes."""

    def __init__(self):
        self.ui_colour_orange = "f05a20"

    def load_stylesheet(self, filename):
        """Loads and applies a QSS stylesheet."""
        try:
            with open(filename, "r", encoding='utf-8') as f:
                self.setStyleSheet(f.read())
        except FileNotFoundError:
            print(f"Stylesheet '{filename}' not found.")

    def is_file_like(self, string):
        pattern = r'^[\w\-]+\.[\w\-]+(\.[\w\-]+)*$'
        return bool(re.match(pattern, string))

    def version_up(self, file):
        """Increment version number in filename."""
        if os.path.isfile(file) or self.is_file_like(file):
            base_dir = os.path.dirname(file)
            base_name = os.path.basename(file)
            file_name, file_ext = os.path.splitext(base_name)
        else:
            file_name = file
            file_ext = ""
            base_dir = ""

        version_pattern = r"(\w+)_v(\d+)(_+\w+)*"
        match = re.search(version_pattern, file_name)
        
        if match:
            file_name_prefix = match.group(1)
            version_padding = len(match.group(2))
            current_version = int(match.group(2))
            file_name_suffix = match.group(3) if match.group(3) else ""
        else:
            file_name_prefix = file_name
            current_version = 0
            version_padding = 2
            file_name_suffix = ""

        new_version = current_version + 1

        if os.path.isfile(file) or self.is_file_like(file):
            new_file_name = f"{file_name_prefix}_v{str(new_version).zfill(version_padding)}{file_name_suffix}{file_ext}"
            return os.path.join(base_dir, new_file_name)
        else:
            return f"{file_name_prefix}_v{str(new_version).zfill(version_padding)}{file_name_suffix}"

    def thumb_grab(self, file, outputfile, frame, index="v01", lut=None):
        """Grab a thumbnail frame from a video file."""
        if not HAS_CV2:
            print("OpenCV not available for thumbnail generation")
            return None
            
        frame = 1  # temp fix
        video = cv2.VideoCapture(file)
        video.set(cv2.CAP_PROP_POS_FRAMES, frame)
        success, thumb = video.read()

        if success:
            original_height, original_width = thumb.shape[:2]
            new_height = original_height // 2
            new_width = original_width // 2
            thumb = cv2.resize(thumb, (new_width, new_height))
            cv2.imwrite(outputfile, thumb, [cv2.IMWRITE_JPEG_QUALITY, 90])

            if lut is not None:
                thumb = cv2.imread(outputfile)
                lut_data = cv2.imread(lut)
                thumb = cv2.LUT(thumb, lut_data)
                cv2.imwrite(outputfile, thumb, [cv2.IMWRITE_JPEG_QUALITY, 90])

            return outputfile
        else:
            print("Failed to read frame from video.")
            return None

    def openFileLocation(self, file):
        """Open file location in system file browser."""
        if os.path.isfile(file):
            file = os.path.dirname(file)
        
        if platform.system() == "Windows":
            os.startfile(file)
        elif platform.system() == "Darwin":
            subprocess.call(["open", file])
        else:
            subprocess.call(["xdg-open", file])

    def clearlayout(self, layout):
        """Clear all widgets from a layout."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()


# =============================================================================
# Configure Mixin
# =============================================================================
class ConfigureMixin(UIToolsMixin):
    """Configuration and shot setup functions."""

    def __init__(self):
        super().__init__()
        self.defineDefaults()
        self.configPrime()

    def defineDefaults(self):
        """Set default configuration values."""
        self.activePage = "page_xmlTools"
        self.recentList = ["NONE"] * 5

        if platform.system() == "Darwin":
            self.configFile = os.path.join(SCRIPT_DIR, "jackapp_config_mac.txt")
        else:
            self.configFile = os.path.join(SCRIPT_DIR, "jackapp_config.txt")

        # Username mapping
        self.user = getpass.getuser()
        user_map = {"Huxley": "Dylan", "Clarke": "Jack", "rockybtw": "Jack", "grade": "Paul"}
        self.user = user_map.get(self.user, self.user)

        # Grid settings
        self.columnWidth = 160
        self.rowHeight = 90
        self.uiType = "table"

        self.debug = False
        self.debugZprojects = "J:/shotbox projects"
        self.localwork = False

        self.shotNumberPad = 3
        self.shoName = "sho"
        self.notePaths = []

        self.colourspaceList = [
            "Input - ARRI - V3 LogC (EI800) - Wide Gamut",
            "Input - ARRI - V4 LogC (EI800) - Wide Gamut4",
            "Input - Sony - Linear - Venice S-Gamut3.Cine",
            "Input - Sony - S-Log3 - Venice S-Gamut3.Cine",
            "Input - Canon - Curve - Canon-Log3",
            "Input - RED - REDLog3G10 - REDWideGamutRGB",
            "color_picking",
            "Output - Rec.709",
            "ACES - ACEScg"
        ]
        self.colourspace = self.colourspaceList[0]
        self.arriLUT = "Arri Alexa LogC to Rec709.cube"

        # Find projects
        self.searchZ = True
        self.allprojects = []
        
        if self.searchZ:
            if platform.system() == "Darwin":
                zProjects = r"/Volumes/projects/PROJECTS"
            elif platform.system() == "Windows":
                zProjects = self.debugZprojects if self.debug else r"Z:\PROJECTS"
            else:  # Linux
                zProjects = self.debugZprojects if self.debug else "/Volumes/projects/PROJECTS"

            if os.path.isdir(zProjects):
                for project in os.listdir(zProjects):
                    if project.startswith(".") or project.startswith("DiskSpeedTest") or project.endswith(".mp4"):
                        continue
                    project_path = os.path.join(zProjects, project)
                    if os.path.isdir(project_path):
                        for folder in os.listdir(project_path):
                            if folder.lower() == "nuke":
                                nukeFolder = os.path.join(project_path, folder)
                                if os.path.isdir(nukeFolder) and len(os.listdir(nukeFolder)) > 1:
                                    self.allprojects.append(project_path)
                                    break

        self.allprojects = sorted(self.allprojects, reverse=True)

    def configPrime(self):
        """Initialize config file."""
        if not os.path.isfile(self.configFile):
            try:
                with open(self.configFile, "w", encoding='utf-8') as f:
                    for _ in range(5):
                        f.write("NONE\n")
            except:
                pass

        try:
            with open(self.configFile, "r", encoding='utf-8') as f:
                self.configContent = f.readlines()
        except:
            self.configContent = []

    def splitall(self, path):
        """Split path into all components."""
        allparts = []
        while True:
            parts = os.path.split(path)
            if parts[0] == path:
                allparts.insert(0, parts[0])
                break
            elif parts[1] == path:
                allparts.insert(0, parts[1])
                break
            else:
                path = parts[0]
                allparts.insert(0, parts[1])
        return allparts

    def shot_number(self, number):
        """Generate shot number string."""
        if self.shotNumberPad == 3:
            if len(str(number)) == 1:
                return f"{self.shoName}0{number}0"
            else:
                return f"{self.shoName}{number}0"
        elif self.shotNumberPad == 4:
            if len(str(number)) == 1:
                return f"{self.shoName}00{number}0"
            elif len(str(number)) == 2:
                return f"{self.shoName}0{number}0"
            else:
                return f"{self.shoName}{number}0"
        return f"{self.shoName}{number}"

    def movDuration(self, mov, format="frames", fps=25):
        """Get duration of video file."""
        if not HAS_CV2:
            return 100
        try:
            cap = cv2.VideoCapture(mov)
            if not cap.isOpened():
                return 100
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            video_fps = cap.get(cv2.CAP_PROP_FPS)
            cap.release()

            if format == "frames":
                return frame_count
            elif format == "seconds":
                return frame_count / video_fps if video_fps > 0 else 100
        except:
            return 100

    def movFormat(self, mov, type="str"):
        """Get format/resolution of video file."""
        if not HAS_CV2:
            return "1920 1080"
        try:
            cap = cv2.VideoCapture(mov)
            if not cap.isOpened():
                return "1920 1080"
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            return f"{width} {height}"
        except:
            return "1920 1080"

    def resolve_xml_clip_duration(self, clip_name, duration_text, start_frame_text, end_frame_text, handles):
        """Resolve XML cut duration from timing fields without inflating it for handles."""
        xml_duration = int(duration_text) if duration_text not in (None, "") else 0
        start_frame = int(start_frame_text)
        end_frame = int(end_frame_text)
        inclusive_duration = (end_frame - start_frame) + 1

        if xml_duration > 0:
            if inclusive_duration > 0 and xml_duration != inclusive_duration:
                print(
                    f"[XMLTools] Duration mismatch for {clip_name}: "
                    f"duration={xml_duration}, inclusive_range={inclusive_duration}. "
                    "Using XML duration."
                )
            base_duration = xml_duration
        elif inclusive_duration > 0:
            base_duration = inclusive_duration
        else:
            print(
                f"[XMLTools] Invalid timing for {clip_name}: "
                f"duration={xml_duration}, start={start_frame}, end={end_frame}. "
                "Defaulting base duration to 1."
            )
            base_duration = 1

        return base_duration

    def folderSetup(self, shotIndex, read_paths, thumb, duration, single=False):
        """Set up folder structure and Nuke script for a shot."""
        print(f"Making shot {shotIndex}: {read_paths}, Duration: {duration}")
        
        # XML imports pass the handled shot length in; only fall back to the
        # source clip duration when that value is missing.
        if not single and not duration:
            duration = self.movDuration(read_paths[0])

        last_frame = duration + 1000
        
        try:
            startframe = self.v1StartFrames[shotIndex - 1]
            endframe = self.v1EndFrames[shotIndex - 1]
        except:
            startframe = 1001
            endframe = 1010

        # Build timestamp info
        now = datetime.datetime.now()
        dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
        timestamp = f"script was auto generated by {self.user} on: {dt_string}\n"
        timestamp += f"OG clip names: {read_paths}\n"
        timestamp += f"duration: {duration}\n"
        timestamp += f"edit start frame: {startframe}"

        shotNumber = self.shot_number(shotIndex + 1)
        trimmed_read_paths = []

        if single:
            self.shotNumberPad = 3
            workFolder = self.singleshotLoccation
            nukeFolder = os.path.join(self.singleshotLoccation, self.singleshoNametextbox.text())
            shotNumber = self.singleshoNametextbox.text()
            
            # Convert single path to list if needed
            if isinstance(read_paths, str):
                read_paths = [read_paths]
            
            if self.localwork:
                scanFolder = os.path.join(nukeFolder, "scans")
                if not os.path.isdir(nukeFolder):
                    os.mkdir(nukeFolder)
                if not os.path.isdir(scanFolder):
                    os.mkdir(scanFolder)
                for path in read_paths:
                    copied = shutil.copy2(path, scanFolder)
                    newpath = "../scans/" + os.path.basename(copied)
                    trimmed_read_paths.append(newpath)
            else:
                for path in read_paths:
                    parts = self.splitall(path)
                    pathstrip = "/".join(parts[3:])
                    newpath = "../../../../" + pathstrip
                    trimmed_read_paths.append(newpath)
        else:
            nuke_root = os.path.join(self.projectRoot, "Nuke")
            if not os.path.isdir(nuke_root):
                os.mkdir(nuke_root)
                os.mkdir(os.path.join(nuke_root, "assets"))
            
            workFolder = os.path.join(nuke_root, self.editname)
            nukeFolder = os.path.join(workFolder, shotNumber)
            
            if self.localwork:
                if not os.path.isdir(nukeFolder):
                    os.mkdir(nukeFolder)
                for index, path in enumerate(read_paths):
                    scanFolder = os.path.join(nukeFolder, "scans")
                    if not os.path.isdir(scanFolder):
                        os.mkdir(scanFolder)
                    path = shutil.copy(path, scanFolder)
                    newpath = "../scans/" + os.path.basename(path)
                    trimmed_read_paths.append(newpath)
            else:
                for index, path in enumerate(read_paths):
                    parts = self.splitall(path)
                    if platform.system() == "Linux":
                        pathstrip = "/".join(parts[5:])
                    else:
                        pathstrip = "/".join(parts[3:])
                    newpath = "../../../../" + pathstrip
                    trimmed_read_paths.append(newpath)

        # Create folder structure
        rendersFolder = os.path.join(nukeFolder, "renders")
        rendersCompFolder = os.path.join(rendersFolder, "comp")
        rendersPrecompFolder = os.path.join(rendersFolder, "precomp")
        scriptsFolder = os.path.join(nukeFolder, "scripts")
        assetsFolder = os.path.join(nukeFolder, "assets")

        notesFileName = shotNumber + ".txt"
        notesFilepath = os.path.join(nukeFolder, notesFileName)

        if not os.path.isdir(workFolder):
            os.mkdir(workFolder)
        
        if os.path.isdir(nukeFolder):
            print(f"{nukeFolder} folder already exists")
        else:
            try:
                os.mkdir(nukeFolder)
            except:
                pass
            os.mkdir(rendersFolder)
            os.mkdir(rendersCompFolder)
            os.mkdir(rendersPrecompFolder)
            os.mkdir(scriptsFolder)
            os.mkdir(assetsFolder)

            # Write notes file
            with open(notesFilepath, "w", encoding='utf-8') as writeNotes:
                writeNotes.write(f"{shotNumber}\n")
                writeNotes.write("Latest conform: None\n")
                writeNotes.write("Notes: type any notes or shot brief here\n")
                writeNotes.write("OG clip names: ")
                writeNotes.write(" ".join([os.path.basename(i) for i in read_paths]))
                writeNotes.write(f"\nduration: {duration}\n")
                writeNotes.write(f"edit start frame: {startframe}\n")

        # Copy thumbnail
        try:
            folderThumb = os.path.join(nukeFolder, f"{shotNumber}_thumb_v01.jpeg")
            if thumb and os.path.isfile(thumb):
                shutil.copyfile(thumb, folderThumb)
        except:
            print("Failed to copy thumbnail")

        # Create Nuke script
        format_str = self.movFormat(read_paths[0])
        nukeScript = os.path.join(scriptsFolder, f"{shotNumber}_v01.nk")

        if self.localwork:
            if single:
                read_paths = "../scans/" + os.path.basename(read_paths[0])
            else:
                read_paths = ["../scans/" + os.path.basename(r) for r in read_paths]

        # Read template and perform replacements
        template_path = os.path.join(SCRIPT_DIR, "template_v06.nk")
        if os.path.isfile(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                contents = f.read()

            contents = contents.replace("find&replace fps", "25")
            contents = contents.replace("find&replace duration", str(duration))
            contents = contents.replace("find&replace last_frame", str(last_frame))
            contents = contents.replace("find&replace format", format_str)
            contents = contents.replace("find&replace info", timestamp)
            contents = contents.replace("find&replace read1_path", trimmed_read_paths[0] if trimmed_read_paths else "")
            contents = contents.replace("find&replace colourspace", self.colourspace)
            contents = contents.replace("find&replace shotname", shotNumber)

            with open(nukeScript, "w", encoding='utf-8') as outputFile:
                outputFile.write(contents)
                
                # Add additional read nodes if multiple sources
                if len(trimmed_read_paths) > 1:
                    offset = 280
                    for read_index, read in enumerate(trimmed_read_paths[1:], start=2):
                        outputFile.write(self.readNode(read_index, read, duration, offset))
                        offset += 100
                
                # Add offline ref if present
                try:
                    if hasattr(self, 'offlineRef_file') and self.offlineRef_file and os.path.isfile(self.offlineRef_file):
                        print("REF Clip found")
                        outputFile.write(self.readRef(self.offlineRef_file, duration, startframe, endframe))
                except:
                    pass

            print(f"Created Nuke script: {nukeScript}")
        else:
            print(f"Template not found: {template_path}")
            # Create a basic script without template
            with open(nukeScript, "w", encoding='utf-8') as outputFile:
                outputFile.write(f"# {shotNumber} - Auto-generated\n")
                outputFile.write(f"# Duration: {duration}\n")
                outputFile.write(f"# {timestamp}\n")
                for i, path in enumerate(trimmed_read_paths):
                    outputFile.write(self.readNode(i + 1, path, duration, i * 100))
            print(f"Created basic Nuke script (no template): {nukeScript}")

        return nukeFolder


# =============================================================================
# Shot Setup Drop Widget
# =============================================================================
class ShotSetupDrop(QWidget, ConfigureMixin):
    """Drag and drop widget for setting up single shots."""
    
    def __init__(self):
        QWidget.__init__(self)
        ConfigureMixin.__init__(self)
        
        self.setWindowTitle("Set up a single shot for Nuke")
        self.setGeometry(0, 0, 800, 300)
        self.setAcceptDrops(True)
        self.initUI()
        self.movselection = "None"
        self.singleshotLoccation = "None"

    def make_shot_check(self):
        shotlocation = os.path.join(self.singleshotLoccation, self.singleshoNametextbox.text())
        if os.path.isfile(self.movselection) and os.path.isdir(self.singleshotLoccation):
            if os.path.isdir(shotlocation):
                QMessageBox.about(self, "ERROR", f"{shotlocation} already exists. Use a different name.")
            else:
                self.makeshot()
        else:
            QMessageBox.about(self, "ERROR", f"{self.movselection} or {self.singleshotLoccation} does not exist.")

    def initUI(self):
        self.label1 = QLabel("Drag and drop a .mov here")
        self.label1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label1.setStyleSheet("background-color: #F0F0F0; color: #333333; font-size: 16px; padding: 10px; border: 1px solid #CCCCCC;")
        self.label1.setMinimumHeight(150)

        self.label2 = QLabel("Drag and drop the folder here for Nuke folder to be created")
        self.label2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label2.setStyleSheet("background-color: #F0F0F0; color: #333333; font-size: 16px; padding: 10px; border: 1px solid #CCCCCC;")
        self.label2.setMinimumHeight(150)

        self.singleshoNametextbox = QLineEdit(self)
        self.singleshoNametextbox.setText("sho010")
        self.singleshoNametextbox.setMaxLength(7)
        self.singleshoNametextbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btnMake_Shot = QToolButton(self)
        self.btnMake_Shot.setText("Make shot")
        self.btnMake_Shot.clicked.connect(self.make_shot_check)
        self.btnMake_Shot.setStyleSheet(f"background-color: #{self.ui_colour_orange}; color: #FFFFFF; font-size: 16px; padding: 10px; border-radius: 5px;")

        hbox = QHBoxLayout()
        hbox.addWidget(self.singleshoNametextbox)
        hbox.addWidget(self.btnMake_Shot)

        layout = QVBoxLayout()
        layout.addStretch(1)
        layout.addWidget(self.label1)
        layout.addWidget(self.label2)
        layout.addLayout(hbox)
        layout.addStretch(1)
        layout.setSpacing(20)
        layout.setContentsMargins(50, 50, 50, 50)
        self.setLayout(layout)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        url = event.mimeData().urls()[0]
        file_path = url.toLocalFile()
        file_name = os.path.basename(file_path)

        drop_position = event.position().toPoint()
        if self.label1.geometry().contains(drop_position):
            self.label1.setText(f"File name: {file_name}\nFile path: {file_path}")
            self.movselection = file_path
        elif self.label2.geometry().contains(drop_position):
            self.label2.setText(f"File name: {file_name}\nFile path: {file_path}")
            self.singleshotLoccation = file_path

    def makeshot(self):
        """Create a single shot from dropped files."""
        thumblocation = os.path.join(
            os.path.dirname(self.movselection),
            f"{self.singleshoNametextbox.text()}_thumb_v01.jpeg"
        )
        thumb = self.thumb_grab(self.movselection, thumblocation, 25)
        duration = self.movDuration(self.movselection)
        
        # Call the full folderSetup
        try:
            result = self.folderSetup(
                shotIndex=1,
                read_paths=self.movselection,
                thumb=thumb,
                duration=duration,
                single=True
            )
            QMessageBox.information(self, "Shot Created", f"Shot setup complete!\n\nFolder: {result}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to create shot:\n{str(e)}")


# =============================================================================
# Worker Thread for Batch Thumbnail Generation
# =============================================================================
class WorkerThread(QThread, UIToolsMixin):
    """Background worker for batch thumbnail generation."""
    
    progress = pyqtSignal(int)
    task = pyqtSignal(str)
    done = pyqtSignal()

    def __init__(self, nameGroup, fileGroup, thumbGroup):
        QThread.__init__(self)
        UIToolsMixin.__init__(self)
        self.nameGroup = nameGroup
        self.fileGroup = fileGroup
        self.thumbGroup = thumbGroup

    def run(self):
        total_files = sum(len(layer) for layer in self.nameGroup)
        completed_files = 0

        for layer_index, layer in enumerate(self.nameGroup):
            for clip_index, clip in enumerate(layer):
                grabOffset = "00:00:01.00"

                if len(str(clip_index)) == 1:
                    grabIndex = f"v{layer_index + 1}-0{clip_index + 1}"
                else:
                    grabIndex = f"v{layer_index + 1}-{clip_index + 1}"

                grabFile = self.fileGroup[layer_index][clip_index]

                if grabFile.startswith("file"):
                    grabFile = grabFile[17:]

                head_tail = os.path.split(grabFile)
                grabFilemod = os.path.join(
                    head_tail[0],
                    f"v{layer_index + 1}_thumbs",
                    f"{os.path.splitext(head_tail[1])[0]}_{clip_index + 1}_thumb_v01.jpeg"
                )

                makepath = os.path.dirname(grabFilemod)
                if not os.path.isdir(makepath):
                    try:
                        os.mkdir(makepath)
                    except:
                        pass

                if grabFile == "offline":
                    self.thumbGroup[layer_index].append("offline")
                    current_task = "offline"
                elif not os.path.isfile(grabFilemod):
                    result = self.thumb_grab(grabFile, grabFilemod, grabOffset, grabIndex)
                    self.thumbGroup[layer_index].append(result)
                    current_task = os.path.basename(grabFilemod)
                else:
                    self.thumbGroup[layer_index].append(grabFilemod)
                    current_task = os.path.basename(grabFilemod)

                completed_files += 1
                progress_percent = int(completed_files / total_files * 100) if total_files > 0 else 100
                self.progress.emit(progress_percent)
                self.task.emit(str(current_task))

        self.done.emit()


# =============================================================================
# Main XML Tools Page
# =============================================================================
class page_xmlTools(QWidget, ConfigureMixin):
    """Main XML Tools page for parsing timelines and creating Nuke shots."""
    
    def __init__(self):
        QWidget.__init__(self)
        ConfigureMixin.__init__(self)
        
        self.shot_setup_drop = ShotSetupDrop()
        self.activePage = "page_xmlTools"

        # Try to load UI file, fallback to programmatic UI
        ui_path = os.path.join(SCRIPT_DIR, "ui", "Nuke_setup_v01.ui")
        if os.path.exists(ui_path):
            uic.loadUi(ui_path, self)
        else:
            self._setup_fallback_ui()

        # Try to load stylesheet
        qss_path = os.path.join(SCRIPT_DIR, "styles.qss")
        if os.path.exists(qss_path):
            self.load_stylesheet(qss_path)

        self.ui_setup()

        self.thumbpos = 1
        self.nameGroup = []
        self.fileGroup = []
        self.thumbGroup = []

    def _setup_fallback_ui(self):
        """Create UI programmatically if .ui file not found."""
        self.gridLayout = QVBoxLayout(self)
        self.setLayout(self.gridLayout)

    def ui_setup(self):
        """Set up the UI elements."""
        self.horizontalSpacer01 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        # Create main layout if not from .ui file
        if not hasattr(self, 'gridLayout'):
            self.gridLayout = QVBoxLayout(self)

        # Row 2 - Shot name settings
        self.layout01 = QHBoxLayout()

        self.shonameLable = QLabel(self)
        self.shonameLable.setText("Shot name:")
        self.shonameLable.setToolTip("Set the code name of the shot using 3 characters only.")

        self.shoNametextbox = QLineEdit(self)
        self.shoNametextbox.setText(self.shoName)
        self.shoNametextbox.setMaxLength(3)

        self.shotNumberPadLable = QLabel(self)
        self.shotNumberPadLable.setText("Shot number padding:")

        self.shotNumberPadSpinbox = QSpinBox(self)
        self.shotNumberPadSpinbox.setMinimum(3)
        self.shotNumberPadSpinbox.setMaximum(4)

        self.updateBtn = QPushButton('Update Settings', self)
        self.updateBtn.clicked.connect(self.updateSettings)

        self.layout01.addWidget(self.shonameLable)
        self.layout01.addWidget(self.shoNametextbox)
        self.layout01.addWidget(self.shotNumberPadLable)
        self.layout01.addWidget(self.shotNumberPadSpinbox)
        self.layout01.addItem(self.horizontalSpacer01)
        self.layout01.addWidget(self.updateBtn)

        self.gridLayout.addLayout(self.layout01)

        # Row 3 - Colourspace
        self.layout03 = QHBoxLayout()

        self.colourspaceLable = QLabel(self)
        self.colourspaceLable.setText("Colour space:")

        self.colourspaceComboBox = QComboBox(self)
        self.colourspaceComboBox.setEditable(True)
        self.colourspaceComboBox.setInsertPolicy(QComboBox.InsertPolicy.InsertAtBottom)
        for cs in self.colourspaceList:
            self.colourspaceComboBox.addItem(cs)

        self.layout03.addWidget(self.colourspaceLable)
        self.layout03.addWidget(self.colourspaceComboBox)
        self.layout03.addItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.gridLayout.addLayout(self.layout03)

        # Row 4 - Project root
        self.layout04 = QHBoxLayout()

        self.btnSelecRoot = QToolButton(self)
        self.btnSelecRoot.setText("Project Root Path")
        self.btnSelecRoot.setDisabled(True)
        self.btnSelecRoot.clicked.connect(self.selectRoot)

        self.lableRoot = QLabel(self)
        self.lableRoot.setText("Project root: None")

        self.checkBoxlocalwork = QCheckBox()
        self.checkBoxlocalwork.setText("Local work")
        self.checkBoxlocalwork.setToolTip("When checked, shots will be copied to shot folder.")

        self.lablehandle = QLabel(self)
        self.lablehandle.setText("Set handles:")

        self.spinboxHandles = QSpinBox(self)
        self.spinboxHandles.setValue(25)

        self.layout04.addWidget(self.btnSelecRoot)
        self.layout04.addWidget(self.lableRoot)
        self.layout04.addWidget(self.checkBoxlocalwork)
        self.layout04.addWidget(self.lablehandle)
        self.layout04.addWidget(self.spinboxHandles)
        self.layout04.addItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.gridLayout.addLayout(self.layout04)

        # Row 5 - Buttons
        self.layout05 = QHBoxLayout()

        self.btnSelectXML = QToolButton(self)
        self.btnSelectXML.setText("Select XML")
        self.btnSelectXML.clicked.connect(self.selectXML)

        self.btnMakeThumbs = QPushButton(self)
        self.btnMakeThumbs.setText("Make Thumbs")
        self.btnMakeThumbs.setDisabled(True)
        self.btnMakeThumbs.clicked.connect(self.runbatch)

        self.btnShotTable = QToolButton(self)
        self.btnShotTable.setText("Shot Table")
        self.btnShotTable.setDisabled(True)
        self.btnShotTable.clicked.connect(self.shotTable)

        self.btnNukeAll = QPushButton(self)
        self.btnNukeAll.setText("Nuke All")
        self.btnNukeAll.setDisabled(True)
        self.btnNukeAll.clicked.connect(self.nukeAll)

        self.btnMakeShot = QToolButton(self)
        self.btnMakeShot.setText("Make Single Shot")
        self.btnMakeShot.clicked.connect(lambda: self.shot_setup_drop.show())

        self.layout05.addWidget(self.btnSelectXML)
        self.layout05.addWidget(self.btnMakeThumbs)
        self.layout05.addWidget(self.btnShotTable)
        self.layout05.addWidget(self.btnNukeAll)
        self.layout05.addWidget(self.btnMakeShot)
        self.layout05.addItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.gridLayout.addLayout(self.layout05)

        # Row 6 - Table
        self.layout06 = QHBoxLayout()

        self.tableWidgetTemp = QTableWidget(self)
        self.tableWidgetTemp.setRowCount(100)
        self.tableWidgetTemp.setColumnCount(6)
        self.tableWidgetTemp.setHorizontalHeaderLabels(
            ['Shot Name', 'V1 Thumb', 'V2 Thumb', 'Duration', 'V1 Clip Name', 'V2 Clip Name']
        )

        self.layout06.addWidget(self.tableWidgetTemp)
        self.gridLayout.addLayout(self.layout06)

    def updateSettings(self):
        """Update settings from UI inputs."""
        self.shotNumberPad = int(self.shotNumberPadSpinbox.text())
        self.shoName = self.shoNametextbox.text()
        self.colourspace = self.colourspaceComboBox.currentText()
        if self.checkBoxlocalwork.isChecked():
            self.localwork = True
        print(f"Settings updated: {self.shoName}, pad={self.shotNumberPad}, colourspace={self.colourspace}")

    def selectRoot(self):
        """Select project root directory."""
        file = QFileDialog.getExistingDirectory(self, "Select project root")
        if file:
            self.projectRoot = file
            self.lableRoot.setText(f"Project root: <{self.projectRoot}>")

    def selectXML(self):
        """Select and parse XML file."""
        file, _ = QFileDialog.getOpenFileName(
            self, "Select XML File", "", "XML Files (*.xml)"
        )
        
        if not file:
            return
            
        self.XMLfile = file
        self.editname = os.path.basename(file).replace(" ", "_")[:-4]

        if os.path.isfile(file):
            self.btnShotTable.setDisabled(False)
            self.btnMakeThumbs.setDisabled(False)
            self.btnNukeAll.setDisabled(False)
            self.xmlParse()
        else:
            QMessageBox.about(self, "ERROR", "You need to select a valid XML")

    def xmlParse(self):
        """Parse the selected XML file."""
        self.btnSelecRoot.setDisabled(False)

        mytree = ET.parse(self.XMLfile)
        myroot = mytree.getroot()

        # Set project root from XML path
        xmlSplit = self.XMLfile.split("/")
        if platform.system() == "Linux":
            self.projectRoot = "/".join(xmlSplit[:5])
        else:
            self.projectRoot = "/".join(xmlSplit[:3])

        self.lableRoot.setText(f"Project root: <{self.projectRoot}>")

        # Initialize layer data
        track = 0
        self.offlineRef_file = None
        self.edit_start_frame = []

        # Initialize lists for each video track (up to 5)
        for i in range(1, 6):
            setattr(self, f'v{i}Thumbs', [])
            setattr(self, f'v{i}ClipNames', [])
            setattr(self, f'v{i}InPoints', [])
            setattr(self, f'v{i}OutPoints', [])
            setattr(self, f'v{i}FilePaths', [])
            setattr(self, f'v{i}Durations', [])
            setattr(self, f'v{i}StartFrames', [])
            setattr(self, f'v{i}EndFrames', [])

        # Parse XML
        for sequence in myroot.findall("sequence"):
            for media in sequence.findall("media"):
                for video in media.findall("video"):
                    for VideoTrack in video.findall("track"):
                        track += 1
                        for clip in VideoTrack.findall("clipitem"):
                            name = clip.find("name").text

                            filepath = "offline"
                            for file in clip.findall("file"):
                                try:
                                    filepath = str(file.find("pathurl").text)
                                    filepath = urllib.parse.unquote(filepath)
                                    
                                    if filepath.startswith("file://localhost"):
                                        filepath = filepath[17:]
                                    elif filepath.startswith("file:///Volumes/projects/PROJECTS"):
                                        filepath = "Z:/PROJECTS" + filepath.split("file:///Volumes/projects/PROJECTS")[1]
                                    elif filepath.startswith("file://"):
                                        filepath = filepath[7:]
                                    
                                    if platform.system() == "Linux" and filepath.startswith("Z:/PROJECTS"):
                                        filepath = "/Volumes/projects/PROJECTS" + filepath.split("Z:/PROJECTS")[1]
                                except:
                                    filepath = "offline"

                            inPoint = clip.find("in").text
                            if inPoint == "90000":
                                inPoint = 0

                            outPoint = clip.find("out").text
                            duration = clip.find("duration").text
                            startFrame = clip.find("start").text
                            endFrame = clip.find("end").text

                            trueDuration = self.resolve_xml_clip_duration(
                                name,
                                duration,
                                startFrame,
                                endFrame,
                                self.spinboxHandles.value(),
                            )

                            # Store in appropriate track list
                            if 1 <= track <= 5:
                                getattr(self, f'v{track}ClipNames').append(name)
                                getattr(self, f'v{track}FilePaths').append(filepath)
                                getattr(self, f'v{track}InPoints').append(inPoint)
                                getattr(self, f'v{track}OutPoints').append(outPoint)
                                getattr(self, f'v{track}Durations').append(trueDuration)
                                getattr(self, f'v{track}StartFrames').append(startFrame)
                                getattr(self, f'v{track}EndFrames').append(endFrame)

        # Update groups for thumbnail generation - all 5 layers as tuples
        self.nameGroup = (self.v1ClipNames, self.v2ClipNames, self.v3ClipNames, self.v4ClipNames, self.v5ClipNames)
        self.fileGroup = (self.v1FilePaths, self.v2FilePaths, self.v3FilePaths, self.v4FilePaths, self.v5FilePaths)
        self.thumbGroup = (self.v1Thumbs, self.v2Thumbs, self.v3Thumbs, self.v4Thumbs, self.v5Thumbs)
        self.inGroup = (self.v1InPoints, self.v2InPoints, self.v3InPoints, self.v4InPoints, self.v5InPoints)
        self.startGroup = (self.v1StartFrames, self.v2StartFrames, self.v3StartFrames, self.v4StartFrames, self.v5StartFrames)

        # Print debug info
        print(f"Parsed {len(self.v1ClipNames)} clips from V1")
        if self.v2ClipNames:
            print(f"Parsed {len(self.v2ClipNames)} clips from V2")
        if self.v3ClipNames:
            print(f"Parsed {len(self.v3ClipNames)} clips from V3")
        if self.v4ClipNames:
            print(f"Parsed {len(self.v4ClipNames)} clips from V4")
        if self.v5ClipNames:
            print(f"Parsed {len(self.v5ClipNames)} clips from V5")

    def linkClips(self):
        """Link clips from V2-V5 to V1 based on matching start frames."""
        self.linkedFiles = []
        self.linkedThumbs = []
        
        # First, add all V1 clips
        for index, v1clip in enumerate(self.v1FilePaths):
            self.linkedFiles.append([v1clip])
            
            if len(self.v1Thumbs) > 0 and index < len(self.v1Thumbs):
                self.linkedThumbs.append([self.v1Thumbs[index]])
            else:
                self.linkedThumbs.append([])
        
        # Now link clips from V2-V5 based on matching start frames
        for layer_index, layer in enumerate(self.fileGroup[1:], start=1):  # Skip V1
            thumbs = self.thumbGroup[layer_index]
            starts = self.startGroup[layer_index]
            
            for clip_index, clip in enumerate(layer):
                start = starts[clip_index]
                thumb = thumbs[clip_index] if clip_index < len(thumbs) else None
                
                # Find matching V1 clip by start frame
                for v1_index, v1_start in enumerate(self.v1StartFrames):
                    if v1_start == start:
                        self.linkedFiles[v1_index].append(clip)
                        if thumb:
                            self.linkedThumbs[v1_index].append(thumb)
                        break
        
        print(f"Linked {len(self.linkedFiles)} shots")
        for i, linked in enumerate(self.linkedFiles[:5]):  # Print first 5 for debug
            print(f"  Shot {i+1}: {len(linked)} clips")

    def getimagelable(self, image):
        """Create a QLabel with a thumbnail image."""
        pic = QtGui.QPixmap(image)
        imagelable = QLabel(self)
        imagelable.setScaledContents(True)
        imagelable.setPixmap(pic)
        imagelable.setObjectName(image)  # Store path for context menu
        return imagelable

    def getinfolable(self, info):
        """Create a QLabel with text info."""
        infolable = QLabel()
        infolable.setText(str(info))
        infolable.setWordWrap(True)
        return infolable

    def getshotBtn(self, shotIndex, read_paths, thumb, duration):
        """Create a 'Set up' button for a shot row."""
        shotIndexBtn = QPushButton(self)
        shotIndexBtn.setText("Set up")
        shotIndexBtn.clicked.connect(lambda: self.folderSetup(shotIndex, read_paths, thumb, duration))
        shotIndexBtn.clicked.connect(lambda: print(f"Set up shot {shotIndex + 1}"))
        shotIndexBtn.clicked.connect(lambda: shotIndexBtn.setStyleSheet("background-color: green"))
        return shotIndexBtn

    def shotTable(self):
        """Populate the table with parsed shot data, thumbnails, and buttons."""
        # Link clips from all layers first
        self.linkClips()
        self.updateSettings()
        self.btnShots = []

        # Create a new table widget
        self.tableWidget = QTableWidget(self)
        self.tableWidget.setColumnCount(9)
        self.tableWidget.setHorizontalHeaderLabels([
            'Shot Name', 'V1\nThumb', 'V2\nThumb', 'Duration', 
            'V1\nClip Name', 'V2\nClip Name', 'Set Up', 'Layers', 'Status'
        ])
        self.tableWidget.setRowCount(len(self.v1ClipNames))
        self.tableWidget.verticalHeader().setDefaultSectionSize(90)
        self.tableWidget.horizontalHeader().setDefaultSectionSize(160)
        
        # Set specific column widths
        self.tableWidget.setColumnWidth(0, 80)   # Shot Name
        self.tableWidget.setColumnWidth(1, 160)  # V1 Thumb
        self.tableWidget.setColumnWidth(2, 160)  # V2 Thumb
        self.tableWidget.setColumnWidth(3, 70)   # Duration
        self.tableWidget.setColumnWidth(6, 80)   # Set Up button
        self.tableWidget.setColumnWidth(7, 50)   # Layers count
        self.tableWidget.setColumnWidth(8, 80)   # Status

        # Replace the existing table with the new one
        for i in reversed(range(self.layout06.count())):
            widget = self.layout06.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.layout06.addWidget(self.tableWidget)

        # Populate the table with data
        for row in range(len(self.v1ClipNames)):
            v1FilePath = self.v1FilePaths[row]
            v1Thumb = self.v1Thumbs[row] if row < len(self.v1Thumbs) else None
            v1Duration = self.v1Durations[row]
            linkedFile = self.linkedFiles[row] if row < len(self.linkedFiles) else [v1FilePath]
            
            # Shot name
            self.tableWidget.setCellWidget(row, 0, self.getinfolable(self.shot_number(row + 1)))

            # V1 Thumbnail
            if v1Thumb and os.path.isfile(v1Thumb):
                self.tableWidget.setCellWidget(row, 1, self.getimagelable(v1Thumb))
            else:
                self.tableWidget.setCellWidget(row, 1, self.getinfolable(os.path.basename(v1FilePath) if v1FilePath else "offline"))

            # V2 Thumbnail (if exists)
            v2Thumb = None
            v2ClipName = ""
            if row < len(self.v2StartFrames):
                # Find matching V2 clip by start frame
                v1Start = self.v1StartFrames[row]
                for v2_idx, v2Start in enumerate(self.v2StartFrames):
                    if v2Start == v1Start:
                        v2Thumb = self.v2Thumbs[v2_idx] if v2_idx < len(self.v2Thumbs) else None
                        v2ClipName = self.v2ClipNames[v2_idx] if v2_idx < len(self.v2ClipNames) else ""
                        break
            
            if v2Thumb and os.path.isfile(v2Thumb):
                self.tableWidget.setCellWidget(row, 2, self.getimagelable(v2Thumb))
            elif v2ClipName:
                self.tableWidget.setCellWidget(row, 2, self.getinfolable(v2ClipName))
            else:
                self.tableWidget.setCellWidget(row, 2, self.getinfolable("-"))

            # Duration
            self.tableWidget.setCellWidget(row, 3, self.getinfolable(v1Duration))

            # V1 Clip Name
            self.tableWidget.setCellWidget(row, 4, self.getinfolable(os.path.basename(v1FilePath) if v1FilePath else "offline"))

            # V2 Clip Name
            self.tableWidget.setCellWidget(row, 5, self.getinfolable(v2ClipName if v2ClipName else "-"))

            # Set Up button
            btnShot = self.getshotBtn(row, linkedFile, v1Thumb, v1Duration)
            self.btnShots.append(btnShot)
            self.tableWidget.setCellWidget(row, 6, btnShot)
            
            # Number of layers for this shot
            num_layers = len(linkedFile)
            self.tableWidget.setCellWidget(row, 7, self.getinfolable(str(num_layers)))
            
            # Status (empty for now)
            self.tableWidget.setCellWidget(row, 8, self.getinfolable(""))

    def runbatch(self):
        """Run batch thumbnail generation."""
        self.loading_dialog = LoadingDialogXML(self)
        self.thread = WorkerThread(self.nameGroup, self.fileGroup, self.thumbGroup)
        self.thread.progress.connect(self.update_progress_bar)
        self.thread.task.connect(self.update_progress_bar_task)
        self.thread.done.connect(self.thread_finished)
        self.thread.start()
        self.loading_dialog.show()

    def update_progress_bar(self, progress_percent):
        self.loading_dialog.progressbar.setValue(progress_percent)

    def update_progress_bar_task(self, task):
        self.loading_dialog.details.setText(task)

    def thread_finished(self):
        self.loading_dialog.progressbar.setValue(100)
        self.thread.quit()
        self.loading_dialog.close()
        QMessageBox.information(self, "Complete", "Thumbnail generation complete!")

    def nukeAll(self):
        """Create Nuke scripts for all shots using linked files from all layers."""
        if not hasattr(self, 'v1ClipNames') or not self.v1ClipNames:
            QMessageBox.warning(self, "Error", "No shots loaded. Please select an XML first.")
            return
        
        # Make sure clips are linked
        if not hasattr(self, 'linkedFiles') or not self.linkedFiles:
            self.linkClips()
            
        reply = QMessageBox.question(
            self, "Confirm",
            f"Create Nuke scripts for {len(self.v1ClipNames)} shots?\n\n"
            f"Project: {getattr(self, 'projectRoot', 'Not set')}\n"
            f"Edit: {getattr(self, 'editname', 'Not set')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Show progress dialog
        progress = LoadingDialogXML(self)
        progress.setWindowTitle("Creating Nuke Scripts")
        progress.show()
        
        created = 0
        skipped = 0
        errors = []
        
        total = len(self.linkedFiles)
        
        for clipindex, linkedFile in enumerate(self.linkedFiles):
            progress.progressbar.setValue(int((clipindex / total) * 100))
            progress.details.setText(f"Processing shot {clipindex + 1}/{total}: {self.shot_number(clipindex + 1)}")
            QApplication.processEvents()
            
            # Check if shot folder already exists
            shot_name = self.shot_number(clipindex + 1)
            nuke_root = os.path.join(self.projectRoot, "Nuke")
            work_folder = os.path.join(nuke_root, self.editname)
            shot_folder = os.path.join(work_folder, shot_name)
            
            if os.path.isdir(shot_folder):
                print(f"Skipping {shot_name} - folder already exists")
                skipped += 1
                continue
            
            try:
                # Get thumbnail
                thumb = self.v1Thumbs[clipindex] if clipindex < len(self.v1Thumbs) else None
                duration = self.v1Durations[clipindex]
                
                # Create the shot folder and script with all linked files
                self.folderSetup(
                    shotIndex=clipindex,
                    read_paths=linkedFile,  # This now contains all layers!
                    thumb=thumb,
                    duration=duration,
                    single=False
                )
                created += 1
                
            except Exception as e:
                errors.append(f"Shot {clipindex + 1}: {str(e)}")
                print(f"Error creating shot {clipindex + 1}: {e}")
        
        progress.progressbar.setValue(100)
        progress.close()
        
        # Update buttons to green if we have them
        if hasattr(self, 'btnShots'):
            for btn in self.btnShots:
                btn.setStyleSheet("background-color: green")
        
        # Show summary
        summary = f"Created: {created} shots\nSkipped: {skipped} shots (already exist)"
        if errors:
            summary += f"\n\nErrors:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                summary += f"\n... and {len(errors) - 10} more errors"
        
        QMessageBox.information(self, "Nuke All Complete", summary)

    def readNode(self, name, file, duration, offset):
        """Generate Nuke Read node code."""
        return f"""Read {{
    inputs 0
    file_type mxf
    file "{file}"
    last {duration}
    origlast {duration}
    origset true
    version 1
    colorspace "{self.colourspace}"
    name Read{name}
    xpos {offset}
    ypos 48
}}
"""

    def readRef(self, file, duration, startframe, endframe):
        """Generate Nuke Read node for reference clip with Retime."""
        REFduration = self.movDuration(file)
        handles = getattr(self, 'spinboxHandles', None)
        handle_value = handles.value() if handles else 25
        
        return f"""Read {{
    inputs 0
    file_type mxf
    file "{file}"
    last {REFduration}
    origlast {REFduration}
    origset true
    version 1
    colorspace color_picking
    name Read_REF
    label "Fr. range: [value first] - [value last]\\nRes: [value width] * [value height]"
    xpos -410
    ypos 130
}}
Retime {{
    input.first {int(startframe) + 1}
    input.first_lock true
    input.last {int(endframe)}
    input.last_lock true
    output.first {int(1001 + handle_value)}
    output.first_lock true
    output.last {int(duration) + 1001 - handle_value}
    output.last_lock true
    time ""
    name Retime_REF
    xpos -410
    ypos 296
}}
"""
