import logging
import os
from datetime import datetime
from time import time

import blinker
import urwid as uw

from qbittorrentui._attrdict import AttrDict
from qbittorrentui.config import config
from qbittorrentui.connector import Connector
from qbittorrentui.debug import log_keypress
from qbittorrentui.debug import log_timing
from qbittorrentui.events import torrent_window_tab_change
from qbittorrentui.formatters import natural_file_size
from qbittorrentui.formatters import pretty_time_delta
from qbittorrentui.misc_widgets import DownloadProgressBar
from qbittorrentui.misc_widgets import SelectableText

logger = logging.getLogger(__name__)


class TorrentWindow(uw.Columns):
    """Display window with tabs for different collections of torrent information."""
    def __init__(self, main, torrent_hash, torrent, client):

        self.tabs = {"General": GeneralDisplay(),
                     "Trackers": TrackersDisplay(),
                     "Peers": PeersDisplay(),
                     "Content": ContentDisplay(client, torrent_hash),
                     }

        self.tabs_column_w = TorrentTabsDisplay(list(self.tabs.keys()))
        self.content_column = self.tabs['General']

        columns_list = [(uw.WEIGHT, 10, self.tabs_column_w),
                        (uw.WEIGHT, 90, self.content_column)]

        super(TorrentWindow, self).__init__(
            columns_list,
            dividechars=3,
            focus_column=0,
            min_width=15,
            box_columns=None
        )

        self.main = main
        self.torrent = torrent
        self.torrent_hash = torrent_hash

        torrent_window_tab_change.connect(receiver=self.switch_tab_window)
        self.main.daemon.add_sync_torrent_hash(torrent_hash=torrent_hash)
        for tab_window in self.tabs.values():
            blinker.signal(torrent_hash).connect(receiver=tab_window.update)

    def switch_tab_window(self, sender, tab=None):
        if tab is None:
            return
        self.content_column = self.tabs[tab]
        self.contents[1] = (
            self.content_column,
            self.options(width_type=uw.WEIGHT, width_amount=90, box_widget=False)
        )

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(TorrentWindow, self).keypress(size, key)
        if key in ['esc', 'left']:
            self.return_to_torrent_list()
            return None
        return key

    def return_to_torrent_list(self):
        self.main.daemon.remove_sync_torrent_hash(torrent_hash=self.torrent_hash)
        for tab_window in self.tabs.values():
            blinker.signal(self.torrent_hash).disconnect(receiver=tab_window.update)
        self.main.app_window.body = self.main.app_window.torrent_list_w


class TorrentTabsDisplay(uw.ListBox):
    def __init__(self, tabs: list):
        tabs_list_for_walker = [uw.Text("")]
        for i, tab_name in enumerate(tabs):
            tabs_list_for_walker.extend(
                [
                    uw.AttrMap(
                        SelectableText(
                            tab_name,
                            align=uw.CENTER,
                            wrap=uw.CLIP
                        ),
                        '',
                        focus_map='selected'
                    ),
                    uw.Text("")
                ]
            )
        self.list_walker = uw.SimpleFocusListWalker(tabs_list_for_walker)
        super(TorrentTabsDisplay, self).__init__(self.list_walker)

        self.__selected_tab_pos = None

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(TorrentTabsDisplay, self).keypress(size, key)

        # Add 'selected' AttrMap to newly focused tab
        #  and remove 'selected'' AttrMap from previously focused tab
        if self.focus_position != self.__selected_tab_pos:
            if self.__selected_tab_pos is not None:
                tab_text = self.list_walker[self.__selected_tab_pos].base_widget.get_text()[0]
                new_tab = uw.AttrMap(
                    SelectableText(tab_text, align=uw.CENTER),
                    '',
                    focus_map='selected')
                self.list_walker[self.__selected_tab_pos] = new_tab
            self.__selected_tab_pos = self.focus_position
            tab_text = self.list_walker[self.__selected_tab_pos].base_widget.get_text()[0]
            new_tab = uw.AttrMap(
                SelectableText(tab_text, align=uw.CENTER),
                'selected',
                focus_map='selected')
            self.list_walker[self.__selected_tab_pos] = new_tab

            torrent_window_tab_change.send("torrent window tabs", tab=tab_text)
        return key


