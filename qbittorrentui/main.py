import logging
from os import environ
from time import time, sleep

import blinker
import urwid as uw

from qbittorrentui.config import APPLICATION_NAME
from qbittorrentui.config import config
from qbittorrentui.connector import Connector
from qbittorrentui.daemon import DaemonManager
from qbittorrentui.events import connection_to_server_acquired
from qbittorrentui.events import connection_to_server_lost
from qbittorrentui.events import exit_tui
from qbittorrentui.events import server_details_changed
from qbittorrentui.events import server_state_changed
from qbittorrentui.events import server_torrents_changed
from qbittorrentui.windows.application import AppWindow
from qbittorrentui.windows.application import ConnectDialog

try:
    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] {%(name)s:%(lineno)d} %(levelname)s - %(message)s',
                        filename='/home/russell/github/qbittorrentui/output.txt',
                        filemode='w')
except Exception:
    logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


class TorrentServer:
    daemon: DaemonManager

    def __init__(self, daemon):
        self.daemon = daemon
        self.server_state = dict()
        self.categories = dict()
        self.partial_daemon_signal = ""

    def daemon_signal(self, signal):
        signal_str = signal.decode()
        # it's technically possible for the daemon to send multiple signals before
        # the urwid event loop reads from the buffer. (this is most obvious if you set a
        # breakpoint pausing the urwid loop.) therefore, signals can all be read together.
        # this looping will allow each signal to still be processed as intended.
        # additionally, if the buffer grows too large, the urwid documentation suggests
        # only some of the buffer will be read in. so, if the signal string doesn't end
        # in the daemon signal terminator, save off the end of the signal for the next loop.
        if signal_str:
            signal_list = signal_str.split(self.daemon.signal_terminator)
            if signal_str.endswith(self.daemon.signal_terminator):
                # if the signal list terminates as expected, prepend any partial signal
                # to the first signal and process the signals
                signal_list[0] = "%s%s" % (self.partial_daemon_signal, signal_list[0])
                self.partial_daemon_signal = ""
                # remove the last element since it'll just be an empty string
                signal_list.pop(-1)
            else:
                # if the last signal doesnt end with the terminator, remove it from the
                # signal list to be processed and concatenate to any previous partial signal
                # saving the whole thing as the new partial signal.
                # if the whole new signal string was a new partial, then nothing will be
                # processed for this signal event.
                self.partial_daemon_signal = "%s%s" % (self.partial_daemon_signal, signal_list.pop(-1))
            for one_signal in signal_list:
                signal_parts = one_signal.split(self.daemon.signal_delimiter)
                sender = signal_parts[0]
                signal = signal_parts[1]
                extra = list(signal_parts[2:])
                if signal == "sync_maindata_ready":
                    self.update_sync_maindata()
                elif signal == "server_details_ready":
                    self.update_details()
                elif signal == "sync_torrent_data_ready":
                    self.update_sync_torrents(torrent_hash=extra[0])
                elif signal == "connection_lost":
                    connection_to_server_lost.send(sender)
                elif signal == "connection_acquired":
                    connection_to_server_acquired.send(sender)
                elif signal == "close_pipe":
                    # tell urwid loop to close the read end of the pipe...daemon will close write end
                    return False
                else:
                    logger.info("Received unknown signal from daemon: "
                                "sender: %s signal: %s" % (sender, signal), exc_info=True)
            return True

    def update_details(self):
        server_details_changed.send('torrent server', details=self.daemon.get_server_details())

    def update_sync_maindata(self):
        """
        Retrieve maindata from bg daemon and update local server state

        :return:
        """
        server_details_updated = False
        server_torrents_updated = False

        # flush the queue if it backs up for any reason...
        while not self.daemon.sync_maindata_q.empty():
            md = self.daemon.sync_maindata_q.get()

            if md.full_update:
                self.server_state = md.server_state
                server_details_updated = True
                server_torrents_updated = True
                self.categories = md.categories

            else:
                if md.server_state:
                    self.server_state.update(md.server_state)
                    server_details_updated = True

                # if torrents removed or updated, send the updates
                if md.torrents_removed or md.torrents:
                    server_torrents_updated = True

                # remove categories no longer in qbittorrent
                for category in md.categories_removed:
                    self.categories.pop(category, None)
                # add new categories or new category info
                for category_name, category in md.categories.items():
                    if category in self.categories:
                        self.categories[category_name].update(category)
                    else:
                        self.categories[category_name] = category

            if server_torrents_updated:
                server_torrents_changed.send('maindata update',
                                             full_update=md.full_update,
                                             torrents=md.torrents,
                                             torrents_removed=md.torrents_removed)

        if server_details_updated:
            server_state_changed.send('maindata update', server_state=self.server_state)

    def update_sync_torrents(self, torrent_hash):
        store = self.daemon.get_torrent_store(torrent_hash=torrent_hash)
        if store is not None:
            blinker.signal(torrent_hash).send('sync_torrent_update',
                                              torrent=store.torrent,
                                              properties=store.properties,
                                              trackers=store.trackers,
                                              sync_torrent_peers=store.sync_torrent_peers,
                                              content=store.content)


