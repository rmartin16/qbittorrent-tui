import urwid as uw

from qbittorrentui.formatters import natural_file_size


class ButtonWithoutCursor(uw.Button):
    button_left = "["
    button_right = "]"

    def __init__(self, label, on_press=None, user_data=None):
        self._label = ButtonWithoutCursor.ButtonLabel("")
        cols = uw.Columns([
            ('fixed', len(self.button_left), uw.Text(self.button_left)),
            self._label,
            ('fixed', len(self.button_right), uw.Text(self.button_right))],
            dividechars=1)
        super(uw.Button, self).__init__(cols)

        if on_press:
            uw.connect_signal(self, 'click', on_press, user_data)

        self.set_label(label)

    class ButtonLabel(uw.SelectableIcon):
        def set_text(self, label):
            self.__super.set_text(label)
            self._cursor_position = len(label) + 1


class DownloadProgressBar(uw.ProgressBar):
    def __init__(self, normal, complete, current=0, done=100, satt=None):
        if done == 0:
            done = 100
        super(DownloadProgressBar, self).__init__(normal=normal,
                                                  complete=complete,
                                                  current=current,
                                                  done=done,
                                                  satt=satt)

    def get_text(self):
        return "%s %s" % (natural_file_size(self.current, gnu=True).rjust(7),
                          ("(%s)" % self.get_percentage()).ljust(6))

    def get_percentage(self):
        try:
            percent = int(self.current * 100 / self.done)
        except ZeroDivisionError:
            percent = "unk"
        return "%s%s" % (percent, "%" if percent != "unk" else "")


class SelectableText(uw.Text):
    _selectable = True

    @staticmethod
    def keypress(_, key):
        return key