class GeneralDisplay(uw.ListBox):
    def __init__(self):
        self.widgets_to_update = []

        def create_widgets():
            val_cont = GeneralDisplay.TorrentWindowGeneralTabValueContainer

            def format_time_active(time_elapsed=0):
                return format_time_delta(seconds=time_elapsed)

            def format_reannounce(reannounce=0):
                return format_time_delta(seconds=reannounce)

            def format_eta(eta=8640000):
                return format_time_delta(seconds=eta, infinity=True)

            def format_time_delta(seconds=0, infinity=False):
                if infinity is True:
                    return "%s" % (
                        pretty_time_delta(seconds=seconds, spaces=True) if seconds < 8640000 else '\u221E')
                return "%s" % pretty_time_delta(seconds=seconds, spaces=True)

            def format_pieces(pieces_num=0, piece_size=0, pieces_have=0):
                return "%d x %s (have %d)" % (pieces_num, format_size(size_bytes=piece_size), pieces_have)

            def format_uploaded(total_uploaded=0, total_uploaded_session=0):
                return format_up_or_down(total=total_uploaded, total_session=total_uploaded_session)

            def format_downloaded(total_downloaded=0, total_downloaded_session=0):
                return format_up_or_down(total=total_downloaded, total_session=total_downloaded_session)

            def format_up_or_down(total=0, total_session=0):
                return "%s (%s this session)" % (format_size(size_bytes=total),
                                                 format_size(size_bytes=total_session))

            def format_upload_speed(up_speed=0, up_speed_avg=0):
                return format_up_or_down_speed(speed=up_speed, speed_avg=up_speed_avg)

            def format_download_speed(dl_speed=0, dl_speed_avg=0):
                return format_up_or_down_speed(speed=dl_speed, speed_avg=dl_speed_avg)

            def format_up_or_down_speed(speed=0, speed_avg=0):
                return "%s/s (%s/s avg)" % (format_size(size_bytes=speed),
                                            format_size(size_bytes=speed_avg))

            def format_up_limit(up_limit=0):
                return format_up_or_down_limit(limit=up_limit)

            def format_down_limit(dl_limit=0):
                return format_up_or_down_limit(limit=dl_limit)

            def format_up_or_down_limit(limit=0):
                if limit == -1:
                    return '\u221E'
                return "%s/s" % format_size(size_bytes=limit)

            def format_wasted(total_wasted=0):
                return format_size(size_bytes=total_wasted)

            def format_total_size(total_size=0):
                return format_size(size_bytes=total_size)

            def format_size(size_bytes=0):
                return natural_file_size(size_bytes, binary=True)

            def format_share_ratio(share_ratio=0):
                return "%.2f" % share_ratio

            def format_connections(nb_connections=0, nb_connections_limit=0):
                return "%d (%d max)" % (nb_connections, nb_connections_limit)

            def format_seeds(seeds=0, seeds_total=0):
                return format_seeds_or_peers(num=seeds, total=seeds_total)

            def format_peers(peers=0, peers_total=0):
                return format_seeds_or_peers(num=peers, total=peers_total)

            def format_seeds_or_peers(num=0, total=0):
                return "%d (%d total)" % (num, total)

            def format_last_seen(last_seen=-1):
                return format_date_time_with_delta(seconds=last_seen)

            def format_added_on(addition_date=-1):
                return format_date_time_with_delta(seconds=addition_date)

            def format_completed_on(completion_date=-1):
                return format_date_time_with_delta(seconds=completion_date)

            def format_creation_date(creation_date=-1):
                return format_date_time_with_delta(seconds=creation_date)

            def format_date_time_with_delta(seconds):
                return "%s%s" % (format_date_time(seconds=seconds),
                                 " (%s)" % pretty_time_delta(seconds=(time() - seconds),
                                                             spaces=True) if seconds != -1 else "")

            def format_date_time(seconds):
                if seconds == -1:
                    return ""
                dt = datetime.fromtimestamp(seconds)
                return dt.strftime("%m/%d/%y %H:%M:%S")

            def format_hash(hash=""):
                return format_string(string=hash)

            def format_save_path(save_path=""):
                return format_string(string=save_path)

            def format_comment(comment=""):
                return format_string(string=comment)

            def format_created_by(created_by=""):
                return format_string(string=created_by)

            def format_string(string):
                return str(string)

            # TRANSFER
            self.time_active_w = val_cont(data_elements=['time_elapsed'],
                                          caption="Time Active",
                                          format_func=format_time_active)
            self.widgets_to_update.append(self.time_active_w)

            self.downloaded_w = val_cont(data_elements=['total_downloaded', 'total_downloaded_session'],
                                         caption="Downloaded",
                                         format_func=format_downloaded)
            self.widgets_to_update.append(self.downloaded_w)

            self.download_speed_w = val_cont(data_elements=['dl_speed', 'dl_speed_avg'],
                                             caption="Download Speed",
                                             format_func=format_download_speed)
            self.widgets_to_update.append(self.download_speed_w)

            self.download_limit_w = val_cont(data_elements=['dl_limit'],
                                             caption="Download Limit",
                                             format_func=format_down_limit)
            self.widgets_to_update.append(self.download_limit_w)

            self.share_ratio_w = val_cont(data_elements=['share_ratio'],
                                          caption="Share Ratio",
                                          format_func=format_share_ratio)
            self.widgets_to_update.append(self.share_ratio_w)

            self.eta_w = val_cont(data_elements=['eta'],
                                  caption='ETA',
                                  format_func=format_eta)
            self.widgets_to_update.append(self.eta_w)

            self.uploaded_w = val_cont(data_elements=['total_uploaded', 'total_uploaded_session'],
                                       caption="Uploaded",
                                       format_func=format_uploaded)
            self.widgets_to_update.append(self.uploaded_w)

            self.upload_speed_w = val_cont(data_elements=['up_speed', 'up_speed_avg'],
                                           caption="Upload Speed",
                                           format_func=format_upload_speed)
            self.widgets_to_update.append(self.upload_speed_w)

            self.upload_limit_w = val_cont(data_elements=['up_limit'],
                                           caption="Upload Limit",
                                           format_func=format_up_limit)
            self.widgets_to_update.append(self.upload_limit_w)

            self.reannounce_w = val_cont(data_elements=['reannounce'],
                                         caption="Reannounce In",
                                         format_func=format_reannounce)
            self.widgets_to_update.append(self.reannounce_w)

            self.connections_w = val_cont(data_elements=['nb_connections', 'nb_connections_limit'],
                                          caption="Connections",
                                          format_func=format_connections)
            self.widgets_to_update.append(self.connections_w)

            self.seeds_w = val_cont(data_elements=['seeds', 'seeds_total'],
                                    caption="Seeds",
                                    format_func=format_seeds)
            self.widgets_to_update.append(self.seeds_w)

            self.peers_w = val_cont(data_elements=['peers', 'peers_total'],
                                    caption="Peers",
                                    format_func=format_peers)
            self.widgets_to_update.append(self.peers_w)

            self.wasted_w = val_cont(data_elements=['total_wasted'],
                                     caption="Wasted",
                                     format_func=format_wasted)
            self.widgets_to_update.append(self.wasted_w)

            self.last_seen_w = val_cont(data_elements=['last_seen'],
                                        caption='Last Seen Complete',
                                        format_func=format_last_seen)
            self.widgets_to_update.append(self.last_seen_w)

            # INFORMATION
            self.total_size_w = val_cont(data_elements=['total_size'],
                                         caption="Total Size",
                                         format_func=format_total_size)
            self.widgets_to_update.append(self.total_size_w)

            self.added_on_w = val_cont(data_elements=['addition_date'],
                                       caption="Added On",
                                       format_func=format_added_on)
            self.widgets_to_update.append(self.added_on_w)

            self.torrent_hash_w = val_cont(data_elements=['hash'],
                                           caption="Torrent Hash",
                                           source="torrent",
                                           format_func=format_hash)
            self.widgets_to_update.append(self.torrent_hash_w)

            self.save_path_w = val_cont(data_elements=['save_path'],
                                        caption="Save Path",
                                        format_func=format_save_path)
            self.widgets_to_update.append(self.save_path_w)

            self.comment_w = val_cont(data_elements=['comment'],
                                      caption="Comment",
                                      format_func=format_comment)
            self.widgets_to_update.append(self.comment_w)

            self.pieces_w = val_cont(data_elements=['pieces_num', 'piece_size', 'pieces_have'],
                                     caption="Pieces",
                                     format_func=format_pieces)
            self.widgets_to_update.append(self.pieces_w)

            self.completed_on_w = val_cont(data_elements=['completion_date'],
                                           caption='Completed On',
                                           format_func=format_completed_on)
            self.widgets_to_update.append(self.completed_on_w)

            self.created_by_w = val_cont(data_elements=['created_by'],
                                         caption="Created By",
                                         format_func=format_created_by)
            self.widgets_to_update.append(self.created_by_w)

            self.created_on_w = val_cont(data_elements=['creation_date'],
                                         caption="Created On",
                                         format_func=format_creation_date)
            self.widgets_to_update.append(self.created_on_w)
        create_widgets()

        walker = uw.SimpleFocusListWalker(
            [
                uw.Text("Transfer"),
                self.time_active_w,
                self.downloaded_w,
                self.download_speed_w,
                self.download_limit_w,
                self.share_ratio_w,
                self.eta_w,
                self.uploaded_w,
                self.upload_speed_w,
                self.upload_limit_w,
                self.reannounce_w,
                self.connections_w,
                self.seeds_w,
                self.peers_w,
                self.wasted_w,
                self.last_seen_w,
                uw.Divider(),
                uw.Text("Information"),
                self.total_size_w,
                self.added_on_w,
                self.torrent_hash_w,
                self.save_path_w,
                self.comment_w,
                self.pieces_w,
                self.completed_on_w,
                self.created_by_w,
                self.created_on_w,
            ]
        )
        super(GeneralDisplay, self).__init__(walker)

    def update(self, sender, **kw):
        start_time = time()
        torrent = kw.get('torrent', {})
        properties = kw.get('properties', {})
        for w in self.widgets_to_update:
            w.base_widget.update(torrent=torrent, properties=properties)
        log_timing(logger, "Updating", self, sender, start_time)

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(GeneralDisplay, self).keypress(size, key)
        return key

    class TorrentWindowGeneralTabValueContainer(uw.Columns):
        def __init__(self, data_elements: list, caption: str, format_func, source: str = "properties"):
            super(GeneralDisplay.TorrentWindowGeneralTabValueContainer, self).__init__(
                [],
                dividechars=1,
                focus_column=None,
                min_width=1,
                box_columns=None
            )
            self.data_elements = data_elements
            self.source = source  # torrent or properties
            self.caption = caption
            self.format_func = format_func
            # initialize widget with default values
            #  update should be called immediately after instantiation
            self.raw_value = dict()

        # def __len__(self):
        #    return len(self.text)

        @property
        def raw_value(self):
            return self._raw_value

        @raw_value.setter
        def raw_value(self, values: dict):
            self._raw_value = values
            left_column = uw.Text(("%s:" % self.caption).rjust(20), align=uw.RIGHT, wrap=uw.CLIP)
            right_column = uw.Text("%s" % self.format_func(**values), wrap=uw.CLIP)

            self.contents.clear()
            self.contents.extend(
                [
                    (
                        left_column,
                        self.options(width_type=uw.PACK, width_amount=50, box_widget=False)
                    ),
                    (
                        right_column,
                        self.options(width_type=uw.PACK, width_amount=50, box_widget=False)
                    )
                ]
            )

        def update(self, torrent: dict, properties: dict):
            values = dict()
            source = torrent if self.source == "torrent" else properties
            for e in self.data_elements:
                if e in source:
                    values[e] = source[e]
            if self.raw_value != values:
                self.raw_value = values


