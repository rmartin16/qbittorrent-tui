import logging
from time import time

import panwid
import urwid as uw

from qbittorrentui._attrdict import AttrDict
from qbittorrentui.config import STATE_MAP_FOR_DISPLAY
from qbittorrentui.config import TORRENT_LIST_FILTERING_STATE_MAP
from qbittorrentui.config import config
from qbittorrentui.connector import Connector
from qbittorrentui.debug import log_keypress
from qbittorrentui.debug import log_timing
from qbittorrentui.events import initialize_torrent_list
from qbittorrentui.events import refresh_torrent_list_now
from qbittorrentui.events import server_torrents_changed
from qbittorrentui.events import update_torrent_list_now
from qbittorrentui.formatters import natural_file_size
from qbittorrentui.formatters import pretty_time_delta
from qbittorrentui.misc_widgets import ButtonWithoutCursor
from qbittorrentui.misc_widgets import DownloadProgressBar
from qbittorrentui.misc_widgets import SelectableText
from qbittorrentui.windows.torrent import TorrentWindow

logger = logging.getLogger(__name__)


class TorrentListWindow(uw.Pile):
    def __init__(self, main):
        """

        :param main:
        :type main: main.Main()
        """
        self.main = main
        self.client = main.torrent_client

        self._width = None

        # initialize torrent list
        self.torrent_list_w = TorrentList(self)

        #  Set up torrent status tabs
        self.torrent_tabs_w = TorrentListTabsColumns()

        pile = [
            (1, self.torrent_tabs_w),
            (1, uw.Filler(uw.Divider())),
            self.torrent_list_w
        ]

        # initialize torrent list window
        super(TorrentListWindow, self).__init__(pile)

        # signals
        initialize_torrent_list.connect(receiver=self.torrent_list_init)

    @property
    def width(self):
        if self._width:
            return self._width
        else:
            return self.main.ui.get_cols_rows()[1]

    def render(self, size, focus=False):
        # catch screen resize
        start_time = time()
        if self._width != size[0]:
            self._width = size[0]
            # call to refresh_torrent_list on screen re-sizes
            refresh_torrent_list_now.send('torrent list render')
        ret = super(TorrentListWindow, self).render(size, focus)
        log_timing(logger, "Rendering", self, 'render', start_time)
        return ret

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(TorrentListWindow, self).keypress(size, key)
        if key in ['a', 'A']:
            self.main.loop.widget = uw.Overlay(top_w=uw.LineBox(TorrentAddDialog(self.main)),
                                               bottom_w=self.main.app_window,
                                               align=uw.CENTER,
                                               valign=uw.MIDDLE,
                                               width=(uw.RELATIVE, 50),
                                               height=(uw.RELATIVE, 50),
                                               min_width=20)
        return key

    def torrent_list_init(self, sender):
        """once connected to qbittorrent, initialize torrent list window"""
        server_torrents_changed.connect(receiver=self.update_torrent_list)
        refresh_torrent_list_now.connect(receiver=self.refresh_torrent_list)
        update_torrent_list_now.send("initialization")

    def update_torrent_list(self, sender, full_update=False, torrents=None, torrents_removed=None):
        """
        Update torrents with new data and refresh_torrent_list window.

        :param sender:
        :param full_update:
        :param torrents:
        :param torrents_removed:
        :return:
        """
        start_time = time()

        if torrents is None:
            torrents = dict()
        if torrents_removed is None:
            torrents_removed = dict()

        # this dictionary of torrents will only contain the data changed since last update...unless full update
        self.torrent_list_w.update(torrents=torrents, torrents_removed=torrents_removed, full_update=full_update)

        log_timing(logger, "Updating", self, sender, start_time)

        self.refresh_torrent_list(sender)

    def refresh_torrent_list(self, sender):
        """
        Refreshes the torrent list using local torrent data.

        :param sender:
        :return:
        """
        start_time = time()

        # save off focused row so it can be re-focused after refresh
        torrent_hash_in_focus = self.torrent_list_w.get_torrent_hash_for_focused_row()

        # dynamically resize torrent list based on window width
        self.torrent_list_w.resize()

        # put the relevant torrents in the walker
        self.torrent_list_w.apply_torrent_list_filter(status_filter=self.torrent_tabs_w.get_selected_tab_name())

        # re-focus same torrent if it still exists
        self.torrent_list_w.set_torrent_list_focus("torrent list refresh", torrent_hash=torrent_hash_in_focus)

        log_timing(logger, "Refreshing", self, sender, start_time)


