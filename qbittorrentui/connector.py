from enum import Enum
from functools import wraps

from qbittorrentapi import Client as qbt_Client
from qbittorrentapi import exceptions as qbt_exceptions

from qbittorrentui.events import run_server_command


class ClientType(Enum):
    qbittorrent = 1


class ConnectorError(Exception):
    pass


class LoginFailed(ConnectorError):
    pass


def connection_required(f):
    """Ensure _client is connected before calling API methods."""

    @wraps(f)
    def wrapper(obj, *args, **kwargs):
        if not obj.is_logged_in:
            raise ConnectorError("Connect to torrent manager first")
        try:
            return f(obj, *args, **kwargs)
        except Exception as e:
            raise ConnectorError(repr(e))

    return wrapper


class Connector:
    _qbt_client: qbt_Client

    def __init__(self, client_type=ClientType.qbittorrent, host="", username="", password="", verify_certificate=True):
        self._client_type = client_type
        # self._qbt_client = None
        self.client_version = None
        self.is_logged_in = False

        self.host = host
        self.username = username
        self.password = password
        self.verify_certificate = verify_certificate

        if client_type is ClientType.qbittorrent:
            if host and username and password:
                try:
                    self.connect()
                except ConnectorError:
                    pass

    def connect(self, host=None, username=None, password=None, verify_certificate=None):
        if host is None:
            host = self.host
        if username is None:
            username = self.username
        if password is None:
            password = self.password
        if verify_certificate is None:
            verify_certificate = self.verify_certificate
        if self._client_type is ClientType.qbittorrent:
            try:
                self._qbt_client = qbt_Client(host, username=username, password=password, VERIFY_WEBUI_CERTIFICATE=verify_certificate)
            except AssertionError:
                raise LoginFailed("Incorrect host, username, or password")
            try:
                self.is_logged_in = True
                self.client_version = self._qbt_client.app.version
            except qbt_exceptions.LoginFailed as e:
                self.is_logged_in = False
                raise LoginFailed(e)
            except qbt_exceptions.APIError as e:
                self.is_logged_in = False
                raise ConnectorError(repr(e))

    @property
    def is_connected(self):
        return self.is_logged_in

    @staticmethod
    def _send_command(func, func_args):
        """
        Send signal to daemon to send command to torrent server.

        :param func:
        :param func_args:
        :return:
        """
        run_server_command.send('connector',
                                command_func=func,
                                command_args=func_args)

    @connection_required
    def version(self):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.app_version()

    @connection_required
    def preferences(self):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.app_preferences()

    @connection_required
    def transfer_info(self):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.transfer_info()

    @connection_required
    def torrents_add(self, urls=None, torrent_files=None, save_path=None, cookie=None, category=None,
                     is_skip_checking=None, is_paused=None, is_root_folder=None, rename=None,
                     upload_limit=None, download_limit=None, use_auto_torrent_management=None,
                     is_sequential_download=None, is_first_last_piece_priority=None):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.torrents_add(urls=urls, torrent_files=torrent_files, save_path=save_path,
                                                 cookie=cookie, category=category, is_skip_checking=is_skip_checking,
                                                 is_paused=is_paused, is_root_folder=is_root_folder, rename=rename,
                                                 upload_limit=upload_limit, download_limit=download_limit,
                                                 use_auto_torrent_management=use_auto_torrent_management,
                                                 is_sequential_download=is_sequential_download,
                                                 is_first_last_piece_priority=is_first_last_piece_priority)

    @connection_required
    def torrent_properties(self, torrent_id):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.torrents_properties(hash=torrent_id)

    @connection_required
    def torrent_rename(self, new_name, torrent_id):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_rename,
                               func_args=dict(new_torrent_name=new_name, hash=torrent_id))

    @connection_required
    def torrent_trackers(self, torrent_id):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.torrents_trackers(hash=torrent_id)

    @connection_required
    def sync_torrent_peers(self, torrent_id, rid=0):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.sync_torrent_peers(hash=torrent_id, rid=rid)

    @connection_required
    def torrent_files(self, torrent_id):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.torrents_files(hash=torrent_id)

    @connection_required
    def torrent_file_priority(self, torrent_id, file_ids, priority):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_file_priority,
                               func_args=dict(hash=torrent_id, file_ids=file_ids, priority=priority))

    @connection_required
    def torrents_list(self, status_filter='all', torrent_ids=None):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.torrents_info(status_filter=status_filter, hashes=torrent_ids)

    @connection_required
    def torrents_delete(self, torrent_ids, delete_files=False):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_delete,
                               func_args=dict(delete_files=delete_files,
                                              hashes=torrent_ids))

    @connection_required
    def torrents_resume(self, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_resume,
                               func_args=dict(hashes=torrent_ids))

    @connection_required
    def torrents_pause(self, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_pause,
                               func_args=dict(hashes=torrent_ids))

    @connection_required
    def torrents_force_resume(self, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_force_start,
                               func_args=dict(hashes=torrent_ids, enable=True))

    @connection_required
    def torrents_recheck(self, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_recheck,
                               func_args=dict(hashes=torrent_ids))

    @connection_required
    def torrents_reannounce(self, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_reannounce,
                               func_args=dict(hashes=torrent_ids))

    @connection_required
    def torrents_categories(self):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.torrents_categories()

    @connection_required
    def torrents_set_location(self, location, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_location,
                               func_args=dict(location=location, hashes=torrent_ids))

    @connection_required
    def torrents_set_automatic_torrent_management(self, enable, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_auto_management,
                               func_args=dict(enable=enable, hashes=torrent_ids))

    @connection_required
    def torrents_set_super_seeding(self, enable, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_super_seeding,
                               func_args=dict(enable=enable, hashes=torrent_ids))

    @connection_required
    def torrents_set_upload_limit(self, limit, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_upload_limit,
                               func_args=dict(limit=limit, hashes=torrent_ids))

    @connection_required
    def torrents_set_download_limit(self, limit, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_download_limit,
                               func_args=dict(limit=limit, hashes=torrent_ids))

    @connection_required
    def torrents_set_category(self, category, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_category,
                               func_args=dict(category=category, hashes=torrent_ids))

    @connection_required
    def torrents_set_share_limits(self, ratio_limit, seeding_time_limit, torrent_ids):
        if self._client_type is ClientType.qbittorrent:
            self._send_command(func=self._qbt_client.torrents_set_share_limits,
                               func_args=dict(ratio_limit=ratio_limit,
                                              seeding_time_limit=seeding_time_limit,
                                              hashes=torrent_ids))

    @connection_required
    def sync_maindata(self, rid):
        if self._client_type is ClientType.qbittorrent:
            return self._qbt_client.sync_maindata(rid)

    @connection_required
    def api_wrapper(self, api_endpoint, **kwargs):
        return getattr(self._qbt_client, api_endpoint)(**kwargs)