class TrackersDisplay(uw.ListBox):
    def __init__(self):
        self.walker = uw.SimpleFocusListWalker([])
        super(TrackersDisplay, self).__init__(self.walker)

    def update(self, sender, **kw):
        """
        Apply tracker information updates from daemon.

        This could be significantly more efficient...however, there aren't usually
        enough trackers on a torrent to warrant a lot of work to make this fast.

        sample tracker list:
        >>> [
        >>> AttrDict({'msg': '', 'num_downloaded': 0, 'num_leeches': 0, 'num_peers': 0, 'num_seeds': 0, 'status': 2,
        >>> 'tier': '', 'url': '** [DHT] **'}),
        >>> AttrDict({'msg': '', 'num_downloaded': 0, 'num_leeches': 0,
        >>> 'num_peers': 0, 'num_seeds': 0, 'status': 2, 'tier': '', 'url': '** [PeX] **'}),
        >>> AttrDict({'msg': '', 'num_downloaded': 0, 'num_leeches': 0, 'num_peers': 0, 'num_seeds': 0, 'status': 2,
        >>> 'tier': '', 'url': '** [LSD] **'}),
        >>> AttrDict({'msg': '', 'num_downloaded': -1, 'num_leeches': -1,
        >>> 'num_peers': 0, 'num_seeds': -1, 'status': 1, 'tier': 0, 'url': 'udp://tracker.coppersurfer.tk:6969/announce'}),
        >>> AttrDict({'msg': '', 'num_downloaded': -1, 'num_leeches': -1, 'num_peers': 0, 'num_seeds': -1,
        >>> 'status': 1, 'tier': 1, 'url': 'udp://9.rarbg.com:2710/announce'}),
        >>> AttrDict({'msg': '', 'num_downloaded': -1, 'num_leeches': -1, 'num_peers': 0, 'num_seeds': -1, 'status': 1,
        >>> 'tier': 2, 'url': 'udp://p4p.arenabg.com:1337'}),
        >>> AttrDict({'msg': '', 'num_downloaded': -1, 'num_leeches': -1,
        >>> 'num_peers': 0, 'num_seeds': -1, 'status': 1, 'tier': 3, 'url': 'udp://tracker.internetwarriors.net:1337'}),
        >>> AttrDict({'msg': '', 'num_downloaded': -1, 'num_leeches': -1, 'num_peers': 0, 'num_seeds': -1,
        >>> 'status': 1, 'tier': 4, 'url': 'udp://tracker.opentrackr.org:1337/announce'})
        >>> ]

        :param sender:
        :param kw:
        :return:
        """
        start_time = time()
        trackers = kw.get('trackers', {})

        status_map = {0: "Disabled",
                      1: "Not contacted yet",
                      2: "Working",
                      3: "Updating",
                      4: "Not working",
                      "Status": "Status"}

        title_bar = AttrDict(url="URL",
                             status="Status",
                             num_peers="Peers",
                             num_seeds="Seeds",
                             num_leeches="Leeches",
                             num_downloaded="Downloaded",
                             msg="Message")

        trackers.insert(0, title_bar)
        tracker_w_list = []
        for tracker in trackers:
            status = status_map[tracker.status] if tracker.status in status_map else tracker.status
            num_peers = tracker.num_peers if tracker.num_peers != -1 else "N/A"
            num_seeds = tracker.num_seeds if tracker.num_seeds != -1 else "N/A"
            num_leeches = tracker.num_leeches if tracker.num_leeches != -1 else "N/A"
            num_downloaded = tracker.num_downloaded if tracker.num_downloaded != -1 else "N/A"
            tracker_w_list.append(
                uw.Columns(
                    [
                        (
                            max(map(len, [tracker.url for tracker in trackers])),
                            uw.Text("%s" % tracker.url, wrap=uw.CLIP)),
                        (
                            max(map(len, [status_map[tracker.status] for tracker in trackers])),
                            uw.Text("%s" % status, wrap=uw.CLIP)),
                        (
                            len(title_bar.num_peers),
                            uw.Text("%s" % num_peers, align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            len(title_bar.num_seeds),
                            uw.Text("%s" % num_seeds, align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            len(title_bar.num_leeches),
                            uw.Text("%s" % num_leeches, align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            len(title_bar.num_downloaded),
                            uw.Text("%s" % num_downloaded, align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            uw.Text("%s" % tracker.msg, wrap=uw.CLIP))
                    ],
                    dividechars=3
                )
            )

        self.walker.clear()
        self.walker.append(uw.Divider())
        self.walker.extend(tracker_w_list)

        log_timing(logger, "Updating", self, sender, start_time)

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(TrackersDisplay, self).keypress(size, key)
        return key


class PeersDisplay(uw.ListBox):
    def __init__(self):
        self.walker = uw.SimpleFocusListWalker([])
        super(PeersDisplay, self).__init__(self.walker)

    def update(self, sender, **kw):
        """

        smaple peer entry:
        '96.51.101.249:57958': {'client': 'μTorrent 3.5.5',
                               'connection': 'μTP',
                               'country': 'Canada',
                               'country_code': 'ca',
                               'dl_speed': 73,
                               'downloaded': 872530,
                               'files': 'Tosh.0.S11E10.720p.WEB.x264-TBS[rarbg]/tosh.0.s11e10.720p.web.x264-tbs.mkv.!qB',
                               'flags': 'D X H E P',
                               'flags_desc': 'D = interested(local) and '
                                             'unchoked(peer)\n'
                                             'X = peer from PEX\n'
                                             'H = peer from DHT\n'
                                             'E = encrypted traffic\n'
                                             'P = μTP',
                               'ip': '96.51.101.249',
                               'port': 57958,
                               'progress': 1,
                               'relevance': 1,
                               'up_speed': 0,
                               'uploaded': 0}
        :param sender:
        :param kw:
        :return:
        """
        start_time = time()
        peers = kw.get('sync_torrent_peers', {})

        max_country_len = len('C')
        max_connection_len = len("Conn")
        max_flags_len = len("Flags")
        max_client_len = len("Client")
        max_ip_len = len("0.0.0.0")
        max_port_len = 4
        max_dl_speed_len = 8
        max_up_speed_len = max_dl_speed_len
        max_downloaded_len = 6
        max_uploaded_len = max_downloaded_len
        max_relevance_len = 4
        max_progress_len = 4
        for p in peers.values():
            max_country_len = max(max_country_len, len(p['country_code']))
            max_flags_len = max(max_flags_len, len(p['flags']))
            max_connection_len = max(max_connection_len, len(p['connection']))
            max_client_len = max(max_client_len, len(p['client']))
            max_ip_len = max(max_ip_len, len(str(p['ip'])))
            max_port_len = max(max_port_len, len(str(p['port'])))

        title_bar = dict(client="Client",
                         connection="Conn",
                         country="Country",
                         country_code="C",
                         dl_speed="Down",
                         downloaded="Down'd",
                         files="Files",
                         flags="Flags",
                         ip="IP",
                         port="Port",
                         progress="Prog",
                         relevance="Rel",
                         up_speed="Up",
                         uploaded="Up'd")

        title_bar_w = uw.Columns(
            [
                (
                    max_country_len,
                    uw.Text("%s" % title_bar['country_code'], wrap=uw.CLIP)),
                (
                    max_ip_len,
                    uw.Text("%s" % title_bar['ip'], wrap=uw.CLIP)),
                (
                    max_port_len,
                    uw.Text("%s" % title_bar['port'], wrap=uw.CLIP)),
                (
                    max_connection_len,
                    uw.Text("%s" % title_bar['connection'], wrap=uw.CLIP)),
                (
                    max_flags_len,
                    uw.Text("%s" % title_bar['flags'], wrap=uw.CLIP)),
                (
                    max_client_len,
                    uw.Text("%s" % title_bar['client'], wrap=uw.CLIP)),
                (
                    max_progress_len,
                    uw.Text("%s" % title_bar["progress"], wrap=uw.CLIP)),
                (
                    max_dl_speed_len,
                    uw.Text("%s" % title_bar['dl_speed'], wrap=uw.CLIP)),
                (
                    max_up_speed_len,
                    uw.Text("%s" % title_bar['up_speed'], wrap=uw.CLIP)),
                (
                    max_downloaded_len,
                    uw.Text("%s" % title_bar['downloaded'], wrap=uw.CLIP)),
                (
                    max_uploaded_len,
                    uw.Text("%s" % title_bar['uploaded'], wrap=uw.CLIP)),
                (
                    max_relevance_len,
                    uw.Text("%s" % title_bar['relevance'], wrap=uw.CLIP)),
                (
                    uw.Text("%s" % title_bar['files'], wrap=uw.CLIP)
                )
            ],
            dividechars=1
        )

        peer_w_list = [title_bar_w]
        for p in peers.values():
            peer_w_list.append(
                uw.Columns(
                    [
                        (
                            max_country_len,
                            uw.Text("%s" % p['country_code'].upper(), wrap=uw.CLIP)),
                        (
                            max_ip_len,
                            uw.Text("%s" % p['ip'], wrap=uw.CLIP, align=uw.RIGHT)),
                        (
                            max_port_len,
                            uw.Text("%s" % p['port'], align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            max_connection_len,
                            uw.Text("%s" % p['connection'], wrap=uw.CLIP)),
                        (
                            max_flags_len,
                            uw.Text("%s" % p['flags'], wrap=uw.CLIP)),
                        (
                            max_client_len,
                            uw.Text("%s" % p['client'], wrap=uw.CLIP)),
                        (
                            max_progress_len,
                            uw.Text("%3d%s" % (p['progress']*100, "%"), align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            max_dl_speed_len,
                            uw.Text("%s/s" % natural_file_size(p['dl_speed'], gnu=True), align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            max_up_speed_len,
                            uw.Text("%s/s" % natural_file_size(p['up_speed'], gnu=True), align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            max_downloaded_len,
                            uw.Text("%s" % natural_file_size(p['downloaded'], gnu=True), align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            max_uploaded_len,
                            uw.Text("%s" % natural_file_size(p['uploaded'], gnu=True), align=uw.RIGHT, wrap=uw.CLIP)),
                        (
                            max_relevance_len,
                            uw.Text("%3d%s" % (p['relevance']*100, "%"), wrap=uw.CLIP)),
                        (
                            uw.Text("%s" % p['files'], wrap=uw.CLIP)
                        )
                    ],
                    dividechars=1
                )
            )

        self.walker.clear()
        self.walker.append(uw.Divider())
        self.walker.extend(peer_w_list)

        log_timing(logger, "Updating", self, sender, start_time)

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(PeersDisplay, self).keypress(size, key)
        return key


class ContentDisplay(uw.Pile):
    """
    This window is a bit of a mess unfortunately...urwid trees are annoying.

    The Content mixes both torrent data and display information. The Content object
    bounces around too much in the Tree itself. Management of the "path" of a file is
    abysmal and need normalization.

    The widget that is actually displayed is built in FlagFileWidget.load_inner_widget().
    The changing of file priorities is controlled in FlagFileWidget.unhandled_keys().

    This tree was derived from a urwid example:
    https://github.com/urwid/urwid/blob/master/examples/browse.py

    """
    def __init__(self, client: Connector, torrent_hash):
        self.client = client
        self.torrent_hash = torrent_hash
        self.focused_path = None
        self.focused_node_class = ContentDisplay.DirectoryNode
        self.collapsed_dirs = []

        self.title_bar = uw.Columns(
            [
                (75, uw.Filler(uw.Text("Name", align=uw.LEFT, wrap=uw.SPACE))),
                (6, uw.Filler(uw.Text("Size", align=uw.LEFT))),
                uw.Filler(uw.Text("Progress", align=uw.LEFT, wrap=uw.CLIP)),
                (8, uw.Filler(uw.Text("Priority", align=uw.LEFT))),
                (6, uw.Filler(uw.Text("Remain", align=uw.LEFT))),
                (5, uw.Filler(uw.Text("Avail", align=uw.LEFT))),
            ],
            dividechars=1,
        )

        self.walker = uw.TreeWalker(
            ContentDisplay.DirectoryNode(
                content=ContentDisplay.Content(
                    self.client,
                    torrent_hash="",
                    content={},
                    collapsed_dirs=[]
                ),
                path='/'
            )
        )
        self.tree_w = uw.TreeListBox(self.walker)
        w_list = [(1, self.title_bar), self.tree_w]

        super(ContentDisplay, self).__init__(w_list)

    def update(self, sender, **kw):
        start_time = time()
        torrent_content = kw.get('content', [])

        content = ContentDisplay.Content(client=self.client,
                                         torrent_hash=self.torrent_hash,
                                         content=torrent_content,
                                         collapsed_dirs=self.collapsed_dirs)

        try:
            # this will fail the first run through but is fine after that
            focused_node = self.walker.get_focus()[1]
            # save off the focused node so it can be refocused after refresh
            self.focused_path = focused_node.get_value()
            self.focused_node_class = type(focused_node)
        except TypeError:
            pass

        node = self.focused_node_class(
            content=content,
            path=self.focused_path if self.focused_path is not None else content.root_dir()
        )
        self.walker.set_focus(node)

        log_timing(logger, "Updating", self, sender, start_time)

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(ContentDisplay, self).keypress(size, key)
        return key

    class Content(object):
        def __init__(self, client, torrent_hash, content, collapsed_dirs: list):
            super(ContentDisplay.Content, self).__init__()
            self.client = client
            self.torrent_hash = torrent_hash
            self._torrent_content = content
            self._collapsed_dirs = collapsed_dirs
            self._content_tree = dict(name=self.dir_sep(),
                                      children=list())

            unwanted = '/.unwanted'
            for c in self._torrent_content:
                # remove the ".unwanted" dir from all paths
                if unwanted in c['name']:
                    c['name'] = c['name'][:c['name'].find(unwanted)] + c['name'][c['name'].find(unwanted)+len(unwanted):]

                c_name = c.get('name', "")

                # build tree data
                if c_name:
                    self._add_node_or_leaf(
                        content_list=self._content_tree['children'],
                        name=c_name,
                        content=c
                    )

        def list_dir(self, path):
            children = self.children_for_path(path)
            return [e['name'] for e in children]

        def is_dir(self, path):
            return len(self.children_for_path(path)) > 0

        @staticmethod
        def root_dir():
            return '/'

        def get_file_ids(self, path):
            file_ids = list()
            for i, file in enumerate(self._torrent_content):
                if file['name'].startswith(path):
                    file_ids.append(i)
            return file_ids

        def get_file_data(self, path):
            file_data = dict(size=0,
                             priority='unk',
                             availability=100,
                             progress=0,
                             completed=0)
            for file in self._torrent_content:
                if self.is_dir(path):
                    if file['name'].startswith(path):
                        file_data['size'] += file.get('size', 0)
                        if file_data['priority'] == 'unk':
                            file_data['priority'] = file.get('priority', 'unk')
                        else:
                            if file_data['priority'] != file.get('priority', 'unk'):
                                file_data['priority'] = -1
                        file_data['availability'] = min(file_data['availability'], file.get('availability', -1))
                        file_data['completed'] += file.get('size', 0) * file.get('progress', 0)

                else:
                    if file['name'] == path:
                        file_data = file
                        file_data['completed'] = file.get('size', 0) * file.get('progress', 0)
                        break
            return file_data

        def add_collapsed_dir(self, path):
            if path not in self._collapsed_dirs:
                self._collapsed_dirs.append(path)

        def remove_collapsed_dir(self, path):
            if path in self._collapsed_dirs:
                self._collapsed_dirs.remove(path)

        def get_collapsed_dirs(self):
            return self._collapsed_dirs

        def children_for_path(self, path: str):
            if path:
                if path[0] != self.dir_sep():
                    path = "%s%s" % (self.dir_sep(), path)
            if path == self.dir_sep():
                return self._content_tree['children']
            path_split = path.split(self.dir_sep())
            path_split[0] = self.dir_sep()
            new_children = [self._content_tree]
            for path_piece in path_split:
                children = new_children
                for child in children:
                    if child['name'] == path_piece:
                        new_children = child['children']
            return new_children

        @staticmethod
        def dir_sep(): return '/'

        def _add_node_or_leaf(self, content_list: list, name: str, content: dict):
            if self.dir_sep() in name:
                index = None
                node_name = name[:name.find(self.dir_sep())]
                children_name = name[name.find(self.dir_sep()) + 1:]
                if node_name not in [e['name'] for e in content_list]:
                    new_node = dict(name=node_name, children=list())
                    content_list.append(new_node)
                    index = content_list.index(new_node)
                else:
                    for i, entry in enumerate(content_list):
                        if entry['name'] == node_name:
                            index = i
                            break
                if index is not None:
                    self._add_node_or_leaf(content_list=content_list[index]['children'],
                                           name=children_name,
                                           content=content)
            else:
                content_list.append(dict(name=name, children=list()))

    class FlagFileWidget(uw.TreeWidget):
        # apply an attribute to the expand/unexpand icons
        unexpanded_icon = uw.AttrMap(uw.TreeWidget.unexpanded_icon, 'dirmark')
        expanded_icon = uw.AttrMap(uw.TreeWidget.expanded_icon, 'dirmark')

        def __init__(self, node):
            self.__super.__init__(node)
            # insert an extra AttrWrap for our own use
            super().__init__(node)
            self._w = uw.AttrWrap(self._w, None)
            # self.flagged = False
            # self.update_w()
            self._w.attr = ''
            self._w.focus_attr = 'selected'

        def selectable(self):
            return True

        # display widget built here
        def load_inner_widget(self):
            """
            {'availability': 1,
              'is_seed': False,
              'name': 'Wyatt.Cenacs.Problem.Areas.S01E10.720p.HDTV.x264-aAF[rarbg]/RARBG.txt',
              'piece_range': [0, 0],
              'priority': 4,
              'progress': 1,
              'size': 30},
            :return:
            """

            path = self.get_normalized_path()

            if path == "":
                return uw.Text(self.get_display_text())
            if self.get_node().get_key() is None:
                return uw.Text("Content")

            file_data = self.get_node().content.get_file_data(path=path)
            is_dir = self.get_node().content.is_dir(path)

            # calculate filename width
            file_node_offset = 1 if not is_dir else 0
            dir_node_offset = 3 if is_dir else 0
            depth_offset = (self.get_node().get_depth()) * 3
            filename_width = int(config.get("TORRENT_CONTENT_MAX_FILENAME_LENGTH")) - depth_offset - file_node_offset - dir_node_offset

            # filename
            filename = self.get_node().get_key()

            # map priority
            priority_map = {-1: "Mixed",
                            0: "Omitted",
                            1: "Normal",
                            4: "Normal",
                            6: "High",
                            7: "Maximal"}
            priority_raw = file_data['priority']
            priority = priority_map[priority_raw] if priority_raw in priority_map else priority_raw

            # availability
            availability_raw = file_data['availability']
            availability = "%s" % ("%3d%s" % (availability_raw*100, "%")) if availability_raw != -1 else "N/A"

            # remaining bytes
            size_raw = file_data['size']
            downloaded_bytes = file_data['completed']
            remaining_bytes = size_raw - downloaded_bytes

            return uw.Columns(
                    [
                        (filename_width, uw.Text(str(filename), align=uw.LEFT, wrap=uw.SPACE)),
                        (6, uw.Text("%s" % natural_file_size(size_raw, gnu=True), align=uw.RIGHT)),
                        DownloadProgressBar('pg normal', 'pg complete', current=downloaded_bytes, done=size_raw),
                        (8, uw.Text("%s" % priority, align=uw.LEFT)),
                        (6, uw.Text("%s" % natural_file_size(remaining_bytes, gnu=True), align=uw.RIGHT)),
                        (5, uw.Text("%s" % availability, align=uw.RIGHT)),
                    ],
                    dividechars=1
            )

        def get_normalized_path(self):
            path = self.get_node().get_value()
            if path.startswith("/"):
                path = path[1:]
            return path

        def keypress(self, size, key):
            """allow subclasses to intercept keystrokes"""
            key = self.__super.keypress(size, key)
            if key:
                key = self.unhandled_keys(size, key)

            # track expanding and collapsing dirs
            if not self.expanded:
                self.get_node().content.add_collapsed_dir(self.get_node().get_value())
            else:
                self.get_node().content.remove_collapsed_dir(self.get_node().get_value())
            return key

        # priority bumping handled here
        def unhandled_keys(self, size, key):
            if key in [" ", 'enter']:
                # self.flagged = not self.flagged
                # self.update_w()
                content = self.get_node().content
                path = self.get_normalized_path()
                file_data = content.get_file_data(path)
                file_ids = content.get_file_ids(path)
                next_priority_map = {-1: 0,
                                     0: 1,
                                     1: 6,
                                     4: 6,
                                     6: 7,
                                     7: 0}
                new_priority = next_priority_map[file_data['priority']]
                content.client.torrent_file_priority(torrent_id=content.torrent_hash,
                                                     file_ids=file_ids,
                                                     priority=new_priority)
            else:
                return key

        def update_w(self):
            """Update the attributes of self.widget based on self.flagged.
            """
            if self.flagged:
                self._w.attr = 'flagged'
                self._w.focus_attr = 'flagged focus'
            else:
                self._w.attr = ''
                self._w.focus_attr = 'selected'

    class FileTreeWidget(FlagFileWidget):
        """Widget for individual files."""

        def get_display_text(self):
            return self.get_node().get_key()

    class EmptyWidget(uw.TreeWidget):
        """A marker for expanded directories with no contents."""

        def get_display_text(self):
            return 'flag', '(empty directory)'

    class DirectoryWidget(FlagFileWidget):
        """Widget for a directory."""
        def __init__(self, node):
            super(ContentDisplay.DirectoryWidget, self).__init__(node)
            self.expanded = not (self.get_node().get_value() in self.get_node().content.get_collapsed_dirs())
            self.update_expanded_icon()

        def get_display_text(self):
            node = self.get_node()
            if node.get_depth() == 0:
                return "/"
            else:
                return node.get_key()

    class FileNode(uw.TreeNode):
        """Metadata storage for individual files"""

        @staticmethod
        def dir_sep(): return '/'

        def __init__(self, content, path, parent=None):
            self.content = content
            depth = path.count(self.dir_sep())
            # TODO: rewrite
            key = os.path.basename(path)
            uw.TreeNode.__init__(self, path, key=key, parent=parent, depth=depth)

        def load_parent(self):
            # TODO: rewrite
            parent_name, my_name = os.path.split(self.get_value())
            parent = ContentDisplay.DirectoryNode(self.content, parent_name)
            parent.set_child_node(self.get_key(), self)
            return parent

        def load_widget(self):
            return ContentDisplay.FileTreeWidget(self)

    class EmptyNode(uw.TreeNode):
        def load_widget(self):
            return ContentDisplay.EmptyWidget(self)

    class DirectoryNode(uw.ParentNode):
        """Metadata storage for directories"""
        @staticmethod
        def dir_sep(): return '/'

        def __init__(self, content, path, parent=None):
            self.content = content
            self.dir_count = 0

            # TODO: rewrite
            if path == self.dir_sep():
                depth = 0
                key = None
            else:
                depth = path.count(self.dir_sep())
                # TODO: rewrite
                key = os.path.basename(path)
            uw.ParentNode.__init__(self, path, key=key, parent=parent, depth=depth)

        def load_parent(self):
            # TODO: rewrite
            parent_name, my_name = os.path.split(self.get_value())
            parent = ContentDisplay.DirectoryNode(self.content, parent_name)
            parent.set_child_node(self.get_key(), self)
            return parent

        def load_child_keys(self):
            dirs = []
            files = []
            path = self.get_value()
            # separate dirs and files
            # TODO: rewrite
            for a in self.content.list_dir(path):
                if self.content.is_dir(os.path.join(path, a)):
                    dirs.append(a)
                else:
                    files.append(a)

            # sort dirs and files
            # dirs.sort(key=alphabetize)
            # files.sort(key=alphabetize)
            # store where the first file starts
            self.dir_count = len(dirs)
            # collect dirs and files together again
            keys = dirs + files
            if len(keys) == 0:
                depth = self.get_depth() + 1
                self._children[None] = ContentDisplay.EmptyNode(self,
                                                                parent=self,
                                                                key=None,
                                                                depth=depth)
                keys = [None]
            return keys

        def load_child_node(self, key):
            """Return either a FileNode or DirectoryNode"""
            index = self.get_child_index(key)
            if key is None:
                return ContentDisplay.EmptyNode(None)
            else:
                # TODO: rewrite
                path = os.path.join(self.get_value(), key)
                if index < self.dir_count:
                    return ContentDisplay.DirectoryNode(self.content, path, parent=self)
                else:
                    # TODO: rewrite
                    path = os.path.join(self.get_value(), key)
                    return ContentDisplay.FileNode(self.content, path, parent=self)

        def load_widget(self):
            return ContentDisplay.DirectoryWidget(self)
