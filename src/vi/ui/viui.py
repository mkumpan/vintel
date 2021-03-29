# coding=utf-8
###########################################################################
#  Vintel - Visual Intel Chat Analyzer									  #
#  Copyright (C) 2014-15 Sebastian Meyer (sparrow.242.de+eve@gmail.com )  #
#																		  #
#  This program is free software: you can redistribute it and/or modify	  #
#  it under the terms of the GNU General Public License as published by	  #
#  the Free Software Foundation, either version 3 of the License, or	  #
#  (at your option) any later version.									  #
#																		  #
#  This program is distributed in the hope that it will be useful,		  #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of		  #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the		  #
#  GNU General Public License for more details.							  #
#																		  #
#																		  #
#  You should have received a copy of the GNU General Public License	  #
#  along with this program.	 If not, see <http://www.gnu.org/licenses/>.  #
###########################################################################

import datetime
import logging
import sys
import time
import webbrowser

import requests
import six
from PyQt4 import QtGui, uic, QtCore
from PyQt4.QtCore import QPoint, SIGNAL
from PyQt4.QtGui import *
from PyQt4.QtGui import QAction
from PyQt4.QtGui import QImage, QPixmap
from PyQt4.QtGui import QMessageBox
from PyQt4.QtWebKit import QWebPage

import vi.version
from vi import dotlan, filewatcher
from vi import evegate, versionchecker
from vi import states
from vi.cache.cache import Cache
from vi.chatparser import ChatParser
from vi.resources import resourcePath
from vi.soundmanager import SoundManager
from vi.threads import AvatarFindThread, KOSCheckerThread, MapStatisticsThread
from vi.ui.systemtray import TrayContextMenu

# Timer intervals
MESSAGE_EXPIRY_SECS = 20 * 60
MAP_UPDATE_INTERVAL_MSECS = 4 * 1000
CLIPBOARD_CHECK_INTERVAL_MSECS = 4 * 1000


