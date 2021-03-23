import logging
from packaging import version as vsn

import requests
from PyQt4.QtCore import QThread, SIGNAL

from vi import version


def getNewestVersion():
    try:
        url = "https://raw.githubusercontent.com/mkumpan/vintel/master/src/vi/version.py"
        lines = requests.get(url).iter_lines(decode_unicode=True)

        for line in lines:
            if line.startswith("VERSION"):
                parts = line.strip().split()
                newestVersion = parts[2].strip('"')
                return newestVersion

        logging.error("No version in file.")
        return "0.0"

    except Exception as e:
        logging.error("Failed version-request: %s", e)
        return "0.0"


class NotifyNewVersionThread(QThread):
    def __init__(self):
        QThread.__init__(self)
        self.alerted = False

    def run(self):
        if not self.alerted:
            try:
                # Is there a newer version available?
                newestVersion = getNewestVersion()
                if newestVersion and vsn.parse(newestVersion) > vsn.parse(version.VERSION):
                    self.emit(SIGNAL("newer_version"), newestVersion)
                    self.alerted = True
            except Exception as e:
                logging.error("Failed NotifyNewVersionThread: %s", e)
