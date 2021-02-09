import configparser
import os


class Configuration(configparser.ConfigParser):
    def __init__(self):
        super(Configuration, self).__init__()
        # load default configuration
        DIRNAME = os.path.split(__file__)[0]
        self.read(os.path.join(DIRNAME, 'default.ini'))
        self._section = 'DEFAULT'

    def set_default_section(self, section: str = ""):
        self._section = section

    def get(self, option: str, section: str = None):
        if section:
            return super(Configuration, self).get(section=section, option=option, raw=True)
        return super(Configuration, self).get(section=self._section, option=option, raw=True)

    def set(self, option: str, value: str, section: str = None):
        if section:
            super(Configuration, self).set(section=section, option=option, value=value)
        super(Configuration, self).set(section=self._section, option=option, value=value)


# CONSTANTS
APPLICATION_NAME = 'qBittorrenTUI'

STATE_MAP_FOR_DISPLAY = {'pausedUP': 'Completed',
                         'uploading': 'Seeding',
                         'stalledUP': 'Seeding',
                         'forcedUP': '[F] Seeding',
                         'queuedDL': 'Queued',
                         'queuedUP': 'Queued',
                         'pausedDL': 'Paused',
                         'checkingDL': 'Checking',
                         'checkingUP': 'Checking',
                         'downloading': 'Downloading',
                         'forcedDL': '[F] Downloading',
                         'metaDL': 'Downloading',
                         'stalledDL': 'Stalled',
                         'allocating': 'Allocating',
                         'moving': 'Moving',
                         'missingfiles': 'Missing Files',
                         'error': 'Error',
                         'queuedForChecking': 'Queued for Checking',
                         'checkingResumeData': 'Checking Resume Data'}

TORRENT_LIST_FILTERING_STATE_MAP = {
    'downloading': [
        'downloading',
        'metaDL',
        'queuedDL',
        'stalledDL',
        'pausedDL',
        'forcedDL'
    ],
    'completed': [
        'uploading',
        'stalledUP',
        'checkingUP',
        'pausedUP',
        'queuedUP',
        'forcedUP',
    ],
    'active': [
        'metaDL',
        'downloading',
        'forcedDL',
        'uploading',
        'forcedUP',
        'moving',
    ],
    'inactive': [
        'pausedUP',
        'stalledUP',
        'stalledDL',
        'queuedDL',
        'queuedUP',
        'pausedDL',
        'checkingDL',
        'checkingUP',
        'allocating',
        'missingfiles',
        'error',
        'queuedForChecking',
        'checkingResumeData',
    ],
    'paused': [
        'pausedUP',
        'queuedDL',
        'queuedUP',
        'pausedDL',
        'missingfiles',
        'error',
        'queuedForChecking',
        'checkingResumeData',
    ],
    'resumed': [
        'uploading',
        'stalledUP',
        'forcedUP',
        'checkingDL',
        'checkingUP',
        'downloading',
        'forcedDL',
        'metaDL',
        'stalledDL',
        'allocating',
        'moving']
}

# CONFIGURATION STORE
config = Configuration()