class Main(object):
    server: TorrentServer
    torrent_client: Connector
    daemon: DaemonManager
    loop: uw.MainLoop

    def __init__(self, args=None):
        super(Main, self).__init__()

        if args.config_file:
            config.read(filenames=args.config_file)

        self.ui = uw.raw_display.Screen()
        self.loop = uw.MainLoop(widget=None,
                                unhandled_input=self.unhandled_urwid_loop_input)
        self.torrent_client = Connector()
        # TODO: revamp data sharing between daemon and torrent server such that
        #       torrent server isn't dependent on daemon. This will likely require
        #       a single queue between the two. May be too much trouble though...
        self.daemon = DaemonManager(torrent_client=self.torrent_client,
                                    daemon_signal_fd=self.loop.watch_pipe(callback=self.daemon_signal))
        self.server = TorrentServer(daemon=self.daemon)

        connection_to_server_lost.connect(receiver=self.connection_lost)
        connection_to_server_acquired.connect(receiver=self.connection_acquired)
        exit_tui.connect(receiver=self.stop_loop_and_cleanup)

    def daemon_signal(self, *a, **kw):
        return self.server.daemon_signal(*a, **kw)

    def connection_lost(self, sender):
        logger.info("Connection lost...")
        connect_dialog_w = ConnectDialog(self, error_message="Connection lost...attempting automatic reconnection")
        self.loop.widget = uw.Overlay(top_w=uw.LineBox(connect_dialog_w),
                                      bottom_w=self.loop.widget,
                                      align=uw.CENTER,
                                      width=(uw.RELATIVE, 50),
                                      valign=uw.MIDDLE,
                                      height=(uw.RELATIVE, 50))

    def connection_acquired(self, sender):
        logger.info("Connection reacquired...")
        try:
            if type(self.loop.widget.top_w.base_widget) == ConnectDialog:
                self.loop.widget = self.loop.widget.bottom_w
        except AttributeError:
            pass

    #########################################
    # Start Application
    #########################################
    def start(self):
        self._setup_screen()
        self._setup_splash()
        self._setup_urwid_loop()
        self._start_tui()

    #########################################
    # start() calls the preceding methods in the order they are listed
    #########################################
    def _setup_screen(self):
        logger.info("Setting up screen")
        palette = [
            ('dark blue on default', 'dark blue', ''),
            ('dark cyan on default', 'dark cyan', ''),
            ('dark green on default', 'dark green', ''),
            ('light red on default', 'light red', '',),
            ('selected', 'white,bold', 'dark blue', 'standout'),
            ('pg normal', '', ''),
            ('pg complete', '', 'dark blue'),
            ('pg smooth', '', ''),

            ('body', 'black', 'light gray', 'standout'),
            ('header', 'white', 'dark red', 'bold'),
            ('screen edge', 'light blue', 'dark cyan'),
            ('main shadow', 'dark gray', 'black'),
            ('line', 'black', 'light gray', 'standout'),
            ('bg background', 'light gray', 'black'),
            ('bg 1', 'black', 'dark blue', 'standout'),
            ('bg 1 smooth', 'dark blue', 'black'),
            ('bg 2', 'black', 'dark cyan', 'standout'),
            ('bg 2 smooth', 'dark cyan', 'black'),
            ('button normal', 'light gray', 'dark blue', 'standout'),
            ('button select', 'white', 'dark green'),
            ('line', 'black', 'light gray', 'standout'),
            ('reversed', 'standout', ''),
        ]
        self.ui.set_terminal_properties(colors=256)
        self.ui.register_palette(palette=palette)

    def _setup_splash(self):
        logger.info("Creating splash window")
        self.splash_screen = uw.Overlay(
            uw.BigText(APPLICATION_NAME, uw.Thin6x6Font()),
            uw.SolidFill(),
            'center', None, 'middle', None)

    def _setup_urwid_loop(self):
        logger.info("Setting up urwid loop")
        self.loop.widget = self.splash_screen
        self.loop.screen = self.ui
        self.loop.handle_mouse = False
        self.loop.pop_ups = True

    def _start_tui(self):
        logger.info("Starting urwid loop")
        self.loop.set_alarm_in(.001, callback=self._finish_setup)
        try:
            self.loop.run()
        except KeyboardInterrupt:
            self.cleanup()

    def _finish_setup(self, loop, _):
        """
        Once the TUI shows the splash screen, setup will continue here

        :param loop: urwid loop
        :param _: user_data from urwid loop
        """
        start_time = time()
        self._start_daemon()
        sleep_time_to_show_splash = 1 - (time() - start_time)
        if environ.get('PYTHON_QBITTORRENTUI_DEV_ENV'):
            sleep_time_to_show_splash = 0
        # show splash screen for at least one second during startup
        if sleep_time_to_show_splash > 0:
            sleep(1 - (time() - start_time))
        self._show_application()

    def _start_daemon(self):
        logger.info("Starting background daemon")
        self.daemon.start()

    def _show_application(self):
        logger.info("Showing %s" % APPLICATION_NAME)
        self.app_window = AppWindow(main=self)
        connect_dialog_w = ConnectDialog(main=self, support_auto_connect=True)
        self.loop.widget = uw.Overlay(top_w=uw.LineBox(connect_dialog_w),
                                      bottom_w=self.app_window,
                                      align=uw.CENTER,
                                      width=(uw.RELATIVE, 50),
                                      valign=uw.MIDDLE,
                                      height=(uw.RELATIVE, 50))

    #########################################
    # Cleanup and Exit - always exit through here
    #########################################
    @staticmethod
    def unhandled_urwid_loop_input(key):
        if key in ('q', 'Q'):
            exit_tui.send("main loop unhandled input")

    def stop_loop_and_cleanup(self, sender):
        """
        Receiver of exit_tui signal from within the urwid loop to exit the app.

        :param sender:
        :return:
        """
        logger.info(f"Exiting TUI (from {sender})")
        self.clear_screen()
        self.cleanup()
        raise uw.ExitMainLoop()

    def clear_screen(self):
        self.loop.widget = uw.Filler(uw.Text(''))
        self.loop.draw_screen()

    def cleanup(self):
        self.daemon.stop()
        self.daemon.join(2)


def run(args):
    program = Main(args=args)
    try:
        program.start()
    except Exception:
        # try to print some mildly helpful info about the crash
        import sys
        from pprint import pprint as pp
        exc_type, exc_value, tb = sys.exc_info()
        if tb is not None:
            prev = tb
            curr = tb.tb_next
            while curr is not None:
                prev = curr
                curr = curr.tb_next
            try:
                pp(prev.tb_frame.f_locals)
            except Exception:
                pass

        print()
        program.cleanup()
        raise
