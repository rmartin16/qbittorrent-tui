import logging
import os
import queue
import threading
from copy import deepcopy
from time import time

from qbittorrentui._attrdict import AttrDict
from qbittorrentui.config import config
from qbittorrentui.connector import Connector
from qbittorrentui.connector import ConnectorError
from qbittorrentui.debug import log_timing
from qbittorrentui.events import connection_to_server_status
from qbittorrentui.events import reset_daemons
from qbittorrentui.events import run_server_command
from qbittorrentui.events import server_details_changed
from qbittorrentui.events import server_state_changed
from qbittorrentui.events import server_torrents_changed
from qbittorrentui.events import update_torrent_list_now
from qbittorrentui.events import update_torrent_window_now
from qbittorrentui.events import update_ui_from_daemon

logger = logging.getLogger(__name__)


class DaemonManager(threading.Thread):
    """
    Background daemon manager. Responsible for stopping and starting daemons,
    providing daemon interfaces to UI, and facilitate signaling of the UI.

    :param torrent_client:
    :param daemon_signal_fd:
    """
    def __init__(self, torrent_client: Connector, daemon_signal_fd: int):
        super(DaemonManager, self).__init__()

        self._wake_up = threading.Event()
        self._stop_request = threading.Event()
        self._daemon_signal_fd = daemon_signal_fd  # use os.write to send signals back to UI

        self._connection_status_q = queue.Queue()
        self._connection_status = DaemonManager.ConnectionStatus()

        ########################################
        # Create daemons and their interfaces
        #  (these interfaces MUST be thread-safe)
        ########################################
        # Sync MainData
        self.sync_maindata_d = SyncMainData(torrent_client)
        self.sync_maindata_q = self.sync_maindata_d.maindata_q

        # Sync Torrents
        self.sync_torrent_d = SyncTorrent(torrent_client)
        self.remove_sync_torrent_hash = self.sync_torrent_d.remove_sync_torrent_hash
        self.add_sync_torrent_hash = self.sync_torrent_d.add_sync_torrent_hash
        self.get_torrent_store = self.sync_torrent_d.get_torrent_store

        # Server Details
        self.server_details_d = ServerDetails(torrent_client)
        self.get_server_details = self.server_details_d.get_server_details
        self.get_server_preferences = self.server_details_d.get_server_preferences

        # Commands
        self.commands_d = Commands(torrent_client)
        self.run_command = self.commands_d.run_command

        ########################################
        # Signals
        ########################################
        update_torrent_list_now.connect(receiver=self.sync_maindata_d.set_wake_up)
        update_torrent_window_now.connect(receiver=self.sync_torrent_d.set_wake_up)
        run_server_command.connect(receiver=self.run_command)
        update_ui_from_daemon.connect(receiver=self.signal_ui)
        connection_to_server_status.connect(receiver=self._connection_status_notification)

        ########################################
        # Enumerate the daemons
        ########################################
        self.workers = [
            self.sync_maindata_d,
            self.sync_torrent_d,
            self.server_details_d,
            self.commands_d
        ]

    @property
    def signal_terminator(self):
        return '\n'

    @property
    def signal_delimiter(self):
        return '\t'

    def signal_ui(self, sender: str = "", signal: str = "", extra: list = None):
        if extra is not None:
            for x in extra:
                signal = f"{signal}{self.signal_delimiter}{x}"
        signal = f"{sender}{self.signal_delimiter}{signal}{self.signal_terminator}"
        if isinstance(self._daemon_signal_fd, int):
            os.write(self._daemon_signal_fd, signal.encode())
        else:
            raise Exception("Background daemon signal file descriptor is not valid. sender: %s" % sender)

    def _connection_status_notification(self, sender: str = "", success: bool = False):
        self._connection_status_q.put(dict(sender=sender, success=success))
        self._wake_up.set()

    def _process_connection_status_notification(self):
        while not self._connection_status_q.empty():
            notification = self._connection_status_q.get()
            sender = notification['sender']
            success = notification['success']
            if success is True:
                # if a connection was successful but a previous connection error was reported,
                # tell the UI the connection was reacquired
                if self._connection_status.connection_failure_reported:
                    self.signal_ui(sender="daemon manager", signal="connection_acquired")
                self._time_of_connection_failure = None
                self._connection_status.connection_failure_reported = False
                self._connection_status.never_connected = False
            else:
                if self._connection_status.never_connected is False:
                    if self._time_of_connection_failure is None:
                        self._time_of_connection_failure = time()
                    else:
                        # if a connection has been lost for a little while, report it to the UI
                        if time() - self._time_of_connection_failure > int(config.get("TIME_AFTER_CONNECTION_FAILURE_THAT_CONNECTION_IS_CONSIDERED_LOST")):
                            if self._connection_status.connection_failure_reported is False:
                                self.signal_ui(sender="daemon manager", signal="connection_lost")
                                self._connection_status.connection_failure_reported = True

    def stop(self):
        self._stop_request.set()
        self._wake_up.set()

    def run(self):
        # start workers
        for worker in self.workers:
            worker.start()

        # TODO: check if any workers died and restart them...maybe
        while not self._stop_request.is_set():
            try:
                self._wake_up.clear()
                self._process_connection_status_notification()
            except Exception:
                pass
            finally:
                self._wake_up.wait(timeout=int(config.get("DAEMON_LOOP_INTERVAL")))

        logger.info("Background manager received stop request")

        # request workers to stop
        for worker in self.workers:
            worker.stop('shutdown')
            worker.join(timeout=1)

        self.signal_ui("daemon manager", "close_pipe")
        os.close(self._daemon_signal_fd)

    class ConnectionStatus(object):
        def __init__(self):
            self.never_connected = True
            self.time_of_connection_failure = None
            self.connection_failure_reported = False