class TorrentList(uw.ListBox):
    def __init__(self, torrent_list_box):
        super(TorrentList, self).__init__(uw.SimpleFocusListWalker([uw.Text("Loading...")]))
        # currently needed for resizing and creating TorrentRows
        self.torrent_list_box_w = torrent_list_box

        self.torrent_row_store = dict()
        """master torrent row widget list of all torrents"""

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(TorrentList, self).keypress(size, key)
        return key

    def get_torrent_hash_for_focused_row(self):
        focused_row, focused_row_pos = self.body.get_focus()
        if isinstance(focused_row, TorrentRow):
            return focused_row.base_widget.get_torrent_hash()
        return None

    def set_torrent_list_focus(self, sender="", torrent_hash: str = None):
        """
        Focus torrent row with provided torrent hash or focus first row

        :param sender:
        :param torrent_hash:
        """
        found = False
        if torrent_hash is not None:
            for pos, torrent in enumerate(self.body):
                if torrent.base_widget.get_torrent_hash() == torrent_hash:
                    self.body.set_focus(pos)
                    found = True
                    break
        if not found:
            self.body.set_focus(0)

    def apply_torrent_list_filter(self, status_filter: str):
        filtered_list = list()
        if status_filter != 'all':
            for torrent_row_w in self.torrent_row_store.values():
                state = torrent_row_w.base_widget.cached_torrent['state']
                if state in TORRENT_LIST_FILTERING_STATE_MAP[status_filter]:
                    filtered_list.append(torrent_row_w)
        else:
            filtered_list.extend(self.torrent_row_store.values())

        self.body = uw.SimpleFocusListWalker(filtered_list)

    def update(self, torrents: dict, torrents_removed: dict, full_update=False):
        for torrent_hash in torrents_removed:
            self.torrent_row_store.pop(torrent_hash)

        if full_update:
            self.torrent_row_store = dict()

        # add any new torrents added on the server
        # and update all torrents
        # this dictionary of torrents will only contain the data changed since last update
        for torrent_hash, torrent in torrents.items():
            if torrent_hash not in self.torrent_row_store:
                self.torrent_row_store[torrent_hash] = TorrentRow(torrent_list_box_w=self.torrent_list_box_w,
                                                                  torrent_hash=torrent_hash,
                                                                  torrent=torrent)
            self.torrent_row_store[torrent_hash].update(torrent)

    def resize(self):
        """
        Resize all torrent rows to screen width.

        1) Determine longest torrent name
        2) Resize all torrent names to max name length
        3) Determine widths of different sizings
        4) Apply largest sizing that fits
        """
        # torrent info width with graphic progress bar: 115

        name_list = [torrent_row_w.cached_torrent['name'] for torrent_row_w in self.torrent_row_store.values()]
        if name_list:
            max_name_len = min(int(config.get("TORRENT_LIST_MAX_TORRENT_NAME_LENGTH")), max(map(len, name_list)))
            for torrent_row_w in self.torrent_row_store.values():
                torrent_row_w.resize_name_len(max_name_len)
        else:
            max_name_len = 50

        if self.torrent_list_box_w.width < (max_name_len + 80):
            for torrent_row_w in self.torrent_row_store.values():
                # resize torrent name to 0 (effectively hiding it)
                #  name keeps resetting each time info is updated
                torrent_row_w.resize_name_len(0)
                if torrent_row_w.base_widget.current_sizing != "narrow":
                    # logger.info("Resizing %s to narrow" % torrent_row_w.base_widget.cached_torrent.name)
                    # ensure we're using the pb text
                    torrent_row_w.swap_pb_bar_for_pb_text()
                    # insert a blank space
                    torrent_row_w.base_widget.torrent_row_columns_w.base_widget.contents.insert(
                        0,
                        (
                            TorrentRowColumns.TorrentInfoColumnValueContainer(
                                name="blank",
                                raw_value=" ",
                                format_func=str),
                            torrent_row_w.base_widget.torrent_row_columns_w.base_widget.options(uw.PACK,
                                                                                                None,
                                                                                                False)
                        )
                    )
                    # add the torrent name as a new widget in the Pile for the TorrentRow
                    torrent_row_w.base_widget.contents.insert(
                        0,
                        (uw.Padding(uw.Text(torrent_row_w.cached_torrent['name'])),
                         ('pack', None))
                    )
                    torrent_row_w.base_widget.current_sizing = "narrow"

        elif self.torrent_list_box_w.width < (max_name_len + 115):
            for torrent_row_w in self.torrent_row_store.values():
                if torrent_row_w.base_widget.current_sizing != 'pb_text':
                    if torrent_row_w.base_widget.current_sizing == 'narrow':
                        torrent_row_w.base_widget.torrent_row_columns_w.base_widget.contents.pop(0)
                        torrent_row_w.base_widget.contents.pop(0)
                    # logger.info("Resizing %s to pb text" % torrent_row_w.base_widget.cached_torrent.name)
                    torrent_row_w.swap_pb_bar_for_pb_text()
                    torrent_row_w.base_widget.current_sizing = 'pb_text'

        else:
            for torrent_row_w in self.torrent_row_store.values():
                if torrent_row_w.base_widget.current_sizing != 'pb_bar':
                    if torrent_row_w.base_widget.current_sizing == 'narrow':
                        torrent_row_w.base_widget.torrent_row_columns_w.base_widget.contents.pop(0)
                        torrent_row_w.base_widget.contents.pop(0)
                    # logger.info("Resizing %s to pb bar" % torrent_row_w.base_widget.cached_torrent.name)
                    torrent_row_w.swap_pb_text_for_pb_bar()
                    torrent_row_w.base_widget.current_sizing = 'pb_bar'