class MainWindow(QtGui.QMainWindow):


    def __init__(self, pathToLogs, trayIcon, backGroundColor):

        QtGui.QMainWindow.__init__(self)
        self.cache = Cache()

        if backGroundColor:
            self.setStyleSheet("QWidget { background-color: %s; }" % backGroundColor)
        uic.loadUi(resourcePath('vi/ui/MainWindow.ui'), self)
        self.setWindowTitle("Vintel " + vi.version.VERSION + "{dev}".format(dev="-SNAPSHOT" if vi.version.SNAPSHOT else ""))
        self.taskbarIconQuiescent = QtGui.QIcon(resourcePath("vi/ui/res/logo_small.png"))
        self.taskbarIconWorking = QtGui.QIcon(resourcePath("vi/ui/res/logo_small_green.png"))
        self.setWindowIcon(self.taskbarIconQuiescent)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self.pathToLogs = pathToLogs
        self.mapTimer = QtCore.QTimer(self)
        self.connect(self.mapTimer, SIGNAL("timeout()"), self.updateMapView)
        self.clipboardTimer = QtCore.QTimer(self)
        self.oldClipboardContent = ""
        self.trayIcon = trayIcon
        self.trayIcon.activated.connect(self.systemTrayActivated)
        self.clipboard = QtGui.QApplication.clipboard()
        self.clipboard.clear(mode=self.clipboard.Clipboard)
        self.alarmDistance = 0
        self.lastStatisticsUpdate = 0
        self.chatEntries = []
        self.frameButton.setVisible(False)
        self.scanIntelForKosRequestsEnabled = True
        self.initialMapPosition = None
        self.mapPositionsDict = {}

        # Load user's toon names
        self.knownPlayerNames = self.cache.getFromCache("known_player_names")
        if self.knownPlayerNames:
            self.knownPlayerNames = set(self.knownPlayerNames.split(","))
        else:
            self.knownPlayerNames = set()
            diagText = "Vintel scans EVE system logs and remembers your characters as they change systems.\n\nSome features (clipboard KOS checking, alarms, etc.) may not work until your character(s) have been registered. Change systems, with each character you want to monitor, while Vintel is running to remedy this."
            QMessageBox.warning(None, "Known Characters not Found", diagText, "Ok")

        # Set up user's intel rooms
        roomnames = self.cache.getFromCache("room_names")
        if roomnames:
            roomnames = roomnames.split(",")
        else:
            roomnames = (u"TheCitadel", u"North Provi Intel", u"North Catch Intel", "North Querious Intel")
            self.cache.putIntoCache("room_names", u",".join(roomnames), 60 * 60 * 24 * 365 * 5)
        self.roomnames = roomnames

        # Disable the sound UI if sound is not available
        if not SoundManager().soundAvailable:
            self.changeSound(disable=True)
        else:
            self.changeSound()

        # Set up Transparency menu - fill in opacity values and make connections
        self.opacityGroup = QActionGroup(self.menu)
        for i in (100, 80, 60, 40, 20):
            action = QAction("Opacity {0}%".format(i), None, checkable=True)
            if i == 100:
                action.setChecked(True)
            action.opacity = i / 100.0
            self.connect(action, SIGNAL("triggered()"), self.changeOpacity)
            self.opacityGroup.addAction(action)
            self.menuTransparency.addAction(action)

        #
        # Platform specific UI resizing - we size items in the resource files to look correct on the mac,
        # then resize other platforms as needed
        #
        if sys.platform.startswith("win32") or sys.platform.startswith("cygwin"):
            font = self.statisticsButton.font()
            font.setPointSize(8)
            self.statisticsButton.setFont(font)
            self.jumpbridgesButton.setFont(font)
            ChatEntryWidget.TEXT_SIZE = 8
        elif sys.platform.startswith("linux"):
            pass

        self.wireUpUIConnections()
        self.recallCachedSettings()
        self.setupThreads()
        self.setupMap(True)


    def paintEvent(self, event):
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt,  painter, self)


    def recallCachedSettings(self):
        try:
            self.cache.recallAndApplySettings(self, "settings")
        except Exception as e:
            logging.error(e)
            # todo: add a button to delete the cache / DB
            self.trayIcon.showMessage("Settings error", "Something went wrong loading saved state:\n {0}".format(str(e)), 1)


    def wireUpUIConnections(self):
        # Wire up general UI connections
        self.connect(self.clipboard, SIGNAL("changed(QClipboard::Mode)"), self.clipboardChanged)
        self.connect(self.autoScanIntelAction, SIGNAL("triggered()"), self.changeAutoScanIntel)
        self.connect(self.kosClipboardActiveAction, SIGNAL("triggered()"), self.changeKosCheckClipboard)
        self.connect(self.zoomInButton, SIGNAL("clicked()"), self.zoomMapIn)
        self.connect(self.zoomOutButton, SIGNAL("clicked()"), self.zoomMapOut)
        self.connect(self.statisticsButton, SIGNAL("clicked()"), self.changeStatisticsVisibility)
        self.connect(self.jumpbridgesButton, SIGNAL("clicked()"), self.changeJumpbridgesVisibility)
        self.connect(self.chatLargeButton, SIGNAL("clicked()"), self.chatLarger)
        self.connect(self.chatSmallButton, SIGNAL("clicked()"), self.chatSmaller)
        self.connect(self.infoAction, SIGNAL("triggered()"), self.showInfo)
        self.connect(self.showChatAvatarsAction, SIGNAL("triggered()"), self.changeShowAvatars)
        self.connect(self.alwaysOnTopAction, SIGNAL("triggered()"), self.changeAlwaysOnTop)
        self.connect(self.chooseChatRoomsAction, SIGNAL("triggered()"), self.showChatroomChooser)
        self.connect(self.catchRegionAction, SIGNAL("triggered()"), lambda : self.handleRegionMenuItemSelected(self.catchRegionAction))
        self.connect(self.providenceRegionAction, SIGNAL("triggered()"), lambda : self.handleRegionMenuItemSelected(self.providenceRegionAction))
        self.connect(self.queriousRegionAction, SIGNAL("triggered()"), lambda : self.handleRegionMenuItemSelected(self.queriousRegionAction))
        self.connect(self.providenceCatchRegionAction, SIGNAL("triggered()"), lambda : self.handleRegionMenuItemSelected(self.providenceCatchRegionAction))
        self.connect(self.providenceCatchCompactRegionAction, SIGNAL("triggered()"), lambda : self.handleRegionMenuItemSelected(self.providenceCatchCompactRegionAction))
        self.connect(self.chooseRegionAction, SIGNAL("triggered()"), self.showRegionChooser)
        self.connect(self.showChatAction, SIGNAL("triggered()"), self.changeChatVisibility)
        self.connect(self.soundSetupAction, SIGNAL("triggered()"), self.showSoundSetup)
        self.connect(self.activateSoundAction, SIGNAL("triggered()"), self.changeSound)
        self.connect(self.useSpokenNotificationsAction, SIGNAL("triggered()"), self.changeUseSpokenNotifications)
        self.connect(self.trayIcon, SIGNAL("alarm_distance"), self.changeAlarmDistance)
        self.connect(self.framelessWindowAction, SIGNAL("triggered()"), self.changeFrameless)
        self.connect(self.trayIcon, SIGNAL("change_frameless"), self.changeFrameless)
        self.connect(self.frameButton, SIGNAL("clicked()"), self.changeFrameless)
        self.connect(self.quitAction, SIGNAL("triggered()"), self.close)
        self.connect(self.trayIcon, SIGNAL("quit"), self.close)
        self.connect(self.jumpbridgeDataAction, SIGNAL("triggered()"), self.showJumbridgeChooser)
        self.mapView.page().scrollRequested.connect(self.mapPositionChanged)


    def setupThreads(self):
        # Set up threads and their connections
        self.avatarFindThread = AvatarFindThread()
        self.connect(self.avatarFindThread, SIGNAL("avatar_update"), self.updateAvatarOnChatEntry)
        self.avatarFindThread.start()

        self.kosRequestThread = KOSCheckerThread()
        self.connect(self.kosRequestThread, SIGNAL("kos_result"), self.showKosResult)
        self.kosRequestThread.start()

        self.filewatcherThread = filewatcher.FileWatcher(self.pathToLogs)
        self.connect(self.filewatcherThread, SIGNAL("file_change"), self.logFileChanged)
        self.filewatcherThread.start()

        self.versionCheckThread = versionchecker.NotifyNewVersionThread()
        self.versionCheckThread.connect(self.versionCheckThread, SIGNAL("newer_version"), self.notifyNewerVersion)
        self.versionCheckThread.start()

        self.statisticsThread = MapStatisticsThread()
        self.connect(self.statisticsThread, SIGNAL("statistic_data_update"), self.updateStatisticsOnMap)
        self.statisticsThread.start()
        # statisticsThread is blocked until first call of requestStatistics


    def setupMap(self, initialize=False):
        self.mapTimer.stop()
        self.filewatcherThread.paused = True

        logging.info("Finding map file")
        regionName = self.cache.getFromCache("region_name")
        if not regionName:
            regionName = "Providence"
        svg = None
        try:
            with open(resourcePath("vi/ui/res/mapdata/{0}.svg".format(regionName))) as svgFile:
                svg = svgFile.read()
        except Exception as e:
            pass

        try:
            self.dotlan = dotlan.Map(regionName, svg)
        except dotlan.DotlanException as e:
            logging.error(e)
            QMessageBox.critical(None, "Error getting map", six.text_type(e), "Quit")
            sys.exit(1)

        if self.dotlan.outdatedCacheError:
            e = self.dotlan.outdatedCacheError
            diagText = "Something went wrong getting map data. Proceeding with older cached data. " \
                       "Check for a newer version and inform the maintainer.\n\nError: {0} {1}".format(type(e), six.text_type(e))
            logging.warn(diagText)
            QMessageBox.warning(None, "Using map from cache", diagText, "Ok")


        # Load the jumpbridges
        logging.critical("Load jump bridges")
        self.setJumpbridges(self.cache.getFromCache("jumpbridge_url"))
        self.systems = self.dotlan.systems

        if self.knownPlayerNames:
            for char in self.knownPlayerNames:
                loc = self.cache.getFromCache("player_" + char + "_loc")
                if loc:
                    logging.warn("Found known character [{0}], located in [{1}]".format(char, loc))
                    self.setLocation(char, loc)

        logging.critical("Creating chat parser")
        self.chatparser = ChatParser(self.pathToLogs, self.roomnames, self.systems)

        # Menus - only once
        if initialize:
            logging.critical("Initializing contextual menus")

            # Add a contextual menu to the mapView
            def mapContextMenuEvent(event):
                #if QApplication.activeWindow() or QApplication.focusWidget():
                self.mapView.contextMenu.exec_(self.mapToGlobal(QPoint(event.x(), event.y())))
            self.mapView.contextMenuEvent = mapContextMenuEvent
            self.mapView.contextMenu = self.trayIcon.contextMenu()

            # Clicking links
            self.mapView.connect(self.mapView, SIGNAL("linkClicked(const QUrl&)"), self.mapLinkClicked)

            # Also set up our app menus
            if not regionName:
                self.providenceCatchRegionAction.setChecked(True)
            elif regionName.startswith("Providencecatch"):
                self.providenceCatchRegionAction.setChecked(True)
            elif regionName.startswith("Catch"):
                self.catchRegionAction.setChecked(True)
            elif regionName.startswith("Providence"):
                self.providenceRegionAction.setChecked(True)
            elif regionName.startswith("Querious"):
                self.queriousRegionAction.setChecked(True)
            else:
                self.chooseRegionAction.setChecked(True)
        self.jumpbridgesButton.setChecked(False)
        self.statisticsButton.setChecked(False)

        # Update the new map view, then clear old statistics from the map and request new
        logging.critical("Updating the map")
        self.updateMapView()
        self.setInitialMapPositionForRegion(regionName)
        self.mapTimer.start(MAP_UPDATE_INTERVAL_MSECS)
        # Allow the file watcher to run now that all else is set up
        self.filewatcherThread.paused = False
        logging.critical("Map setup complete")


    # def eventFilter(self, obj, event):
    #     if event.type() == QtCore.QEvent.WindowDeactivate:
    #         self.enableContextMenu(False)
    #         return True
    #     elif event.type() == QtCore.QEvent.WindowActivate:
    #         self.enableContextMenu(True)
    #         return True
    #     return False


    def startClipboardTimer(self):
        """
            Start a timer to check the keyboard for changes and kos check them,
            first initializing the content so we dont kos check from random content
        """
        self.oldClipboardContent = tuple(six.text_type(self.clipboard.text()))
        self.connect(self.clipboardTimer, SIGNAL("timeout()"), self.clipboardChanged)
        self.clipboardTimer.start(CLIPBOARD_CHECK_INTERVAL_MSECS)


    def stopClipboardTimer(self):
        if self.clipboardTimer:
           self.disconnect(self.clipboardTimer, SIGNAL("timeout()"), self.clipboardChanged)
           self.clipboardTimer.stop()


    def closeEvent(self, event):
        """
            Persisting things to the cache before closing the window
        """
        # Known playernames
        if self.knownPlayerNames:
            value = ",".join(self.knownPlayerNames)
            self.cache.putIntoCache("known_player_names", value, 60 * 60 * 24 * 30)

        # Program state to cache (to read it on next startup)
        settings = ((None, "restoreGeometry", str(self.saveGeometry())), (None, "restoreState", str(self.saveState())),
                    ("splitter", "restoreGeometry", str(self.splitter.saveGeometry())),
                    ("splitter", "restoreState", str(self.splitter.saveState())),
                    ("mapView", "setZoomFactor", self.mapView.zoomFactor()),
                    (None, "updateChatFontSize", ChatEntryWidget.TEXT_SIZE),
                    (None, "changeOpacity", self.opacityGroup.checkedAction().opacity),
                    (None, "changeAlwaysOnTop", self.alwaysOnTopAction.isChecked()),
                    (None, "changeShowAvatars", self.showChatAvatarsAction.isChecked()),
                    (None, "changeAlarmDistance", self.alarmDistance),
                    (None, "changeSound", self.activateSoundAction.isChecked()),
                    (None, "changeChatVisibility", self.showChatAction.isChecked()),
                    (None, "loadInitialMapPositions", self.mapPositionsDict),
                    (None, "setSoundVolume", SoundManager().soundVolume),
                    (None, "changeFrameless", self.framelessWindowAction.isChecked()),
                    (None, "changeUseSpokenNotifications", self.useSpokenNotificationsAction.isChecked()),
                    (None, "changeKosCheckClipboard", self.kosClipboardActiveAction.isChecked()),
                    (None, "changeAutoScanIntel", self.scanIntelForKosRequestsEnabled))
        self.cache.putIntoCache("settings", str(settings), 60 * 60 * 24 * 30)

        # Stop the threads
        try:
            SoundManager().quit()
            self.avatarFindThread.quit()
            self.avatarFindThread.wait()
            self.filewatcherThread.quit()
            self.filewatcherThread.wait()
            self.kosRequestThread.quit()
            self.kosRequestThread.wait()
            self.versionCheckThread.quit()
            self.versionCheckThread.wait()
            self.statisticsThread.quit()
            self.statisticsThread.wait()
        except Exception:
            pass
        self.trayIcon.hide()
        event.accept()


    def notifyNewerVersion(self, newestVersion):
        self.trayIcon.showMessage("Newer Version", ("An update is available for Vintel.\nhttps://github.com/mkumpan/vintel/releases"), 1)

    def changeChatVisibility(self, newValue=None):
        if newValue is None:
            newValue = self.showChatAction.isChecked()
        self.showChatAction.setChecked(newValue)
        self.chatbox.setVisible(newValue)

    def changeKosCheckClipboard(self, newValue=None):
        if newValue is None:
            newValue = self.kosClipboardActiveAction.isChecked()
        self.kosClipboardActiveAction.setChecked(newValue)
        if newValue:
            self.startClipboardTimer()
        else:
            self.stopClipboardTimer()

    def changeAutoScanIntel(self, newValue=None):
        if newValue is None:
            newValue = self.autoScanIntelAction.isChecked()
        self.autoScanIntelAction.setChecked(newValue)
        self.scanIntelForKosRequestsEnabled = newValue

    def changeUseSpokenNotifications(self, newValue=None):
        if SoundManager().platformSupportsSpeech():
            if newValue is None:
                newValue = self.useSpokenNotificationsAction.isChecked()
            self.useSpokenNotificationsAction.setChecked(newValue)
            SoundManager().setUseSpokenNotifications(newValue)
        else:
            self.useSpokenNotificationsAction.setChecked(False)
            self.useSpokenNotificationsAction.setEnabled(False)

    def changeOpacity(self, newValue=None):
        if newValue is not None:
            for action in self.opacityGroup.actions():
                if action.opacity == newValue:
                    action.setChecked(True)
        action = self.opacityGroup.checkedAction()
        self.setWindowOpacity(action.opacity)

    def changeSound(self, newValue=None, disable=False):
        if disable:
            self.activateSoundAction.setChecked(False)
            self.activateSoundAction.setEnabled(False)
            self.soundSetupAction.setEnabled(False)
            #self.soundButton.setEnabled(False)
            QMessageBox.warning(None, "Sound disabled",
                                      "The lib 'pyglet' which is used to play sounds cannot be found, ""so the soundsystem is disabled.\nIf you want sound, please install the 'pyglet' library. This warning will not be shown again.",
                                      "OK")
        else:
            if newValue is None:
                newValue = self.activateSoundAction.isChecked()
            self.activateSoundAction.setChecked(newValue)
            SoundManager().soundActive = newValue

    def changeAlwaysOnTop(self, newValue=None):
        if newValue is None:
            newValue = self.alwaysOnTopAction.isChecked()
        self.hide()
        self.alwaysOnTopAction.setChecked(newValue)
        if newValue:
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & (~QtCore.Qt.WindowStaysOnTopHint))
        self.show()

    def changeFrameless(self, newValue=None):
        if newValue is None:
            newValue = not self.frameButton.isVisible()
        self.hide()
        if newValue:
            self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
            self.changeAlwaysOnTop(True)
        else:
            self.setWindowFlags(self.windowFlags() & (~QtCore.Qt.FramelessWindowHint))
        self.menubar.setVisible(not newValue)
        self.frameButton.setVisible(newValue)
        self.framelessWindowAction.setChecked(newValue)

        for cm in TrayContextMenu.instances:
            cm.framelessCheck.setChecked(newValue)
        self.show()

    def changeShowAvatars(self, newValue=None):
        if newValue is None:
            newValue = self.showChatAvatarsAction.isChecked()
        self.showChatAvatarsAction.setChecked(newValue)
        ChatEntryWidget.SHOW_AVATAR = newValue
        for entry in self.chatEntries:
            entry.avatarLabel.setVisible(newValue)

    def updateChatFontSize(self):
        if ChatEntryWidget.TEXT_SIZE:

            for row in range(self.chatListWidget.count()):
                chatListWidgetItem = self.chatListWidget.item(row)
                chatEntryWidget = self.chatListWidget.itemWidget(chatListWidgetItem)

                chatEntryWidget.changeFontSize(ChatEntryWidget.TEXT_SIZE)
                chatListWidgetItem.setSizeHint(chatEntryWidget.sizeHint())


    def chatSmaller(self):
        ChatEntryWidget.TEXT_SIZE -= 1
        self.updateChatFontSize()


    def chatLarger(self):
        ChatEntryWidget.TEXT_SIZE += 1
        self.updateChatFontSize()


    def changeAlarmDistance(self, distance):
        self.alarmDistance = distance
        for cm in TrayContextMenu.instances:
            for action in cm.distanceGroup.actions():
                if action.alarmDistance == distance:
                    action.setChecked(True)
        self.trayIcon.alarmDistance = distance


    def changeJumpbridgesVisibility(self):
        newValue = self.dotlan.changeJumpbridgesVisibility()
        self.jumpbridgesButton.setChecked(newValue)
        self.updateMapView()


    def changeStatisticsVisibility(self):
        newValue = self.dotlan.changeStatisticsVisibility()
        self.statisticsButton.setChecked(newValue)
        self.updateMapView()
        if newValue:
            self.statisticsThread.requestStatistics()


    def clipboardChanged(self, mode=0):
        if not (mode == 0 and self.kosClipboardActiveAction.isChecked() and self.clipboard.mimeData().hasText()):
            return
        content = six.text_type(self.clipboard.text())
        contentTuple = tuple(content)
        # Limit redundant kos checks
        if contentTuple != self.oldClipboardContent:
            parts = tuple(content.split("\n"))
            knownPlayers = self.knownPlayerNames
            for part in parts:
                # Make sure user is in the content (this is a check of the local system in Eve).
                # also, special case for when you have no knonwnPlayers (initial use)
                if not knownPlayers or part in knownPlayers:
                    self.trayIcon.setIcon(self.taskbarIconWorking)
                    self.kosRequestThread.addRequest(parts, "clipboard", True)
                    break
            self.oldClipboardContent = contentTuple


    def mapLinkClicked(self, url):
        systemName = six.text_type(url.path().split("/")[-1]).upper()
        system = self.systems[str(systemName)]
        sc = SystemChat(self, SystemChat.SYSTEM, system, self.chatEntries, self.knownPlayerNames)
        sc.connect(self, SIGNAL("chat_message_added"), sc.addChatEntry)
        sc.connect(self, SIGNAL("avatar_loaded"), sc.newAvatarAvailable)
        sc.connect(sc, SIGNAL("location_set"), self.setLocation)
        sc.show()


    def markSystemOnMap(self, systemname):
        self.systems[six.text_type(systemname)].mark()
        self.updateMapView()


    def setLocation(self, char, newSystem):
        for system in self.systems.values():
            system.removeLocatedCharacter(char)
        if not newSystem == "?" and newSystem in self.systems:
            self.systems[newSystem].addLocatedCharacter(char)
            self.setMapContent(self.dotlan.svg)
            self.cache.putIntoCache("player_" + char + "_loc", newSystem)


    def setMapContent(self, content):
        if self.initialMapPosition is None:
            scrollPosition = self.mapView.page().mainFrame().scrollPosition()
        else:
            scrollPosition = self.initialMapPosition
        self.mapView.setContent(content)
        self.mapView.page().mainFrame().setScrollPosition(scrollPosition)
        self.mapView.page().setLinkDelegationPolicy(QWebPage.DelegateAllLinks)

        # Make sure we have positioned the window before we nil the initial position;
        # even though we set it, it may not take effect until the map is fully loaded
        scrollPosition = self.mapView.page().mainFrame().scrollPosition()
        if scrollPosition.x() or scrollPosition.y():
            self.initialMapPosition = None


    def loadInitialMapPositions(self, newDictionary):
        self.mapPositionsDict = newDictionary


    def setInitialMapPositionForRegion(self, regionName):
        try:
            if not regionName:
                regionName = self.cache.getFromCache("region_name")
            if regionName:
                xy = self.mapPositionsDict[regionName]
                self.initialMapPosition = QPoint(xy[0], xy[1])
        except Exception:
            pass


    def mapPositionChanged(self, dx, dy, rectToScroll):
        regionName = self.cache.getFromCache("region_name")
        if regionName:
            scrollPosition = self.mapView.page().mainFrame().scrollPosition()
            self.mapPositionsDict[regionName] = (scrollPosition.x(), scrollPosition.y())


    def showChatroomChooser(self):
        chooser = ChatroomsChooser(self)
        chooser.connect(chooser, SIGNAL("rooms_changed"), self.changedRoomnames)
        chooser.show()


    def showJumbridgeChooser(self):
        url = self.cache.getFromCache("jumpbridge_url")
        chooser = JumpbridgeChooser(self, url)
        chooser.connect(chooser, SIGNAL("set_jumpbridge_url"), self.setJumpbridges)
        chooser.show()


    def setSoundVolume(self, value):
        SoundManager().setSoundVolume(value)


    def setJumpbridges(self, url):
        if url is None:
            QMessageBox.warning(None, "Loading jumpbridges failed!", "Invalid URL. Got: {0}".format(six.text_type(url)), "OK")
            return

        try:
            data = []
            resp = requests.get(url)
            for line in resp.iter_lines(decode_unicode=True):
                parts = line.strip().split()
                if len(parts) == 5:
                    data.append([
                        parts[0],
                        parts[2],
                        parts[3]
                    ])
                if len(parts) == 3:
                    data.append(parts)

            self.dotlan.setJumpbridges(data)
            self.cache.putIntoCache("jumpbridge_url", url, 60 * 60 * 24 * 365 * 8)
        except Exception as e:
            QMessageBox.warning(None, "Loading jumpbridges failed!", "Error: {0}".format(six.text_type(e)), "OK")


    def handleRegionMenuItemSelected(self, menuAction=None):
        self.catchRegionAction.setChecked(False)
        self.providenceRegionAction.setChecked(False)
        self.queriousRegionAction.setChecked(False)
        self.providenceCatchRegionAction.setChecked(False)
        self.providenceCatchCompactRegionAction.setChecked(False)
        self.chooseRegionAction.setChecked(False)
        if menuAction:
            menuAction.setChecked(True)
            regionName = six.text_type(menuAction.property("regionName").toString())
            regionName = dotlan.convertRegionName(regionName)
            Cache().putIntoCache("region_name", regionName, 60 * 60 * 24 * 365)
            self.setupMap()


    def showRegionChooser(self):
        def handleRegionChosen():
            self.handleRegionMenuItemSelected(None)
            self.chooseRegionAction.setChecked(True)
            self.setupMap()

        self.chooseRegionAction.setChecked(False)
        chooser = RegionChooser(self)
        self.connect(chooser, SIGNAL("new_region_chosen"), handleRegionChosen)
        chooser.show()


    def addMessageToIntelChat(self, message):
        scrollToBottom = False
        if (self.chatListWidget.verticalScrollBar().value() == self.chatListWidget.verticalScrollBar().maximum()):
            scrollToBottom = True
        chatEntryWidget = ChatEntryWidget(message)
        listWidgetItem = QtGui.QListWidgetItem(self.chatListWidget)
        self.chatListWidget.addItem(listWidgetItem)
        self.chatListWidget.setItemWidget(listWidgetItem, chatEntryWidget)
        self.avatarFindThread.addChatEntry(chatEntryWidget)
        self.chatEntries.append(chatEntryWidget)
        self.connect(chatEntryWidget, SIGNAL("mark_system"), self.markSystemOnMap)
        self.emit(SIGNAL("chat_message_added"), chatEntryWidget)

        listWidgetItem.setSizeHint(chatEntryWidget.sizeHint())

        self.pruneMessages()
        if scrollToBottom:
            self.chatListWidget.scrollToBottom()


    def pruneMessages(self):
        try:
            now = time.mktime(evegate.currentEveTime().timetuple())
            for row in range(self.chatListWidget.count()):
                chatListWidgetItem = self.chatListWidget.item(row)
                chatEntryWidget = self.chatListWidget.itemWidget(chatListWidgetItem)
                message = chatEntryWidget.message
                if now - time.mktime(message.timestamp.timetuple()) > MESSAGE_EXPIRY_SECS:
                    self.chatEntries.remove(chatEntryWidget)
                    self.chatListWidget.takeItem(row)

                    for widgetInMessage in message.widgets:
                        widgetInMessage.removeItemWidget(chatListWidgetItem)
                else:
                    break
        except Exception as e:
            logging.error(e)


    def showKosResult(self, state, text, requestType, hasKos):
        if not self.scanIntelForKosRequestsEnabled:
            return
        try:
            if hasKos:
                SoundManager().playSound("kos", text)
            if state == "ok":
                if requestType == "xxx":  # An xxx request out of the chat
                    self.trayIcon.showMessage("Player KOS-Check", text, 1)
                elif requestType == "clipboard":  # request from clipboard-change
                    if len(text) <= 0:
                        text = "None KOS"
                    self.trayIcon.showMessage("Your KOS-Check", text, 1)
                text = text.replace("\n\n", "<br>")
                message = chatparser.chatparser.Message("Vintel KOS-Check", text, evegate.currentEveTime(), "VINTEL",
                                                        [], states.NOT_CHANGE, text.upper(), text)
                self.addMessageToIntelChat(message)
            elif state == "error":
                self.trayIcon.showMessage("KOS Failure", text, 3)
        except Exception:
            pass
        self.trayIcon.setIcon(self.taskbarIconQuiescent)


    def changedRoomnames(self, newRoomnames):
        self.cache.putIntoCache("room_names", u",".join(newRoomnames), 60 * 60 * 24 * 365 * 5)
        self.chatparser.rooms = newRoomnames


    def showInfo(self):
        infoDialog = QtGui.QDialog(self)
        uic.loadUi(resourcePath("vi/ui/Info.ui"), infoDialog)
        infoDialog.versionLabel.setText(u"Version: {0}".format(vi.version.VERSION))
        infoDialog.logoLabel.setPixmap(QtGui.QPixmap(resourcePath("vi/ui/res/logo.png")))
        infoDialog.connect(infoDialog.closeButton, SIGNAL("clicked()"), infoDialog.accept)
        infoDialog.show()


    def showSoundSetup(self):
        dialog = QtGui.QDialog(self)
        uic.loadUi(resourcePath("vi/ui/SoundSetup.ui"), dialog)
        dialog.volumeSlider.setValue(SoundManager().soundVolume)
        dialog.connect(dialog.volumeSlider, SIGNAL("valueChanged(int)"), SoundManager().setSoundVolume)
        dialog.connect(dialog.testSoundButton, SIGNAL("clicked()"), SoundManager().playSound)
        dialog.connect(dialog.closeButton, SIGNAL("clicked()"), dialog.accept)
        dialog.show()


    def systemTrayActivated(self, reason):
        if reason == QtGui.QSystemTrayIcon.Trigger:
            if self.isMinimized():
                self.showNormal()
                self.activateWindow()
            elif not self.isActiveWindow():
                self.activateWindow()
            else:
                self.showMinimized()


    def updateAvatarOnChatEntry(self, chatEntry, avatarData):
        updated = chatEntry.updateAvatar(avatarData)
        if not updated:
            self.avatarFindThread.addChatEntry(chatEntry, clearCache=True)
        else:
            self.emit(SIGNAL("avatar_loaded"), chatEntry.message.user, avatarData)


    def updateStatisticsOnMap(self, data):
        if not self.statisticsButton.isChecked():
            return
        if data["result"] == "ok":
            self.dotlan.addSystemStatistics(data["statistics"])
        elif data["result"] == "error":
            text = data["text"]
            self.trayIcon.showMessage("Loading statstics failed", text, 3)
            logging.error("updateStatisticsOnMap, error: %s" % text)


    def updateMapView(self):
        logging.debug("Updating map start")
        self.setMapContent(self.dotlan.svg)
        logging.debug("Updating map complete")


    def zoomMapIn(self):
        self.mapView.setZoomFactor(self.mapView.zoomFactor() + 0.1)


    def zoomMapOut(self):
        self.mapView.setZoomFactor(self.mapView.zoomFactor() - 0.1)


    def logFileChanged(self, path):
        messages = self.chatparser.fileModified(path)
        for message in messages:
            # If players location has changed
            if message.status == states.LOCATION:
                self.knownPlayerNames.add(message.user)
                self.setLocation(message.user, message.systems[0])
            elif message.status == states.KOS_STATUS_REQUEST:
                # Do not accept KOS requests from any but monitored intel channels
                # as we don't want to encourage the use of xxx in those channels.
                if not message.room in self.roomnames:
                    text = message.message[4:]
                    text = text.replace("  ", ",")
                    parts = (name.strip() for name in text.split(","))
                    self.trayIcon.setIcon(self.taskbarIconWorking)
                    self.kosRequestThread.addRequest(parts, "xxx", False)
            # Otherwise consider it a 'normal' chat message
            elif message.user not in ("EVE-System", "EVE System", u"Система EVE") and message.status != states.IGNORE:
                self.addMessageToIntelChat(message)
                # For each system that was mentioned in the message, check for alarm distance to the current system
                # and alarm if within alarm distance.
                systemList = self.dotlan.systems
                if message.systems:
                    for system in message.systems:
                        systemname = system.name
                        systemList[systemname].setStatus(message.status)
                        if message.status in (states.REQUEST, states.ALARM) and message.user not in self.knownPlayerNames:
                            alarmDistance = self.alarmDistance if message.status == states.ALARM else 0
                            for nSystem, data in system.getNeighbours(alarmDistance).items():
                                distance = data["distance"]
                                chars = nSystem.getLocatedCharacters()
                                if len(chars) > 0 and message.user not in chars:
                                    self.trayIcon.showNotification(message, system.name, ", ".join(chars), distance)
                self.setMapContent(self.dotlan.svg)


