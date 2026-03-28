import os  # Operating system utilities
import time  # Time-related functions
import platform  # System and platform identification
import subprocess  # Running external processes

# PyQt5 Imports (GUI Framework)
from PyQt6 import *  # Core PyQt5 modules
from PyQt6 import uic
from PyQt6.QtWidgets import *  # UI widgets (buttons, tables, etc.)
from PyQt6.QtCore import Qt, QPropertyAnimation  # Core features and animationsfrom PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton  # Specific widgets
from PyQt6.QtGui import QFont  # Font handling
# DaVinci Resolve Automation
try:
    import DaVinciResolveScript as dvr_script  # DaVinci Resolve scripting API
except:
    print("DaVinciResolveScript not found.")

from configure import configure
from uiTools import uiTools

class page_nukedash(QMainWindow, configure):
    def __init__(self):
        super().__init__()
        uic.loadUi("ui/nukedash_v02.ui", self)
        self.initUI()

        self.activePage= "page_nukedash"
        self.thumbpos = 0

        self.configure = configure()
        self.uiTools = uiTools()
        #self.initUIold()


        self.configContent = self.configure.configContent
        self.configFile = self.configure.configFile
        self.recentList = self.configure.recentList

        self.columnWidth = self.configure.columnWidth
        self.rowHeight = self.configure.rowHeight


        #self.configRecent()
        self.recentComboBox.currentTextChanged.connect(self.refreshRoot)
        self.recentComboBox.currentTextChanged.connect(lambda : print(self.recentComboBox.currentText()))
        self.refreshRoot()


        #self.load_stylesheet("ui/nukedash.qss")  # Load the QSS stylesheet
        self.load_stylesheet("ui/nukedash.qss")
        #self.showNukeTL()



    def test(self):
        #print("test funtion")
        self.clearlayout(self.layout04)
        #self.layout04.removeWidget(self.tableWidgetx)
        pass
    def UIcleartable(self):
        self.clearlayout(self.layout04)
    def configRecent(self):
        self.recentComboBox.clear()
        # self.recentList =[]
        for index, l in enumerate(self.configContent[:5]):
            if not l == "NONE":
                self.recentList[index] = l[:-1]
        self.recentComboBox.addItems(self.recentList)
    def toggleRecent(self):
        if self.btnToggleRecent.text() == "All projects":
            self.btnToggleRecent.setText("show recent")
            self.recentComboBox.clear()
            #self.recentList =[]
            self.recentComboBox.addItems(self.allprojects)
        else:
            self.btnToggleRecent.setText("All projects")
            self.configRecent()
    def showNukeTL(self):
        self.uiTools.clearlayout(self.layout03)
        self.dvrRenders = os.path.join(self.projectRoot, "DaVinci", "Renders")
        self.nukeFolder = os.path.join(self.projectRoot, "Nuke")
        self.assetsFolder = os.path.join(self.nukeFolder, "assets")
        if not os.path.isdir(self.assetsFolder):
            os.mkdir(self.assetsFolder)
        self.btnProjectAssets.setDisabled(False)
        self.timelines = []
        self.TLbtns = []

        for folder in sorted(os.listdir(self.nukeFolder), reverse=True):

            if not folder[0] == "." and not folder == "Assets" and not folder == "assets" and not folder[0] == ".":
                self.timelines.append(folder)

                TLBtn = self.getTLBtn(folder)
                self.TLbtns.append(TLBtn)
                self.layout03.addWidget(TLBtn)
        self.horizontalSpacer_3 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.layout03.addItem(self.horizontalSpacer_3)
        self.TLbtns[0].setStyleSheet("background-color: #d85f29;")
        self.findnks(self.timelines[0])

    def selectRoot(self):

        root = Tk()
        root.withdraw()
        file = filedialog.askdirectory(title="Select project root")
        root.destroy()
        #print((file))
        self.projectRoot = file
        # print(file)
        self.lableRoot.setText("Project root: <" + self.projectRoot + ">")
        self.lableRoot.adjustSize()

        #for index, l in enumerate(self.configContent[:5]):
            #print(self.configContent[index])
            #if self.configContent[index] == "NONE\n":
                #self.configContent[index] = f"{file}\n"

        self.configContent[4] = self.configContent[3]
        self.configContent[3] = self.configContent[2]
        self.configContent[2] = self.configContent[1]
        self.configContent[1] = self.configContent[0]
        self.configContent[0] = f"{file}\n"


        with open(self.configFile, "w") as writeConfig:
            writeConfig.writelines(self.configContent)

        self.configRecent()
    def getTLBtn(self, folder):
        TLbtn = QtWidgets.QPushButton(self)
        TLbtn.setText(folder)
        # shotIndexBtn.clicked.connect(lambda: self.folderSetup(shotIndex))
        # shotIndexBtn.clicked.connect(lambda: shotIndexBtn.setStyleSheet("background-color : green"))
        TLbtn.clicked.connect(lambda: self.findnks(folder))
        TLbtn.clicked.connect(lambda: self.setCurrentTL(folder))
        TLbtn.clicked.connect(lambda: self.update_button_color(TLbtn))

        TLbtn.setToolTip("self.findnks")
        return TLbtn

    def update_button_color(self, clicked_button):
        # Reset all buttons to default color
        for btn in self.TLbtns:
            btn.setStyleSheet("background-color: none;")  # Reset to default

        # Change clicked button's color
        clicked_button.setStyleSheet("background-color: #d85f29;")


    def getimagelable(self, image):
        pic = QtGui.QPixmap(image)
        imagelable = QtWidgets.QLabel(self)
        imagelable.setScaledContents(True)
        imagelable.setPixmap(pic)
        imagelable.setObjectName(image)
        # tableWidget.setCellWidget(0,0, self.imagelable)
        return imagelable
    def btnpushRender(self, render, index):



        btnLatestRender = QtWidgets.QPushButton(self)
        text = os.path.basename(render)
        btnLatestRender.setText(text)
        btnLatestRender.setObjectName(render)
        btnLatestRender.setToolTip(render)
        if platform.system() == "Darwin":  # macOS
            #print("no mac")
            # macOS specific code
            btnLatestRender.clicked.connect(lambda: self.conform(render, index, btnLatestRender))
            
        elif platform.system() == "Windows":  # windows
            btnLatestRender.clicked.connect(lambda: self.conform(render, index, btnLatestRender))
            #btnLatestRender.clicked.connect(lambda: btnLatestRender.setStyleSheet("background-color : green"))
            #btnLatestRender.clicked.connect(lambda: self.noteConform(render, index))
            # windows specific code
        else:
            btnLatestRender.clicked.connect(lambda: self.conform(render, index, btnLatestRender))


        font = QFont()
        font.setPointSize(14)
        btnLatestRender.setFont(font)

        return btnLatestRender
    def conform(self, render, index, btn):
        """shotname = str(os.path.basename(render)[:3 + self.shotNumberPad])
        shotNumber = shotname[:self.shotNumberPad]
        shotIndex = 0

        renderFileName = (os.path.splitext(render)[0])
        renderVersion = renderFileName[:-2]

        nukeFileName = self.latestscriptpaths[d]"""
        self.push2dvr(render, index, btn)
        """if self.comboConformPlatform.currentText() == "DVR":
            pass
        elif self.comboConformPlatform.currentText() == "prem":
            self.push2prem(render)"""
    def push2dvr(self, clip, index, btn,  matte=False):
        shotname = str(os.path.basename(clip)[:3 + self.shotNumberPad])
        # Get currently open project
        # resolve = GetResolve()

        projectManager = None

        resolve = dvr_script.scriptapp("Resolve")
        try:
            projectManager = resolve.GetProjectManager()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error finding Resolve project:\n{e}\nIs resolve open?\nTurn on external scripting in preferences, general ")
        if projectManager:
            try:
                project = projectManager.GetCurrentProject()
            except Exception as e:
                QMessageBox.critical(None, "Error",
                                     f"Error finding Resolve project:\n{e}\nIs resolve open?\nTurn on external scripting in preferences, general ")
            if project:
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
                        #print("dupe")
                if shotBinDupe:
                    shotBin = shotbin1
                else:
                    shotBin = mediaPool.AddSubFolder(vfxBin, shotname)

                mediaPool.SetCurrentFolder(shotBin)
                mediaPool.DeleteClips(clip)
                mediaClip = mediaPool.ImportMedia(clip)

                for f in mediaClip:
                    f.SetClipColor("Lime")
                if not matte == False:
                    listmatte = []
                    listmatte.append(matte)
                    mediaStorage.AddClipMattesToMediaPool(mediaClip[0], listmatte)
                #complete
                btn.setStyleSheet("background-color : green")
                self.noteConform(clip, index)
    def push2prem(self, clip):

        shotname = str(os.path.basename(clip)[:3 + self.shotNumberPad])


        project_opened, sequence_active = wrappers.check_active_sequence(crash=False)
        if not project_opened:
            raise ValueError("please open a project")

        project = pymiere.objects.app.project

        bin_media = False
        bin_VFX = False
        bin_shot = False

        for bin in project.rootItem.children:
            if bin.name == "01_Media":
                bin_media = bin
                break
        if not bin_media:
            bin_media = project.rootItem.createBin("01_Media")

        for bin in bin_media.children:
            if bin.name == "VFX":
                bin_VFX = bin
                break
        if not bin_VFX:
            bin_VFX = bin_media.createBin("VFX")

        for bin in bin_VFX.children:
            if bin.name == shotname:
                bin_shot = bin
                break
        if not bin_shot:
            bin_shot = bin_VFX.createBin(shotname)

        clipimported = False
        for child in bin_shot.children:
            if child.name == os.path.basename(clip):
                clipimported = True
                premClip = child

        if not clipimported:
            cliplist = []
            cliplist.append(clip)
            premClip = project.importFiles(cliplist, suppressUI=False, targetBin=bin_shot, importAsNumberedStills=False)
    def openScript(self, script, text="na"):
        btnOpenScript = QtWidgets.QPushButton(self)
        if text == "na":
            btnOpenScript.setText(os.path.basename(script))
        else:
            btnOpenScript.setText(os.path.basename(text))

        btnOpenScript.clicked.connect(lambda: subprocess.Popen(['open', script]) if platform.system() == "Darwin" else
        subprocess.Popen(['xdg-open', script]) if platform.system() == "Linux" else
        os.startfile(script))
        #btnOpenScript.clicked.connect(self.play_sound)

        btnOpenScript.setObjectName(script)
        btnOpenScript.setToolTip("will open in default program")

        font = QFont()
        font.setPointSize(14)
        btnOpenScript.setFont(font)
        # right click menu
        shotv = os.path.basename(script)[-5:-3]
        # print("shotv; "+shotv)
        ishotv = 1
        try:
            ishotv = int(shotv)

            # print("ishot: " +str(ishotv))
        except:
            # print("fu")
            pass

        # print("ishot: "+str(ishot))
        # btnOpenScript.clicked.connect(lambda: print(type(ishot)))
        if ishotv > 1:
            btnOpenScript.setStyleSheet("background-color : purple")
        """if ishotv > 2:
            btnOpenScript.setStyleSheet("background-color : orange")
        if ishotv > 5:
            btnOpenScript.setStyleSheet("background-color : green")"""

        return btnOpenScript
    def setCurrentTL(self, folder):
        self.currentTL = folder
    def findnks(self, folder):
        #print(f"ize {self.size()} pos {self.pos}")
        position = self.pos()  # Get QPoint object
        #print(f"Window X: {position.x()} px, Y: {position.y()} px")

        self.shots = []

        start_t = time.perf_counter()

        self.edit = os.path.join(self.nukeFolder, folder)
        self.latestscriptpaths = []
        try:
            self.layout04.removeWidget(self.tableWidgetx)
            self.tableWidgetx.deleteLater()
        except Exception as e:
            print(e)


        for shot in sorted(os.listdir(self.edit), reverse=False):

            if not shot[0] == ".": # ignor folders that start with "."

                scripts = os.path.join(self.edit, shot, "scripts")
                self.shots.append(shot)

                nukescript = []
                if os.path.isdir(scripts):
                    for nk in sorted(os.listdir(scripts), reverse=False):

                        if nk[-3:] == ".nk" or nk[-6:] == ".nkind":
                            # rint("s"+nk[:3])
                            if not nk[:3] == "tmp":
                                nukescript.append(nk)

                    if nukescript:
                        latestscript = nukescript[-1]
                        latestscriptpath = os.path.join(scripts, latestscript)
                    # print(latestscriptpath)

                    # latestscriptpath = latestscriptpath.replace('\\', '\xxxreplacexxx')
                    self.latestscriptpaths.append(latestscriptpath)
                    try:
                        self.tableWidget.close()
                    except:
                        print("cant close table")


                    # print(os.path.basename(latestscriptpath))
        #print(self.shots)
        if not len(self.shots) == 0:
            self.showscripttable()
            self.shotNumberPad = len(self.shots[0]) - 3
        else:

            self.tableWidget.setColumnCount(4)
            self.tableWidget.setGeometry(0, 200, 1600, 700)
            self.tableWidget.setHorizontalHeaderLabels(
                ['Thumb', 'Open latest script\n in nuke', 'Push to DVR', "Notes"])

            self.tableWidget.setRowCount(len(self.latestscriptpaths))
            self.tableWidget.verticalHeader().setDefaultSectionSize(90)
            self.tableWidget.horizontalHeader().setDefaultSectionSize(160)
            try:
                self.layout04.removeWidget(self.tableGridWidget)
            except: pass
            #self.layout04.removeWidget(self.tableWidgetx)
            self.layout04.addWidget(self.tableWidget)



        emd_t = time.perf_counter()
        dur = emd_t - start_t
        print(f"Loading all .nk files took {dur:.2f}s")
    def showscripttable(self):
        if self.uiType == "grid":
            self.uiType = "table"
            self.layout04.removeWidget(self.tableGridWidget)


        self.tableWidget = QTableWidget(self)

        self.tableWidget.setColumnCount(4)
        self.tableWidget.setGeometry(0, 200, 1600, 700)
        self.tableWidget.setHorizontalHeaderLabels(
            ['Thumb', 'Open latest script\n in nuke', 'Push to DVR', "Notes"])

        self.tableWidget.setRowCount(len(self.latestscriptpaths))
        self.tableWidget.verticalHeader().setDefaultSectionSize(90)
        self.tableWidget.horizontalHeader().setDefaultSectionSize(160)
        # self.tableWidget.show()
        try:
            pass
            #self.layout04.removeWidget(self.tableGridWidget)
        except:
            pass

        #self.layout04.removeWidget(self.tableWidgetx)
        self.layout04.addWidget(self.tableWidget)
        self.tableWidget.setColumnWidth(0, self.columnWidth)

        for i in range(len(self.latestscriptpaths)):
            self.tableWidget.setRowHeight(i, self.rowHeight)

        row = 0
        count = 0
        self.tableWidget.setColumnWidth(3, 300)

        self.hiddenRows = []
        self.latestRendersPaths = []
        self.latestMattePaths = []
        self.latestThumbsPaths = []
        self.renderButtonList = []
        self.scriptlist = []
        self.conformBtns = []
        self.configure.notePaths = []

        for script in self.latestscriptpaths:
            count += 1
            shotindex = count

            self.hiddenRows.append(False)
            # self.tableWidget.setRowCount(row + 1)
            shotdir = os.path.join(self.edit, self.shots[self.latestscriptpaths.index(script)])
            shotRenderDir = os.path.join(shotdir, "renders")
            shotRenderCompDir = os.path.join(shotRenderDir, "comp")
            assetsfolder = os.path.join(shotdir, "assets")
            renders = []
            renderPaths = []
            mattePaths = []

            latestrenderPath = ""
            # print(script)
            # print(count)

            for render in os.listdir(shotRenderCompDir):
                # print("render: "+render)
                if not render.endswith(".mp4"):
                    renders.append(render)
            # print(renders)
            for render in sorted(renders):
                if not "matte" in render:
                    renderPaths.append(os.path.join(shotRenderCompDir, render))
                if "matte" in render:
                    mattePaths.append(os.path.join(shotRenderCompDir, render))
            # renderPaths.sort(key=os.path.getmtime)
            # print(renderPaths)

            """if self.checkboxUseRenderSearch.isChecked():
                renderPaths = ["none"]
                search = self.QtSearchbox.text()

                for render in renders:

                    if search in render:
                        renderPaths.append(os.path.join(shotRenderCompDir, render))"""

            """if self.checkboxSortRendersbyTime.isChecked():
                renderPaths.sort(key=os.path.getmtime)"""

            if not len(mattePaths) == 0:
                latestMatte  = mattePaths[-1]
                self.latestMattePaths.append(latestMatte)
            else:
                latestMatte = "NONE"
                self.latestMattePaths.append(latestMatte)

            if not len(renders) == 0:
                latestrender = renders[-1]
                # latestrenderPath = os.path.join(shotRenderCompDir, latestrender)

                latestrenderPath = renderPaths[-1]
                self.latestRendersPaths.append(latestrenderPath)


            else:
                latestrender = "NONE"
                latestrenderPath = "NONE"
                self.latestRendersPaths.append(latestrenderPath)

            # print("latestrender: " + latestrender)
            thumbpath = "nothumb.jpg"
            for i in sorted(os.listdir(shotdir)):
                #print(i)


                if i.endswith(".jpeg") or i.endswith(".png") or i.endswith(".JPG"):
                    thumbpath = os.path.join(shotdir, i)
                    # print(thumbpath)







            if os.path.isfile(thumbpath):

                self.tableWidget.setCellWidget(row, 0, self.getimagelable(thumbpath))
                self.latestThumbsPaths.append(thumbpath)
            else:
                self.latestThumbsPaths.append(None)
                pass
            btnOpenScript = self.openScript(script)
            self.scriptlist.append(btnOpenScript)
            self.tableWidget.setCellWidget(row, 1, btnOpenScript)

            if latestrender == "NoRenders":
                self.tableWidget.setCellWidget(row, 2, self.openScript(shotRenderCompDir, "NoRenders"))
            else:
                btnConform = self.btnpushRender(latestrenderPath, row)
                self.conformBtns.append(btnConform)
                self.tableWidget.setCellWidget(row, 2, btnConform)


                #btnConform.clicked.connect(lambda: self.noteConform(row, render))

            textboxshotNote, shotNotePath = self.shotNotes(row)
            self.configure.notePaths.append(shotNotePath)
            self.tableWidget.setCellWidget(row, 3, textboxshotNote)

            row += 1

        self.uiHideFrows()
    def gridTable(self,thumbs=None):

        #print(len(thumbs))
        self.tableGridWidget = QTableWidget(self)
        columnNumber = 6
        #print(len(thumbs))
        rowNumber = (int(len(thumbs)/columnNumber))*2 +1
        #print("row"+str(rowNumber))
        columnWidth = int(2000 / columnNumber - 15)
        rowHeight = int(columnWidth/1.77)
        #4 * 13 = 50
        #50 / 4 = 13

        self.tableGridWidget.setColumnCount(columnNumber)
        self.tableGridWidget.setRowCount(rowNumber+1)

        index = 0
        for row in range(rowNumber):
            if row%2 == 0:
                self.tableGridWidget.setRowHeight(row, rowHeight)
                for column in range(columnNumber):
                    self.tableGridWidget.setColumnWidth(column, columnWidth)

                    if index < len(thumbs):
                        self.tableGridWidget.setCellWidget(row, column, QtWidgets.QLabel(str(index)))
                        self.tableGridWidget.setCellWidget(row, column, self.getimagelable(thumbs[index]))
                        self.tableGridWidget.setCellWidget(row + 1, column,self.uibtnLayout(self.openScript(self.latestscriptpaths[index]), self.btnpushRender(self.latestRendersPaths[index], index)))
                        #self.tableGridWidget.setCellWidget(row, column, QtWidgets.QLabel(str(index)))
                        self.tableGridWidget.setRowHeight(row + 1, 40)

                    index = index + 1

        self.tableGridWidget.horizontalHeader().setVisible(False)
        self.tableGridWidget.verticalHeader().setVisible(False)


        self.layout04.addWidget(self.tableGridWidget)
        self.layout04.removeWidget(self.tableWidget)
    def uibtnLayout(self, one, two):

        layout = QHBoxLayout()


        layout.addWidget(one)
        layout.addWidget(two)

        cellWidget = QWidget()
        cellWidget.setLayout(layout)
        return cellWidget
    ##note taking
    def shotNotes(self, shotindex):
        folder = os.path.dirname(self.latestThumbsPaths[shotindex])
        shotname = os.path.split(folder)[1]
        notesFileName = shotname + ".txt"
        notesFilepath = os.path.join(folder, notesFileName)

        notes = shotname
        if not os.path.isfile(notesFilepath):
            with open(notesFilepath, "w") as writeNotes:
                writeNotes.writelines(shotname)
                writeNotes.writelines("\n")
                writeNotes.writelines("Latest conform: None")
                writeNotes.writelines("\n")
        else:
            lines = open(notesFilepath, "r").readlines()
            notes = ""
            for line in lines:
                if '*' not in line:
                    notes += line

        QtTextbox = QtWidgets.QTextEdit(self)
        QtTextbox.setText(notes)
        QtTextbox.textChanged.connect(lambda: self.update_notes(QtTextbox.toPlainText(), notesFilepath, shotindex))

        lable = QtWidgets.QLabel(self)
        #lable.setText(str(notes))

        lines = open(notesFilepath, "r").readlines()
        notes = ""
        colouredWidget = self.scriptlist[shotindex]
        if "-g" in lines[0]:
            colouredWidget.setStyleSheet("background-color : green")
        elif "-r" in lines[0]:
            colouredWidget.setStyleSheet("background-color : red")
        elif "-o" in lines[0]:
            colouredWidget.setStyleSheet("background-color : #da8500")
        elif "-p" in lines[0]:
            colouredWidget.setStyleSheet("background-color : purple")
        elif "-b" in lines[0]:
            colouredWidget.setStyleSheet("background-color : blue")
        elif "-y" in lines[0]:
            colouredWidget.setStyleSheet("background-color : #a69606")
        elif "-t" in lines[0]:
            colouredWidget.setStyleSheet("background-color : teal")
        elif "-f" in lines[0]:
            colouredWidget.setStyleSheet("color : red")
            self.tableWidget.setRowHeight(shotindex, 1)
            self.hiddenRows[shotindex] = True
        try:
            if "Latest conform: " in lines[1]:
                #print(self.latestRendersPaths[shotindex])
                latestConform = os.path.basename(self.latestRendersPaths[shotindex]).split('/')[-1]
                note = lines[1][16:]
                if latestConform in note:

                    self.conformBtns[shotindex].setStyleSheet("background-color : green")

        except:
            pass

        return QtTextbox, notesFilepath
    def update_notes(self, text, txtFile, index):
        with open(txtFile, "w") as writeNotes:
            writeNotes.writelines(text)

        lines = open(txtFile, "r").readlines()
        notes = ""
        colouredWidget = self.scriptlist[index]
        if "-g" in lines[0]:
            colouredWidget.setStyleSheet("background-color : green")
        elif "-r" in lines[0]:
            colouredWidget.setStyleSheet("background-color : red")
        elif "-o" in lines[0]:
            colouredWidget.setStyleSheet("background-color : #da8500")
        elif "-p" in lines[0]:
            colouredWidget.setStyleSheet("background-color : purple")
        elif "-b" in lines[0]:
            colouredWidget.setStyleSheet("background-color : blue")
        elif "-y" in lines[0]:
            colouredWidget.setStyleSheet("background-color : #a69606")
        elif "-t" in lines[0]:
            colouredWidget.setStyleSheet("background-color : teal")
        elif "-f" in lines[0]:
            colouredWidget.setStyleSheet("color : red")
            self.tableWidget.setRowHeight(index, 1)
            self.hiddenRows[index] = True

        elif "0" in lines[0]:
            colouredWidget.setStyleSheet("background-color : none")

        else:
            colouredWidget.setStyleSheet("background-color : purple")

        if "-f" not in lines[0]:
            self.tableWidget.setRowHeight(index, self.rowHeight)
    def noteConform(self,text, shotindex ):
        #print("text: " +text)
        #print(shotindex)
        print("note paths: " + str(self.configure.notePaths))
        self.splitall(text)
        conformtxt = (os.path.basename(text).split('/')[-1])
        #print(conformtxt)
        #print("conform")
        txtFile = self.configure.notePaths[shotindex]
        lines = open(txtFile, "r").readlines()
        #print("lines: " +str(lines))

        try:
            lines[1] = "Latest conform: " + conformtxt + " by: "+self.user + "\n"
        except:
            lines.insert(1,"Latest conform: " + conformtxt + " by: "+self.user +"\n")



        with open(txtFile, "w") as writeNotes:
            writeNotes.writelines(lines)
    def refreshRoot(self):
        try:
            if not self.recentComboBox.currentText() == "NONE" or not self.recentComboBox.currentText() == "":

                #print(self.recentComboBox.currentText())
                self.projectRoot = self.recentComboBox.currentText()
                self.lableRoot.setText("Project root: <" + self.projectRoot + ">")
                #self.recentComboBox.resize()
                #self.showNukeTL()
                #print("refresh")

                for folder in os.listdir(self.nukeFolder):

                    if not folder[0] == "." and not folder == "Assets" and not folder == "assets" and not folder[0] == ".":


                        #self.setCurrentTL(folder)
                        self.findnks(folder)

                        break
        except:
            pass
        try:
            self.showNukeTL()
        except:
            QMessageBox.about(self, "ERROR", f" Something to do with {self.recentComboBox.currentText()}  is broken.")
    def selectRoot(self):

        root = Tk()
        root.withdraw()
        file = filedialog.askdirectory(title="Select project root")
        root.destroy()
        #print((file))
        if not file.startswith(r"Z:\\PROJECTS"):
            file = "None"


        self.projectRoot = file
        # #print(file)
        self.lableRoot.setText("Project root: <" + self.projectRoot + ">")
        self.lableRoot.adjustSize()

        #for index, l in enumerate(self.configContent[:5]):
            #print(self.configContent[index])
            #if self.configContent[index] == "NONE\n":z
            #if self.configContent[index] == "NONE\n":z
                #self.configContent[index] = f"{file}\n"

        self.configContent[4] = self.configContent[3]
        self.configContent[3] = self.configContent[2]
        self.configContent[2] = self.configContent[1]
        self.configContent[1] = self.configContent[0]
        self.configContent[0] = f"{file}\n"


        with open(self.configFile, "w") as writeConfig:
            writeConfig.writelines(self.configContent)

        self.configRecent()
    def uiChangeView(self):
        if self.uiType == "table":
            self.uiType = "grid"
            self.gridTable(self.latestThumbsPaths)

        elif self.uiType == "grid":
            self.uiType = "table"
            self.showscripttable()
            self.layout04.removeWidget(self.tableGridWidget)
    def updateThumbs(self):
       #print("update")
        for index, script in enumerate(self.latestscriptpaths):
            clip = self.latestRendersPaths[index]
            thumb = self.latestThumbsPaths[index]
            if os.path.isfile(thumb):
                if os.path.isfile(clip):
                    newthumb = thumb[:-5] + clip[-8:-4] + ".jpeg"
                    self.thumb_grab(clip, self.version_up(thumb), 1)
                    # print(newthumb)
                else:
                    continue

    def make_preview_mp4(self, mov):
        if mov.endswith(".mov"):
            output_path = os.path.splitext(mov)[0] + "_preview.mp4"
            ffmpeg.input(mov).filter("scale", width="1920", height="1080").output(output_path).run(quiet=True)
    def make_preview_batch(self):

        for movie in self.latestRendersPaths:
            if movie.endswith(".mov"):
                #print(movie)
                #print(os.path.split(movie))
                #input_path = os.path.join(input_folder, movie)
                output_path = os.path.splitext(movie)[0] + "_preview.mp4"
                #print(output_path)

                ffmpeg.input(movie).filter("scale", width="1920", height="1080").output(output_path).run(quiet=True)





        QMessageBox.about(self, "mp4s", "New previews updated")
    def uiHideFrows(self):
        if self.checkboxHideF.isChecked():
            for i, value in enumerate(self.hiddenRows):
                if value:
                    self.tableWidget.setRowHeight(i, 0)

        elif self.checkboxShowF.isChecked():
            for i, value in enumerate(self.hiddenRows):
                if value:
                    self.tableWidget.setRowHeight(i, self.rowHeight)


        else:
            for i, value in enumerate(self.hiddenRows):
                if value:
                    self.tableWidget.setRowHeight(i, 1)
    def refreshDvr(self, btn):
        for path in self.latestRendersPaths:
            self.push2dvr(path)


    def initUI(self):
        self.recentComboBox.addItems(self.allprojects)

        self.btnshowTL.clicked.connect(self.showNukeTL)
        self.btnshowTL.setToolTip("self.showNukeTL")


        self.btnRefreshDvr.clicked.connect(self.refreshDvr)
        self.btnProjectAssets.clicked.connect(lambda: self.openFileLocation(self.assetsFolder))
        self.btnDvrRenders.clicked.connect(lambda: self.openFileLocation(self.dvrRenders))

        self.btnUpdateThumbs.clicked.connect(self.updateThumbs)
        self.btnUpdateThumbs.setDisabled(True)

        self.btnMakePreviews.clicked.connect(self.make_preview_batch)
        self.btnMakePreviews.setDisabled(True)

        self.checkboxHideF.setChecked(True)
        self.checkboxHideF.stateChanged.connect(self.uiHideFrows)

        self.checkboxShowF.setChecked(False)
        self.checkboxShowF.stateChanged.connect(self.uiHideFrows)

        self.sliderUIslace.valueChanged.connect(self.uiSliderUpdate)
        self.sliderUIslace.setMaximum(500)
        #self.sliderUIslace.setMinimum(10)