class TorrentRow(uw.Pile):
    def __init__(self, torrent_list_box_w, torrent_hash: str, torrent: dict):
        """
        Build a row for the torrent list.

        :param torrent_list_box_w:
        :param torrent_hash:
        :param torrent:
        """
        self._hash = None
        self.torrent_list_box_w = torrent_list_box_w
        self.main = torrent_list_box_w.main

        self.current_sizing = None

        # TODO: stop caching the torrent
        self.cached_torrent = torrent

        # color based on state
        # TODO: move to config
        state = STATE_MAP_FOR_DISPLAY.get(torrent['state'], '')
        if state in ["Downloading", "Queued", "Stalled"]:
            attr = 'dark green on default'
        elif state == "Paused":
            attr = 'dark cyan on default'
        elif state in ["Completed", '[F] Seeding', 'Seeding']:
            attr = 'dark blue on default'
        elif state == 'Error':
            attr = 'light red on default'
        else:
            attr = ''

        # build and populate new torrent row
        self.torrent_row_columns_w = uw.AttrMap(w=TorrentRowColumns(),
                                                attr_map=attr,
                                                focus_map='selected')
        # store hash
        self.set_torrent_hash(torrent_hash)
        # build row widget
        super(TorrentRow, self).__init__([self.torrent_row_columns_w])

    def update(self, torrent: dict):
        self.cached_torrent.update(torrent)
        self.torrent_row_columns_w.base_widget.update(torrent)

    def resize_name_len(self, name_length: int):
        for i, w in enumerate(self.torrent_row_columns_w.base_widget.contents):
            if hasattr(w[0], 'name'):
                if w[0].name == 'name':
                    self.torrent_row_columns_w.base_widget.contents[i] = (
                        w[0], self.torrent_row_columns_w.base_widget.options(w[1][0], name_length, w[1][2]))
        self.torrent_row_columns_w.base_widget.name_len = name_length

    def swap_pb_bar_for_pb_text(self):
        for i, w in enumerate(self.torrent_row_columns_w.base_widget.contents):
            if hasattr(w[0], 'name'):
                if w[0].name == 'pb':
                    self.torrent_row_columns_w.base_widget.contents[i] = (
                        self.torrent_row_columns_w.base_widget.pb_text_w,
                        self.torrent_row_columns_w.base_widget.options(uw.GIVEN,
                                                                       len(self.torrent_row_columns_w.base_widget.pb_text_w),
                                                                       False)
                    )

    def swap_pb_text_for_pb_bar(self):
        for i, w in enumerate(self.torrent_row_columns_w.base_widget.contents):
            if hasattr(w[0], 'name'):
                if w[0].name == 'pb_text':
                    self.torrent_row_columns_w.base_widget.contents[i] = (
                        self.torrent_row_columns_w.base_widget.pb_w,
                        self.torrent_row_columns_w.base_widget.options(uw.GIVEN,
                                                                       int(config.get("TORRENT_LIST_PROGRESS_BAR_LENGTH")),
                                                                       False)
                    )

    def set_torrent_hash(self, torrent_hash):
        self._hash = torrent_hash

    def get_torrent_hash(self):
        return self._hash

    def open_torrent_options_window(self):
        torrent_name = self.cached_torrent.get('name', "")

        self.main.torrent_options_window = uw.Overlay(
            top_w=uw.LineBox(
                TorrentOptionsDialog(torrent_list_box_w=self.torrent_list_box_w,
                                     torrent_hash=self.get_torrent_hash(),
                                     torrent=self.cached_torrent),
                title=torrent_name
            ),
            bottom_w=self.torrent_list_box_w.main.app_window,
            align=uw.CENTER,
            width=(uw.RELATIVE, 50),
            valign=uw.MIDDLE,
            height=25,
            min_width=75)

        self.main.loop.widget = self.main.torrent_options_window

    def open_torrent_window(self):
        torrent_window = TorrentWindow(self.main,
                                       torrent_hash=self.get_torrent_hash(),
                                       torrent=self.cached_torrent,
                                       client=self.torrent_list_box_w.client)
        header_w = uw.Pile(
            [
                uw.Divider(),
                uw.Text(self.cached_torrent['name'], align=uw.CENTER, wrap=uw.CLIP)
            ]
        )
        frame_w = uw.Frame(body=torrent_window, header=header_w)
        self.main.app_window.body = frame_w

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        if key == 'enter':
            self.open_torrent_options_window()
            return None
        if key in ['right']:
            self.open_torrent_window()
            return None
        return key