class ChatroomsChooser(QtGui.QDialog):
    def __init__(self, parent):
        QtGui.QDialog.__init__(self, parent)
        uic.loadUi(resourcePath("vi/ui/ChatroomsChooser.ui"), self)
        self.connect(self.defaultButton, SIGNAL("clicked()"), self.setDefaults)
        self.connect(self.cancelButton, SIGNAL("clicked()"), self.accept)
        self.connect(self.saveButton, SIGNAL("clicked()"), self.saveClicked)
        cache = Cache()
        roomnames = cache.getFromCache("room_names")
        if not roomnames:
            roomnames = u"TheCitadel,North Provi Intel,North Catch Intel,North Querious Intel"
        self.roomnamesField.setPlainText(roomnames)


    def saveClicked(self):
        text = six.text_type(self.roomnamesField.toPlainText())
        rooms = [six.text_type(name.strip()) for name in text.split(",")]
        self.accept()
        self.emit(SIGNAL("rooms_changed"), rooms)


    def setDefaults(self):
        self.roomnamesField.setPlainText(u"TheCitadel,North Provi Intel,North Catch Intel,North Querious Intel")


class RegionChooser(QtGui.QDialog):
    def __init__(self, parent):
        QtGui.QDialog.__init__(self, parent)
        uic.loadUi(resourcePath("vi/ui/RegionChooser.ui"), self)
        self.connect(self.cancelButton, SIGNAL("clicked()"), self.accept)
        self.connect(self.saveButton, SIGNAL("clicked()"), self.saveClicked)
        cache = Cache()
        regionName = cache.getFromCache("region_name")
        if not regionName:
            regionName = u"Providence"
        self.regionNameField.setPlainText(regionName)


    def saveClicked(self):
        text = six.text_type(self.regionNameField.toPlainText())
        text = dotlan.convertRegionName(text)
        self.regionNameField.setPlainText(text)
        correct = False
        try:
            url = dotlan.Map.DOTLAN_BASIC_URL.format(text)
            content = requests.get(url).text
            if u"not found" in content:
                correct = False
                # Fallback -> ships vintel with this map?
                try:
                    with open(resourcePath("vi/ui/res/mapdata/{0}.svg".format(text))) as _:
                        correct = True
                except Exception as e:
                    logging.error(e)
                    correct = False
                if not correct:
                    QMessageBox.warning(self, u"No such region!", u"I can't find a region called '{0}'".format(text))
            else:
                correct = True
        except Exception as e:
            QMessageBox.critical(self, u"Something went wrong!", u"Error while testing existing '{0}'".format(str(e)))
            logging.error(e)
            correct = False
        if correct:
            Cache().putIntoCache("region_name", text, 60 * 60 * 24 * 365)
            self.accept()
            self.emit(SIGNAL("new_region_chosen"))