class Daemon(threading.Thread):
    """
    Base class for background daemons to send and receive data/commands with server.

    :param torrent_client:
    """

    def __init__(self, torrent_client: Connector):
        super(Daemon, self).__init__()
        self.setDaemon(daemonic=True)
        self.stop_request = threading.Event()
        self.wake_up = threading.Event()
        self.reset = threading.Event()

        self._loop_interval = int(config.get("DAEMON_LOOP_INTERVAL"))
        self._loop_success = False
        self._daemon_name = self.__class__.__name__

        self.client = torrent_client

        reset_daemons.connect(receiver=self.reset_signal)

    def reset_signal(self, *a):
        self.reset.set()

    def reset_daemon(self):
        pass

    def stop(self, *a):
        self.stop_request.set()
        self.set_wake_up(*a)

    def signal_ui(self, signal: str, extra: list = None):
        # right now, this is intercepted by background manager
        update_ui_from_daemon.send("%s" % self.__class__.__name__, signal=signal, extra=extra)

    def set_wake_up(self, sender):
        logging.info("Waking up %s (from %s)" % (self.__class__.__name__, sender))
        self.wake_up.set()

    def run(self):
        while not self.stop_request.is_set():
            start_time = time()
            try:
                start_time = time()
                self.wake_up.clear()
                if self.reset.is_set():
                    self.reset.clear()
                    self.reset_daemon()
                self._one_loop()
                log_timing(logger, "One loop", self, "daemon loop", start_time)
            except ConnectorError:
                logger.info("Daemon %s could not connect to server" % self.__class__.__name__)
                connection_to_server_status.send(f"{self._daemon_name}", success=False)
            except Exception:
                logger.info(f"Daemon {self._daemon_name} crashed", exc_info=True)
            finally:
                if self._loop_success:
                    connection_to_server_status.send(self._daemon_name, success=True)
                    self._loop_success = False
                # wait for next loop
                poll_time = time() - start_time
                if poll_time < self._loop_interval:
                    self.wake_up.wait(self._loop_interval - poll_time)

        logger.info("Daemon %s exiting" % self._daemon_name)

    def _one_loop(self):
        pass


class SyncMainData(Daemon):
    """
    Background daemon that syncs app with server

    :param torrent_client:
    """
    def __init__(self, torrent_client: Connector):
        super(SyncMainData, self).__init__(torrent_client)

        self.maindata_q = queue.Queue()
        self._rid = 0

    def _one_loop(self):
        # if no one is listening, reset syncing just in case the next send is the first time a receiver connects
        # TODO: add support to remotely reset RID when there's a new listener
        #  that way I don't need to directly reference this signal here
        if server_state_changed.receivers or server_torrents_changed.receivers:
            md = self.client.sync_maindata(self._rid)
            self.maindata_q.put(SyncMainData.MainData(md))
            self.signal_ui("sync_maindata_ready")
            # only start incrementing once everyone is listening
            if server_state_changed.receivers and server_torrents_changed.receivers:
                self._rid = md.get('rid', 0)  # reset syncing if '_rid' is missing from response...

            self._loop_success = True
        else:
            logger.info("No receivers for sync maindata...")
            self._rid = 0

    def reset_daemon(self):
        logger.info(f"{self._daemon_name} is resetting")
        self._rid = 0
        while not self.maindata_q.empty():
            self.maindata_q.get()

    class MainData(object):
        def __init__(self, md: dict):
            super(SyncMainData.MainData, self).__init__()
            self.full_update = md.get('full_update', False)
            self.server_state = md.get('server_state', dict())
            self.torrents_removed = md.get('torrents_removed', dict())
            self.torrents = md.get('torrents', dict())
            self.categories_removed = md.get('categories_removed', dict())
            self.categories = md.get('categories', dict())