class TorrentRowColumns(uw.Columns):
    def __init__(self):
        self.wide = False

        val_cont = TorrentRowColumns.TorrentInfoColumnValueContainer
        pb_cont = TorrentRowColumns.TorrentInfoColumnPBContainer

        def format_title(v): return str(v).ljust(int(config.get("TORRENT_LIST_MAX_TORRENT_NAME_LENGTH")))
        self.name_w = val_cont(name='name', raw_value="", format_func=format_title)

        def format_state(v):
            return (STATE_MAP_FOR_DISPLAY.get(v, v)).ljust(12)
        self.state_w = val_cont(name='state', raw_value="", format_func=format_state)

        def format_size(v): return natural_file_size(v, gnu=True).rjust(6)
        self.size_w = val_cont(name='size', raw_value=0, format_func=format_size)

        def format_pb(v: DownloadProgressBar):
            return v.get_percentage().rjust(4)
        self.pb_w = pb_cont(name='pb', current=0)
        self.pb_text_w = val_cont(name='pb_text', raw_value=self.pb_w, format_func=format_pb)

        def format_dl_speed(v): return "%s%s" % (natural_file_size(v, gnu=True).rjust(6), '\u25BC')
        self.dl_speed_w = val_cont(name='dlspeed', raw_value=0, format_func=format_dl_speed)

        def format_up_speed(v): return "%s%s" % (natural_file_size(v, gnu=True).rjust(6), '\u25B2')
        self.up_speed_w = val_cont(name='upspeed', raw_value=0, format_func=format_up_speed)

        def format_amt_uploaded(v): return "%s%s" % (natural_file_size(v, gnu=True).rjust(6), '\u21D1')
        self.amt_uploaded_w = val_cont(name='uploaded', raw_value=0, format_func=format_amt_uploaded)

        def format_ratio(v): return "R %.2f" % v
        self.ratio_w = val_cont(name='ratio', raw_value=0, format_func=format_ratio)

        def format_leech_num(v): return "L %3d" % v
        self.leech_num_w = val_cont(name='num_leechs', raw_value=0, format_func=format_leech_num)

        def format_seed_num(v): return "S %3d" % v
        self.seed_num_w = val_cont(name='num_seeds', raw_value=0, format_func=format_seed_num)

        def format_eta(v):
            return "ETA %s" % (pretty_time_delta(seconds=v) if v < 8640000 else '\u221E').ljust(6)
        self.eta_w = val_cont(name='eta', raw_value=8640000, format_func=format_eta)

        def format_category(v): return str(v)
        self.category_w = val_cont(name='category', raw_value="", format_func=format_category)

        self.pb_info_list = [
            # state
            (len(self.state_w), self.state_w),
            # size
            (len(self.size_w), self.size_w),
            # progress percentage
            (int(config.get("TORRENT_LIST_PROGRESS_BAR_LENGTH")), self.pb_w),
            # dl speed
            (len(self.dl_speed_w), self.dl_speed_w),
            # up speed
            (len(self.up_speed_w), self.up_speed_w),
            # amount uploaded
            (len(self.amt_uploaded_w), self.amt_uploaded_w),
            # share ratio
            (len(self.ratio_w), self.ratio_w),
            # seeders
            (len(self.seed_num_w), self.seed_num_w),
            # leechers
            (len(self.leech_num_w), self.leech_num_w),
            # ETA
            (len(self.eta_w), self.eta_w),
        ]

        self.pb_full_info_list = [(len(self.name_w), self.name_w)]
        self.pb_full_info_list.extend(self.pb_info_list)
        self.pb_full_info_list.append(self.category_w)

        self.text_pb_info_list = list(self.pb_full_info_list)
        self.text_pb_info_list.pop(3)
        self.text_pb_info_list.insert(3, (len(self.pb_text_w), self.pb_text_w))

        super(TorrentRowColumns, self).__init__(self.pb_full_info_list,
                                                dividechars=1,
                                                focus_column=None,
                                                min_width=1, box_columns=None)

    def update(self, torrent: dict):
        for w in self.contents:
            e = w[0]
            e.update(torrent)

    def keypress(self, size, key):
        """Ignore key presses by just returning key."""
        log_keypress(logger, self, key)
        return key

    class TorrentInfoColumnValueContainer(SelectableText):
        def __init__(self, name, raw_value, format_func):
            super(TorrentRowColumns.TorrentInfoColumnValueContainer, self).__init__("", wrap=uw.CLIP)

            self.name = name
            self.format_func = format_func

            self._raw_value = None
            self.raw_value = raw_value

        def __len__(self):
            return len(self.text)

        @property
        def raw_value(self):
            return self._raw_value

        @raw_value.setter
        def raw_value(self, v):
            self._raw_value = v
            self.set_text(self.format_func(v))

        def update(self, torrent: dict):
            if self.name == 'blank':
                pass
            elif self.name == "pb_text":
                self._raw_value.update(torrent)
                self.raw_value = self._raw_value
            else:
                if self.name in torrent:
                    self.raw_value = torrent[self.name]

    class TorrentInfoColumnPBContainer(DownloadProgressBar):
        def __init__(self, name, current, done=100):
            self.name = name
            super(TorrentRowColumns.TorrentInfoColumnPBContainer, self).__init__(
                'pg normal',
                'pg complete',
                current=current,
                done=done if done != 0 else 100
            )

        def __len__(self):
            return len(self.get_pb_text())

        def get_pb_text(self):
            return self.get_percentage().rjust(4)

        def update(self, torrent: dict):
            if 'completed' in torrent:
                self.current = torrent['completed']
            if 'size' in torrent:
                self.done = torrent['size'] if torrent['size'] != 0 else 100