class SystemChat(QtGui.QDialog):
    SYSTEM = 0

    def __init__(self, parent, chatType, selector, chatEntries, knownPlayerNames):
        QtGui.QDialog.__init__(self, parent)
        uic.loadUi(resourcePath("vi/ui/SystemChat.ui"), self)
        self.parent = parent
        self.chatType = 0
        self.selector = selector
        self.chatEntries = []
        for entry in chatEntries:
            self.addChatEntry(entry)
        titleName = ""
        if self.chatType == SystemChat.SYSTEM:
            titleName = self.selector.name
            self.system = selector
        for name in knownPlayerNames:
            self.playerNamesBox.addItem(name)
        self.setWindowTitle("Chat for {0}".format(titleName))
        self.connect(self.closeButton, SIGNAL("clicked()"), self.closeDialog)
        self.connect(self.alarmButton, SIGNAL("clicked()"), self.setSystemAlarm)
        self.connect(self.clearButton, SIGNAL("clicked()"), self.setSystemClear)
        self.connect(self.locationButton, SIGNAL("clicked()"), self.locationSet)


    def _addMessageToChat(self, message, avatarPixmap):
        scrollToBottom = False
        if (self.chat.verticalScrollBar().value() == self.chat.verticalScrollBar().maximum()):
            scrollToBottom = True
        entry = ChatEntryWidget(message)
        entry.avatarLabel.setPixmap(avatarPixmap)
        listWidgetItem = QtGui.QListWidgetItem(self.chat)
        listWidgetItem.setSizeHint(entry.sizeHint())
        self.chat.addItem(listWidgetItem)
        self.chat.setItemWidget(listWidgetItem, entry)
        self.chatEntries.append(entry)
        self.connect(entry, SIGNAL("mark_system"), self.parent.markSystemOnMap)
        if scrollToBottom:
            self.chat.scrollToBottom()


    def addChatEntry(self, entry):
        if self.chatType == SystemChat.SYSTEM:
            message = entry.message
            avatarPixmap = entry.avatarLabel.pixmap()
            if self.selector in message.systems:
                self._addMessageToChat(message, avatarPixmap)


    def locationSet(self):
        char = six.text_type(self.playerNamesBox.currentText())
        self.emit(SIGNAL("location_set"), char, self.system.name)


    def newAvatarAvailable(self, charname, avatarData):
        for entry in self.chatEntries:
            if entry.message.user == charname:
                entry.updateAvatar(avatarData)


    def setSystemAlarm(self):
        self.system.setStatus(states.ALARM)
        self.parent.updateMapView()


    def setSystemClear(self):
        self.system.setStatus(states.CLEAR)
        self.parent.updateMapView()


    def closeDialog(self):
        self.accept()