class SyncTorrent(Daemon):
    """
    Background daemon that syncs data for Torrent Window.

    :param torrent_client:
    """
    def __init__(self, torrent_client: Connector):
        super(SyncTorrent, self).__init__(torrent_client)

        self._rid = dict()
        self._torrents_to_add_q = queue.Queue()
        self._torrents_to_remove_q = queue.Queue()
        self._torrent_hashes = list()

        self._torrent_stores = dict()
        self._torrent_store_lock = threading.RLock()

    def _one_loop(self):
        self._update_torrent_hashes_list()
        for torrent_hash in self._torrent_hashes:
            self._retrieve_torrent_data(torrent_hash=torrent_hash)
            self._send_store(torrent_hash=torrent_hash)
            self._loop_success = True

    def reset_daemon(self):
        logger.info(f"{self._daemon_name} is resetting")
        self._rid = dict()
        self._torrent_hashes = list()
        self._torrent_stores = dict()

    def add_sync_torrent_hash(self, torrent_hash: str):
        self._torrents_to_add_q.put(torrent_hash)
        self.set_wake_up("torrent hash added")

    def remove_sync_torrent_hash(self, torrent_hash: str):
        self._torrents_to_remove_q.put(torrent_hash)

    def get_torrent_store(self, torrent_hash: str):
        self._torrent_store_lock.acquire()
        store = self._torrent_stores.get(torrent_hash, None)
        self._torrent_store_lock.release()
        return store

    def _update_torrent_hashes_list(self):
        # remove stale torrent stores
        while not self._torrents_to_remove_q.empty():
            torrent_hash = self._torrents_to_remove_q.get()
            if torrent_hash in self._torrent_hashes:
                self._rid.pop(torrent_hash)
                self._torrent_hashes.remove(torrent_hash)
                self._delete_torrent_store(torrent_hash=torrent_hash)

        # add and reset new torrents
        while not self._torrents_to_add_q.empty():
            new_torrent_hash = self._torrents_to_add_q.get()
            if new_torrent_hash not in self._torrent_hashes:
                self._rid[new_torrent_hash] = 0
                self._torrent_hashes.append(new_torrent_hash)
                self._put_torrent_store(torrent_hash=new_torrent_hash,
                                        torrent=dict(),
                                        properties=dict(),
                                        trackers=list(),
                                        sync_torrent_peers=dict(full_update=True))

    def _retrieve_torrent_data(self, torrent_hash: str):
        # retrieve properties, trackers, and torrent peers info for all trackers
        try:
            torrent = self.client.torrents_list(torrent_ids=torrent_hash).pop()
        except IndexError:
            torrent = dict()
        properties = self.client.torrent_properties(torrent_id=torrent_hash)
        trackers = self.client.torrent_trackers(torrent_id=torrent_hash)
        sync_torrent_peers = self.client.sync_torrent_peers(torrent_id=torrent_hash, rid=self._rid[torrent_hash])
        self._rid[torrent_hash] = sync_torrent_peers.get('rid', 0)
        content = self.client.torrent_files(torrent_id=torrent_hash)

        # put everything in to the store for the torrent
        self._put_torrent_store(torrent_hash=torrent_hash,
                                torrent=torrent,
                                properties=properties,
                                trackers=trackers,
                                sync_torrent_peers=sync_torrent_peers,
                                content=content)

    def _put_torrent_store(self, torrent_hash: str, torrent=None, properties=None, trackers=None,
                           sync_torrent_peers=None, content=None):
        self._torrent_store_lock.acquire()
        if torrent_hash not in self._torrent_stores:
            self._torrent_stores[torrent_hash] = SyncTorrent.TorrentStore()
        if torrent:
            self._torrent_stores[torrent_hash].torrent = torrent
        if properties:
            self._torrent_stores[torrent_hash].properties = properties
        if trackers:
            self._torrent_stores[torrent_hash].trackers = trackers
        if sync_torrent_peers:
            if sync_torrent_peers.get('full_update', False):
                self._torrent_stores[torrent_hash].sync_torrent_peers = sync_torrent_peers.get('peers', {})
            else:
                for peer in sync_torrent_peers.get('peers_removed', []):
                    self._torrent_stores[torrent_hash].sync_torrent_peers.pop(peer)
                for peer, peer_dict in sync_torrent_peers.get('peers', {}).items():
                    if peer in self._torrent_stores[torrent_hash].sync_torrent_peers:
                        self._torrent_stores[torrent_hash].sync_torrent_peers[peer].update(peer_dict)
                    else:
                        self._torrent_stores[torrent_hash].sync_torrent_peers[peer] = peer_dict
        if content:
            self._torrent_stores[torrent_hash].content = content
        self._torrent_store_lock.release()

    def _send_store(self, torrent_hash: str):
        self.signal_ui("sync_torrent_data_ready", [torrent_hash])

    def _delete_torrent_store(self, torrent_hash: str):
        self._torrent_store_lock.acquire()
        self._torrent_stores.pop(torrent_hash, None)
        self._torrent_store_lock.release()

    class TorrentStore(object):
        def __init__(self):
            super(SyncTorrent.TorrentStore, self).__init__()
            self.torrent = AttrDict()
            self.properties = AttrDict()
            self.trackers = list()
            self.sync_torrent_peers = AttrDict()
            self.content = list()