class TorrentListTabsColumns(uw.Columns):
    def __init__(self):
        torrent_tabs_list = []
        for i, tab_name in enumerate(
                ["All", "Downloading", "Completed", "Paused", "Active", "Inactive", "Resumed"]):
            torrent_tabs_list.append(
                uw.AttrMap(
                    uw.Filler(SelectableText(tab_name,align=uw.CENTER)),
                    attr_map="selected" if i == 0 else "",
                    focus_map='selected')
            )
        super(TorrentListTabsColumns, self).__init__(widget_list=torrent_tabs_list,
                                                     dividechars=0,
                                                     focus_column=0)

    def get_selected_tab_name(self):
        """
        Used to drive filtering torrents in display

        :return:
        """
        return self.get_focus().base_widget.get_text()[0].lower()

    def move_cursor_to_coords(self, size, col, row):
        """Don't change focus based on coords"""
        return True

    @staticmethod
    def update_focused_tab(old_tab, new_tab):
        """
        Update tab attributes as user navigates through them.

        :param old_tab: previously selected tab
        :param new_tab: newly selected tab
        :return:
        """
        if old_tab is not new_tab:
            old_tab.set_attr_map({None: 'default'})
            new_tab.set_attr_map({None: 'selected'})
            refresh_torrent_list_now.send('torrents list tabs focus change')

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        old_tab: uw.AttrMap = self.get_focus()
        key = super(TorrentListTabsColumns, self).keypress(size, key)
        new_tab: uw.AttrMap = self.get_focus()
        self.update_focused_tab(old_tab=old_tab, new_tab=new_tab)
        return key


