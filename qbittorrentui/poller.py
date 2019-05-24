import logging
from time import time, sleep

from qbittorrentui.connector import Connector
from qbittorrentui.connector import ConnectorError
from qbittorrentui.events import sync_maindata_ready
from qbittorrentui.events import refresh_torrent_list_with_remote_data_now

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2


class Poller:
    client: Connector

    def __init__(self, main):
        """
        Background poller to qbittorrent.
        :param main:
        """
        self.main = main
        self.client = main.torrent_client
        self.rid = 0
        self.sync_maindata_update_in_progress = False

        # signals to respond to
        refresh_torrent_list_with_remote_data_now.connect(receiver=self._one_sync_maindata_loop)

    def start_sync_maindata_loop(self):
        while True:
            start_time = time()
            try:
                self._one_sync_maindata_loop()
            finally:
                poll_time = time() - start_time
                if poll_time < POLL_INTERVAL:
                    sleep(POLL_INTERVAL - poll_time)

    def _one_sync_maindata_loop(self, *a, **kw):
        try:
            self._run_sync_maindata_update()
        except ConnectorError:
            logger.info("Poller could not connect to request sync maindata")
        except Exception:
            logger.exception("MainData poller daemon crashed")
        finally:
            self.sync_maindata_update_in_progress = False

    def _run_sync_maindata_update(self, *a, **kw):
        if self.sync_maindata_update_in_progress is True:
            logger.info("Sync maindata update already in progress")
            return

        # proceed with update if one isn't already happening
        self.sync_maindata_update_in_progress = True

        logger.info("Requesting maindata (RID: %s)" % self.rid)
        start_time = time()
        md = self.client.sync_maindata(self.rid)
        response_time = time() - start_time
        logger.info("Received maindata (RID: %s) in %.3f secs" % (md.get('rid', ""), response_time))

        # if no one is listening, reset syncing just in case the next send is the first time a receiver connects
        if sync_maindata_ready.receivers:
            logger.info("Sending sync maindata")
            sync_maindata_ready.send("client poller", md=md)
            self.rid = md.get('rid', self.rid)
        else:
            logger.info("Sync maindata reset")
            self.rid = 0