class ChatEntryWidget(QtGui.QWidget):
    TEXT_SIZE = 11
    SHOW_AVATAR = True
    questionMarkPixmap = None

    def __init__(self, message):
        QtGui.QWidget.__init__(self)
        if not self.questionMarkPixmap:
            self.questionMarkPixmap = QtGui.QPixmap(resourcePath("vi/ui/res/qmark.png")).scaledToHeight(32)
        uic.loadUi(resourcePath("vi/ui/ChatEntry.ui"), self)
        self.avatarLabel.setPixmap(self.questionMarkPixmap)
        self.message = message
        self.updateText()
        self.changeFontSize(ChatEntryWidget.TEXT_SIZE)
        self.connect(self.textLabel, SIGNAL("linkActivated(QString)"), self.linkClicked)
        if not ChatEntryWidget.SHOW_AVATAR:
            self.avatarLabel.setVisible(False)


    def linkClicked(self, link):
        link = six.text_type(link)
        function, parameter = link.split("/", 1)
        if function == "mark_system":
            self.emit(SIGNAL("mark_system"), parameter)
        elif function == "link":
            webbrowser.open(parameter)


    def updateText(self):
        time = datetime.datetime.strftime(self.message.timestamp, "%H:%M:%S")
        text = u"<small>{time} - <b>{user}</b> - <i>{room}</i></small><br>{text}".format(user=self.message.user,
                                                                                         room=self.message.room,
                                                                                         time=time,
                                                                                         text=self.message.message)
        self.textLabel.setText(text)


    def updateAvatar(self, avatarData):
        image = QImage.fromData(avatarData)
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return False
        scaledAvatar = pixmap.scaled(32, 32)
        self.avatarLabel.setPixmap(scaledAvatar)
        return True


    def changeFontSize(self, newSize):
        font = self.textLabel.font()
        font.setPointSize(newSize)
        self.textLabel.setFont(font)


class JumpbridgeChooser(QtGui.QDialog):
    def __init__(self, parent, url):
        if not url:
            url = ""

        QtGui.QDialog.__init__(self, parent)
        uic.loadUi(resourcePath("vi/ui/JumpbridgeChooser.ui"), self)
        self.connect(self.saveButton, SIGNAL("clicked()"), self.savePath)
        self.connect(self.cancelButton, SIGNAL("clicked()"), self.accept)
        self.urlField.setText(url)
        # loading format explanation from textfile
        with open(resourcePath("docs/jumpbridgeformat.txt")) as f:
            self.formatInfoField.setPlainText(f.read())


    def savePath(self):
        try:
            url = six.text_type(self.urlField.text())
            if url != "":
                requests.get(url).text
            self.emit(SIGNAL("set_jumpbridge_url"), url)
            self.accept()
        except Exception as e:
            QMessageBox.critical(None, "Finding Jumpbridgedata failed", "Error: {0}".format(six.text_type(e)), "OK")