class TorrentOptionsDialog(uw.ListBox):
    client: Connector

    def __init__(self, torrent_list_box_w: TorrentListWindow, torrent_hash, torrent):
        self.torrent_list_box_w = torrent_list_box_w
        self.main = self.torrent_list_box_w.main
        self.torrent_hash = torrent_hash
        self.torrent = torrent
        self.client = self.torrent_list_box_w.client

        self.torrent = AttrDict(torrent)

        self.delete_files_w = None

        categories = {x: x for x in list(self.main.server.categories.keys())}
        categories["<no category>"] = "<no category>"

        self.original_location = self.torrent.save_path
        self.location_w = uw.Edit(caption="Save path: ",
                                  edit_text=self.original_location)
        self.original_name = self.torrent.name
        self.rename_w = uw.Edit(caption="Rename: ",
                                edit_text=self.original_name)
        self.original_autotmm_state = self.torrent.auto_tmm
        self.autotmm_w = uw.CheckBox("Automatic Torrent Management",
                                     state=self.original_autotmm_state)
        self.original_super_seeding_state = self.torrent.super_seeding
        self.super_seeding_w = uw.CheckBox("Super Seeding Mode",
                                           state=self.original_super_seeding_state)
        self.original_upload_rate_limit = self.torrent.up_limit
        self.upload_rate_limit_w = uw.IntEdit(caption="Upload Rate Limit (Kib/s)  : ",
                                              default=int(
                                                  self.original_upload_rate_limit / 1024) if self.original_upload_rate_limit != -1 else "")
        self.original_download_rate_limit = self.torrent.dl_limit
        self.download_rate_limit_w = uw.IntEdit(caption="Download Rate Limit (Kib/s): ",
                                                default=int(
                                                    self.original_download_rate_limit / 1024) if self.original_download_rate_limit != -1 else "")
        # TODO: accommodate share ratio and share time
        self.original_share_ratio = self.torrent.ratio_limit
        self.share_ratio_dropdown_w = panwid.Dropdown(items=[("Global Limit", -2), ("Unlimited", -1), ("Specify", 0)],
                                                      label="Share Ratio: ",
                                                      default=self.torrent.ratio_limit if self.torrent.ratio_limit in [
                                                          -2, -1] else 0)
        if self.torrent.ratio_limit >= 0:
            self.original_share_ratio_percentage = int(self.torrent.ratio_limit * 100)
            self.original_share_minutes = self.torrent.seeding_time_limit
        else:
            self.original_share_ratio_percentage = None
            self.original_share_minutes = None
        self.share_ratio_limit_w = uw.IntEdit(caption="Share ratio limit (%): ",
                                              default=self.original_share_ratio_percentage)
        self.share_ratio_minutes_w = uw.IntEdit(caption="Share ratio minutes: ", default=self.original_share_minutes)
        self.original_category = self.torrent.category if self.torrent.category != "" else "<no category>"
        self.category_w = panwid.Dropdown(items=categories,
                                          label="Category",
                                          default=self.original_category,
                                          auto_complete=True)

        super(TorrentOptionsDialog, self).__init__(uw.SimpleFocusListWalker(
            [
                uw.Divider(),
                uw.Columns(
                    [
                        uw.Padding(uw.Text('')),
                        (10, uw.AttrMap(ButtonWithoutCursor("Resume",
                                                            on_press=self.resume_torrent),
                                        '', focus_map='selected')),
                        (16, uw.AttrMap(ButtonWithoutCursor("Force Resume",
                                                            on_press=self.force_resume_torrent),
                                        '', focus_map='selected')),
                        (9, uw.AttrMap(ButtonWithoutCursor("Pause",
                                                           on_press=self.pause_torrent),
                                       '', focus_map='selected')),
                        uw.Padding(uw.Text('')),
                    ],
                    dividechars=2
                ),
                uw.Divider(),
                uw.Columns(
                    [
                        uw.Padding(uw.Text('')),
                        (10, uw.AttrMap(ButtonWithoutCursor("Delete",
                                                            on_press=self.delete_torrent),
                                        '', focus_map='selected')),
                        (11, uw.AttrMap(ButtonWithoutCursor("Recheck",
                                                            on_press=self.recheck_torrent),
                                        '', focus_map='selected')),
                        (14, uw.AttrMap(ButtonWithoutCursor("Reannounce",
                                                            on_press=self.reannounce_torrent),
                                        '', focus_map='selected')),
                        uw.Padding(uw.Text('')),
                    ],
                    dividechars=2
                ),
                uw.Divider(),
                self.location_w,
                uw.Divider(),
                self.rename_w,
                uw.Divider(),
                uw.Columns(
                    [
                        uw.Padding(uw.Text('')),
                        (33, self.autotmm_w),
                        (23, self.super_seeding_w),
                        uw.Padding(uw.Text('')),
                    ],
                    dividechars=2
                ),
                uw.Divider(),
                self.share_ratio_dropdown_w,
                self.share_ratio_limit_w,
                self.share_ratio_minutes_w,
                uw.Divider(),
                self.upload_rate_limit_w,
                self.download_rate_limit_w,
                uw.Divider(),
                self.category_w,
                uw.Divider(),
                uw.Divider(),
                uw.Columns(
                    [
                        uw.Padding(uw.Text('')),
                        (6, uw.AttrMap(ButtonWithoutCursor("OK",
                                                           on_press=self.apply_settings),
                                       '', focus_map='selected')),
                        (10, uw.AttrMap(ButtonWithoutCursor("Cancel",
                                                            on_press=self.close_window),
                                        '', focus_map='selected'))
                    ],
                    dividechars=2,
                )
            ]
        ))

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(TorrentOptionsDialog, self).keypress(size, {"shift tab": "up", "tab": "down"}.get(key, key))
        if key == 'esc':
            self.close_window()
        return key

    def apply_settings(self, b):

        new_location = self.location_w.get_edit_text()
        new_name = self.rename_w.get_edit_text()
        new_autotmm_state = self.autotmm_w.get_state()
        new_super_seeding_state = self.super_seeding_w.get_state()
        new_share_ratio = self.share_ratio_dropdown_w.selected_value
        if self.share_ratio_limit_w.get_edit_text():
            new_share_ratio_percentage = int(self.share_ratio_limit_w.get_edit_text()) / 100
        else:
            new_share_ratio_percentage = self.original_share_ratio_percentage
        if self.share_ratio_minutes_w.get_edit_text():
            new_share_ratio_minutes = self.share_ratio_minutes_w.get_edit_text()
        else:
            new_share_ratio_minutes = self.original_share_minutes
        if self.upload_rate_limit_w.get_edit_text() != "":
            new_upload_rate_limit = int(self.upload_rate_limit_w.get_edit_text()) * 1024
        else:
            new_upload_rate_limit = self.original_upload_rate_limit
        if self.download_rate_limit_w.get_edit_text() != "":
            new_download_rate_limit = int(self.download_rate_limit_w.get_edit_text()) * 1024
        else:
            new_download_rate_limit = self.original_download_rate_limit
        new_category = self.category_w.selected_label

        if new_location != self.original_location:
            logger.info("Setting new location: %s (%s)" % (new_location, self.torrent_hash))
            self.client.torrents_set_location(location=new_location, torrent_ids=self.torrent_hash)

        if new_name != self.original_name:
            logger.info("Setting new name: %s (%s)" % (new_name, self.torrent_hash))
            self.client.torrent_rename(new_name=new_name, torrent_id=self.torrent_hash)

        if new_autotmm_state is not self.original_autotmm_state:
            logger.info("Setting Auto TMM: %s (%s)" % (new_autotmm_state, self.torrent_hash))
            self.client.torrents_set_automatic_torrent_management(enable=new_autotmm_state,
                                                                  torrent_ids=self.torrent_hash)

        if new_super_seeding_state is not self.original_super_seeding_state:
            logger.info("Setting super seeding: %s (%s)" % (new_super_seeding_state, self.torrent_hash))
            self.client.torrents_set_super_seeding(enable=new_super_seeding_state, torrent_ids=self.torrent_hash)

        if new_upload_rate_limit != self.original_upload_rate_limit:
            logger.info("Setting new upload rate: %s (%s)" % (new_upload_rate_limit, self.torrent_hash))
            self.client.torrents_set_upload_limit(limit=new_upload_rate_limit, torrent_ids=self.torrent_hash)

        if new_download_rate_limit != self.original_download_rate_limit:
            logger.info("Setting new download rate: %s (%s)" % (new_download_rate_limit, self.torrent_hash))
            self.client.torrents_set_download_limit(limit=new_download_rate_limit, torrent_ids=self.torrent_hash)

        if new_category != self.original_category:
            if new_category == '<no category>':
                new_category = ""
            logger.info("Setting new category: %s (%s)" % (new_category, self.torrent_hash))
            self.client.torrents_set_category(category=new_category, torrent_ids=self.torrent_hash)

        if new_share_ratio != self.original_share_ratio:
            if new_share_ratio in [-1, -2]:
                self.client.torrents_set_share_limits(ratio_limit=new_share_ratio,
                                                      seeding_time_limit=new_share_ratio,
                                                      torrent_ids=self.torrent_hash)
            else:
                self.client.torrents_set_share_limits(ratio_limit=new_share_ratio_percentage,
                                                      seeding_time_limit=new_share_ratio_minutes,
                                                      torrent_ids=self.torrent_hash)

        self.reset_screen_to_torrent_list_window()

    def close_window(self, b=None):
        self.reset_screen_to_torrent_list_window()

    def resume_torrent(self, b):
        self.client.torrents_resume(torrent_ids=self.torrent_hash)
        self.reset_screen_to_torrent_list_window()

    def force_resume_torrent(self, b):
        self.client.torrents_force_resume(torrent_ids=self.torrent_hash)
        self.reset_screen_to_torrent_list_window()

    def delete_torrent(self, b):
        self.delete_files_w = uw.CheckBox(label="Delete Files")
        self.main.loop.widget = uw.Overlay(
            top_w=uw.LineBox(
                uw.ListBox(
                    uw.SimpleFocusListWalker(
                        [
                            uw.Divider(),
                            self.delete_files_w,
                            uw.Divider(),
                            uw.Columns(
                                [
                                    uw.Padding(uw.Text('')),
                                    (
                                        6,
                                        uw.AttrMap(
                                            ButtonWithoutCursor(
                                                "OK",
                                                on_press=self.confirm_delete
                                            ),
                                            '',
                                            focus_map='selected'
                                        )
                                     ),
                                    (
                                        10,
                                        uw.AttrMap(
                                            ButtonWithoutCursor(
                                                "Cancel",
                                                on_press=self.close_delete_dialog
                                            ),
                                            '',
                                            focus_map='selected'
                                        )
                                    )
                                ],
                                dividechars=2,
                            ),
                        ]
                    )
                )
            ),
            bottom_w=self.main.app_window,
            align=uw.CENTER,
            valign=uw.MIDDLE,
            width=30,
            height=10,
            min_width=20
        )

    def confirm_delete(self, b):
        delete_files = self.delete_files_w.get_state()
        self.client.torrents_delete(torrent_ids=self.torrent_hash, delete_files=delete_files)
        self.reset_screen_to_torrent_list_window()

    def close_delete_dialog(self, b):
        self.main.loop.widget = self.main.app_window

    def pause_torrent(self, b):
        self.client.torrents_pause(torrent_ids=self.torrent_hash)
        self.reset_screen_to_torrent_list_window()

    def recheck_torrent(self, b):
        self.client.torrents_recheck(torrent_ids=self.torrent_hash)
        self.reset_screen_to_torrent_list_window()

    def reannounce_torrent(self, b):
        self.client.torrents_reannounce(torrent_ids=self.torrent_hash)
        self.reset_screen_to_torrent_list_window()

    def reset_screen_to_torrent_list_window(self):
        update_torrent_list_now.send("torrent menu")
        self.main.loop.widget = self.main.app_window