class ServerDetails(Daemon):
    """
    Background daemon that syncs server details with app.

    :param torrent_client:
    """
    def __init__(self, torrent_client: Connector):
        super(ServerDetails, self).__init__(torrent_client)

        self._server_details = AttrDict({'server_version': ""})
        self._server_preferences = AttrDict()
        self._server_details_lock = threading.RLock()
        self._server_preferences_lock = threading.RLock()

    def _one_loop(self):
        server_version = self.client.version()
        preferences = self.client.preferences()

        self.set_preferences(preferences)

        new_details = False
        if server_details_changed.receivers:
            if server_version != self.get_server_details('server_version'):
                self.set_server_detail('server_version', server_version)
                new_details = True

        if new_details:
            self.signal_ui("server_details_ready")

        self._loop_success = True

    def get_server_preferences(self):
        self._server_preferences_lock.acquire()
        prefs = deepcopy(self._server_preferences)
        self._server_preferences_lock.release()
        return prefs

    def set_preferences(self, prefs):
        self._server_preferences_lock.acquire()
        self._server_preferences = prefs
        self._server_preferences_lock.release()

    def set_server_detail(self, key, value):
        self._server_details_lock.acquire()
        self._server_details[key] = value
        self._server_details_lock.release()

    def get_server_details(self, detail=None):
        self._server_details_lock.acquire()
        details = deepcopy(self._server_details)
        self._server_details_lock.release()
        if detail is None:
            return details
        else:
            return details.get(detail, "")


# TODO: implement callback ability
class Commands(Daemon):
    """
    Daemon to send commands to the server

    :param torrent_client:
    """
    def __init__(self, torrent_client: Connector):
        super(Commands, self).__init__(torrent_client)

        # set a long loop interval since anything sending
        # commands should also be setting the wake alarm
        self._loop_interval = 60

        self._command_q = queue.Queue()

    def _one_loop(self):
        # logger.info("Command queue length: %s" % self._command_q.qsize())
        ran_commands = False
        while not self._command_q.empty():
            try:
                command = self._command_q.get()
                command_func = command.get('func', '')
                command_args = command.get('func_args', dict())
                logger.info("Background command: %s" % command_func)
                logger.info("Background command args: %s " % command_args)
                command_func(**command_args)
                ran_commands = True
            except ConnectorError:
                raise
            except Exception:
                logger.info("Failed to run command", exc_info=True)

        # request server sync if commands were issued
        if ran_commands:
            self._loop_success = True
            update_torrent_list_now.send("%s daemon" % self._daemon_name)
            update_torrent_window_now.send("%s daemon" % self._daemon_name)

    def run_command(self, sender: str, command_func: str, command_args: dict):
        self._command_q.put(dict(func=command_func, func_args=command_args))
        self.set_wake_up(sender)