class TorrentAddDialog(uw.ListBox):
    def __init__(self, main):
        self.main = main

        categories = {x: x for x in list(self.main.server.categories.keys())}
        categories["<no category>"] = "<no category>"

        prefs = self.main.daemon.get_server_preferences()

        self.torrent_file_w = uw.Edit(caption="Torrent file path: ")
        self.torrent_url_w = uw.Edit(caption="Torrent url: ")
        self.autotmm_w = uw.CheckBox("Automatic Torrent Management",
                                     state=prefs.auto_tmm_enabled)
        self.location_w = uw.Edit(caption="Save path: ",
                                  edit_text=prefs.save_path)
        self.name_w = uw.Edit(caption="Custom name: ")
        self.category_w = panwid.Dropdown(items=categories,
                                          label="Category",
                                          default="<no category>",
                                          auto_complete=True)
        self.start_torrent_w = uw.CheckBox("Start Torrent",
                                           state=(not prefs.start_paused_enabled))
        self.download_in_sequential_order_w = uw.CheckBox("Download in Sequential Order")
        self.download_first_last_first_w = uw.CheckBox("Download First and Last Pieces First")
        self.skip_hash_check_w = uw.CheckBox("Skip Hash Check")
        self.create_subfolder_w = uw.CheckBox("Create Subfolder",
                                              state=prefs.create_subfolder_enabled)
        self.upload_rate_limit_w = uw.IntEdit(caption="Upload Rate Limit (Kib/s)  : ")
        self.download_rate_limit_w = uw.IntEdit(caption="Download Rate Limit (Kib/s): ")

        super(TorrentAddDialog, self).__init__(
            uw.SimpleFocusListWalker(
                [
                    self.torrent_file_w,
                    self.torrent_url_w,
                    uw.Divider(),
                    self.location_w,
                    self.name_w,
                    uw.Divider(),
                    self.category_w,
                    uw.Divider(),
                    self.autotmm_w,
                    self.start_torrent_w,
                    self.create_subfolder_w,
                    self.skip_hash_check_w,
                    self.download_in_sequential_order_w,
                    self.download_first_last_first_w,
                    uw.Divider(),
                    self.upload_rate_limit_w,
                    self.download_rate_limit_w,
                    uw.Divider(),
                    uw.Divider(),
                    uw.Columns(
                        [
                            uw.Padding(uw.Text('')),
                            (
                                6,
                                uw.AttrMap(
                                    ButtonWithoutCursor(
                                        "OK",
                                        on_press=self.add_torrent
                                    ),
                                    '',
                                    focus_map='selected'
                                )
                            ),
                            (
                                10,
                                uw.AttrMap(
                                    ButtonWithoutCursor(
                                        "Cancel",
                                        on_press=self.close_window
                                    ),
                                    '',
                                    focus_map='selected'
                                )
                            )
                        ],
                        dividechars=2,
                    ),
                ]
            )
        )

    def add_torrent(self, b):
        torrent_file = self.torrent_file_w.get_edit_text()
        torrent_url = self.torrent_url_w.get_edit_text()
        is_autotmm = self.autotmm_w.get_state()
        save_path = self.location_w.get_edit_text()
        name = self.name_w.get_edit_text()
        category = self.category_w.selected_label
        is_start_torrent = self.start_torrent_w.get_state()
        is_seq_download = self.download_in_sequential_order_w.get_state()
        is_first_last_download = self.download_first_last_first_w.get_state()
        is_skip_hash = self.skip_hash_check_w.get_state()
        is_create_subfolder = self.create_subfolder_w.get_state()
        upload_limit = self.upload_rate_limit_w.get_edit_text()
        download_limit = self.download_rate_limit_w.get_edit_text()

        try:
            upload_limit = int(upload_limit) * 1024
        except ValueError:
            upload_limit = None
        try:
            download_limit = int(download_limit) * 1024
        except ValueError:
            download_limit = None

        outcome = self.main.torrent_client.torrents_add(
            urls=torrent_url if torrent_url else None,
            torrent_files=torrent_file if torrent_file else None,
            save_path=save_path if save_path else None,
            cookie=None,
            category=category if category != "<no category>" else None,
            is_skip_checking=is_skip_hash,
            is_paused=(not is_start_torrent),
            is_root_folder=is_create_subfolder,
            rename=name if name else None,
            upload_limit=upload_limit,
            download_limit=download_limit,
            use_auto_torrent_management=is_autotmm,
            is_sequential_download=is_seq_download,
            is_first_last_piece_priority=is_first_last_download
        )
        self.reset_screen_to_torrent_list_window()

    def keypress(self, size, key):
        log_keypress(logger, self, key)
        key = super(TorrentAddDialog, self).keypress(size, {"shift tab": "up", "tab": "down"}.get(key, key))
        if key == 'esc':
            self.close_window()
        return key

    def close_window(self, b=None):
        self.reset_screen_to_torrent_list_window()

    def reset_screen_to_torrent_list_window(self):
        update_torrent_list_now.send("torrent add")
        self.main.loop.widget = self.main.app_window
