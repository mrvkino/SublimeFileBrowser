# coding: utf-8

from __future__ import print_function
import sublime
from sublime import Region
from sublime_plugin import WindowCommand, TextCommand
import os
from os.path import basename, dirname, isdir, exists, join

ST3 = int(sublime.version()) >= 3000

if ST3:
    from .common import DiredBaseCommand, print, set_proper_scheme, calc_width, hijack_window, NT, PARENT_SYM
    from . import prompt
    from .show import show
    from .jumping import jump_names
else:  # ST2 imports
    from common import DiredBaseCommand, print, set_proper_scheme, calc_width, hijack_window, NT, PARENT_SYM
    import prompt
    from show import show
    from jumping import jump_names


def reuse_view():
    return sublime.load_settings('dired.sublime-settings').get('dired_reuse_view', False)


def plugin_loaded():
    if len(sublime.windows()) == 1 and len(sublime.windows()[0].views()) == 0:
        hijack_window()

    window = sublime.active_window()
    if not ST3:
        global recursive_plugin_loaded
        # recursion limit is 1000 generally, so it will try to refresh for 100*1000 ms (100 s)
        # if no active_window in 100 s, then no refresh
        # if view still loading, refresh fail because view cant be edited
        if not window or any(view.is_loading() for view in window.views()):
            recursive_plugin_loaded += 1
            try:
                return sublime.set_timeout(plugin_loaded, 100)
            except RuntimeError:
                print('\ndired.plugin_loaded run recursively %d time(s); and failed to refresh\n' % recursive_plugin_loaded)
                return

    for v in window.views():
        if v.settings() and v.settings().get("dired_path"):
            # reset sels because dired_index not exists yet, so we cant restore sels
            v.run_command("dired_refresh", {"reset_sels": True})
    # if not ST3:
    #     print('\ndired.plugin_loaded run recursively %d time(s); and call refresh command\n'%recursive_plugin_loaded)

if not ST3:
    recursive_plugin_loaded = 1
    plugin_loaded()


class DiredCommand(WindowCommand):
    """
    Prompt for a directory to display and display it.
    """
    def run(self, immediate=False, single_pane=False, project=False, other_group=False):
        path, goto = self._determine_path()
        if project:
            folders = self.window.folders()
            if len(folders) == 1:
                path = folders[0]
            elif folders:
                names = [basename(f) for f in folders]
                longest_name = max([len(n) for n in names])
                for i, f in enumerate(folders):
                    name     = names[i]
                    offset   = ' ' * (longest_name - len(name) + 1)
                    names[i] = u'%s%s%s' % (name, offset, self.display_path(f))
                self.window.show_quick_panel(names, lambda i: self._show_folder(i, path, goto, single_pane, other_group), sublime.MONOSPACE_FONT)
                return
        if immediate:
            show(self.window, path, goto=goto, single_pane=single_pane, other_group=other_group)
        else:
            prompt.start('Directory:', self.window, path, self._show)

    def _show_folder(self, index, path, goto, single_pane, other_group):
        if index != -1:
            choice = self.window.folders()[index]
            if path == choice:
                show(self.window, path, goto=goto, single_pane=single_pane, other_group=other_group)
            else:
                show(self.window, choice, single_pane=single_pane, other_group=other_group)

    def _show(self, path):
        show(self.window, path)

    def _determine_path(self):
        '''Return (path, fname) so goto=fname to set cursor'''
        # Use the current view's directory if it has one.
        view = self.window.active_view()
        path = view and view.file_name()
        if path:
            return os.path.split(path)

        # Use the first project folder if there is one.
        data = self.window.project_data() if ST3 else None
        if data and 'folders' in data:
            folders = data['folders']
            if folders:
                return (folders[0]['path'], None)

        # Use window folder if possible
        folders = self.window.folders()
        if len(folders) > 0:
            return (folders[0], None)

        # Use the user's home directory.
        return (os.path.expanduser('~'), None)

    def display_path(self, folder):
        display = folder
        home = os.path.expanduser("~")
        if folder.startswith(home):
            display = folder.replace(home, "~", 1)
        return display


class DiredRefreshCommand(TextCommand, DiredBaseCommand):
    """
    Populates or repopulates a dired view.

    self.index is a representation of view lines
               list contains full path of each item in a view, except
               header ['', ''] and parent_dir [PARENT_SYM]
    self.index shall be updated according to view modifications (refresh, expand single folder, fold)
                    and stored in view settings as 'dired_index'

    The main reason for index is access speed to item path because we can
        self.index[self.view.rowcol(region.a)[0]]
    to get full path, instead of grinding with substr thru entire view
    substr is slow: https://github.com/SublimeTextIssues/Core/issues/882
    """
    def run(self, edit, goto=None, to_expand=None, toggle=None, reset_sels=None):
        """
        goto
            Optional filename to put the cursor on; used only from "dired_up"

        to_expand
            List of relative paths for direcories which shall be expanded

        toggle
            If true, marked/selected directories shall switch state,
            i.e. expand/collapse

        reset_sels
            If True, previous selections & marks shan’t be restored
        """
        # after restart ST, callback seems to disappear, so reset callback on each refresh for more reliability
        self.view.settings().clear_on_change('color_scheme')
        self.view.settings().add_on_change('color_scheme', lambda: set_proper_scheme(self.view))

        path = self.path
        expanded = self.view.find_all(u'^\s*▾') if not goto else []
        names = []
        if reset_sels:
            self.index, self.marked, self.sels = None, None, None
        else:
            self.index  = self.get_all()
            self.marked = self.get_marked()
            self.sels   = (self.get_selected(), list(self.view.sel()))
        self.show_hidden = self.view.settings().get('dired_show_hidden_files', True)

        if path == 'ThisPC\\':
            path, names = '', self.get_disks()
        if not reset_sels:
            self.re_populate_view(edit, path, names, expanded, to_expand, toggle)
        else:
            self.index = []
            self.populate_view(edit, path, names, goto)

    def re_populate_view(self, edit, path, names, expanded, to_expand, toggle):
        root = path
        for i, r in enumerate(expanded):
            name = self.get_fullpath_for(r)
            expanded[i] = name
        if toggle and to_expand:
            merged = list(set(expanded + to_expand))
            expanded = [e for e in merged if not (e in expanded and e in to_expand)]
        else:
            expanded.extend(to_expand or [])
        # we need prev index to setup expanded list — done, so reset index
        self.index = []

        tree = self.traverse_tree(root, root, '', names, expanded)
        if not tree:
            return self.populate_view(edit, path, names, goto=None)

        self.set_status()
        items = self.correcting_index(path, tree)
        self.write(edit, items)
        self.restore_selections()
        self.view.run_command('dired_call_vcs', {'path': path})

    def populate_view(self, edit, path, names, goto):
        if not path and names:  # open ThisPC
            self.continue_populate(edit, path, names, goto)
            return
        items, error = self.try_listing_directory(path)
        if error:
            self.view.run_command("dired_up")
            self.view.set_read_only(False)
            self.view.insert(edit, self.view.line(self.view.sel()[0]).b,
                             u'\t<%s>' % error)
            self.view.set_read_only(True)
        else:
            self.continue_populate(edit, path, items, goto)

    def continue_populate(self, edit, path, names, goto=None):
        self.sel = None
        self.number_line = 0
        self.set_status()
        items = self.correcting_index(path, self.prepare_filelist(names, path, '', ''))
        self.write(edit, items)
        self.restore_selections(goto, path)
        self.view.run_command('dired_call_vcs', {'path': path})

    def traverse_tree(self, root, path, indent, tree, expanded):
        if not path:  # special case for ThisPC, path is empty string
            items = [u'%s\\' % d for d in tree]
            tree  = []

        else:
            if indent:  # this happens during recursive call, i.e. path in expanded
                # basename return funny results for c:\\ so it is tricky
                bname = os.path.basename(os.path.abspath(path)) or path.rstrip(os.sep)
                tree.append(u'%s▾ %s%s' % (indent[:-1], bname.rstrip(os.sep), os.sep))
                self.index.append(u'%s' % path)

            items, error = self.try_listing_directory(path)
            if error:
                tree[~0] += u'\t<%s>' % error
                return
            if not items:  # expanding empty folder, so notify that it is empty
                tree[~0] += '\t<empty>'
                return

        files = []
        index_files = []
        for f in items:
            new_path = join(path, f)
            dir_path = u'%s%s' % (new_path.rstrip(os.sep), os.sep)
            check = isdir(new_path)
            if check and dir_path in expanded:
                self.traverse_tree(root, dir_path, indent + '\t', tree, expanded)
            elif check:
                self.index.append(dir_path)
                tree.append(u'%s▸ %s%s' % (indent, f.rstrip(os.sep), os.sep))
            else:
                index_files.append(new_path)
                files.append(u'%s≡ %s' % (indent, f))

        self.index += index_files
        tree += files
        return tree

    def set_title(self, path):
        header  = self.view.settings().get('dired_header', False)
        name    = jump_names().get(path or self.path)
        caption = u"{0} → {1}".format(name, path) if name else path or self.path
        text    = [caption, len(caption)*(u'—')] if header else []
        icon    = self.view.name()[:2]
        if not path:
            title = u'%s%s' % (icon, name or 'This PC')
        else:
            norm_path = path.rstrip(os.sep)
            if self.view.settings().get('dired_show_full_path', False):
                title = u'%s%s (%s)' % (icon, name or basename(norm_path), norm_path)
            else:
                title = u'%s%s' % (icon, name or basename(norm_path))
        self.view.set_name(title)
        return (text, header)

    def write(self, edit, fileslist):
        '''apply changes to view'''
        self.view.set_read_only(False)
        self.view.erase(edit, Region(0, self.view.size()))
        self.view.insert(edit, 0, '\n'.join(fileslist))
        self.view.set_read_only(True)

        fileregion = self.fileregion()
        count = len(self.view.lines(fileregion)) if fileregion else 0
        self.view.settings().set('dired_count', count)
        self.view.settings().set('dired_index', self.index)

    def correcting_index(self, path, fileslist):
        text, header = self.set_title(path)
        if path and (not fileslist or self.show_parent()):
            text.append(PARENT_SYM)
            self.index = [PARENT_SYM] + self.index
            self.number_line += 1
        if header:
            self.index = ['', ''] + self.index
            self.number_line += 2
        return text + fileslist

    def restore_selections(self, goto=None, path=None):
        self.restore_marks(self.marked)
        if goto:
            if goto[~0] != os.sep:
                goto += (os.sep if isdir(join(path, goto)) else '')
            self.sels = ([goto], None)
        self.restore_sels(self.sels)

    def get_disks(self):
        names = []
        for s in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            disk = '%s:' % s
            if isdir(disk):
                names.append(disk)
        return names


# NAVIGATION #####################################################

class DiredNextLineCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, forward=None):
        self.move(forward)


class DiredMoveCommand(TextCommand, DiredBaseCommand):
    def run(self, edit, to="bof"):
        self.move_to_extreme(to)


class DiredSelect(TextCommand, DiredBaseCommand):
    def run(self, edit, new_view=0, other_group=0, preview=0, and_close=0):
        path = self.path
        self.index = self.get_all()
        filenames = self.get_selected() if not new_view else self.get_marked() or self.get_selected()

        # If reuse view is turned on and the only item is a directory, refresh the existing view.
        if not new_view and reuse_view():
            fqn = join(path, filenames[0])
            if len(filenames) == 1 and isdir(fqn):
                show(self.view.window(), fqn, view_id=self.view.id())
                return
            elif len(filenames) == 1 and filenames[0] == PARENT_SYM:
                self.view.window().run_command("dired_up")
                return

        w = self.view.window()
        if other_group or preview or and_close:
            dired_view = self.view
            nag = self.view.window().active_group()
            if not and_close:
                target_group = self._other_group(w, nag)
                # set_view_index and focus are not very reliable
                # just focus target_group should do what we want
                w.focus_group(target_group)

        for filename in filenames:
            fqn = join(path, filename)
            if exists(fqn):  # ignore 'item <error>'
                if isdir(fqn):
                    show(w, fqn, ignore_existing=new_view)
                else:
                    if preview:
                        w.open_file(fqn, sublime.TRANSIENT)
                        w.focus_view(dired_view)
                        return  # preview is possible for a single file only
                    else:
                        v = w.open_file(fqn)
        if and_close:
            w.focus_view(dired_view)
            w.run_command("close")
            w.focus_view(v)

    def _other_group(self, w, nag):
        '''
        creates new group if need and return index of the group where files
        shall be opened
        '''
        groups = w.num_groups()
        if groups == 1:
            width = calc_width(self.view)
            w.set_layout({"cols": [0.0, width, 1.0], "rows": [0.0, 1.0], "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]})
        if groups <= 4 and nag < 2:
            group = 1 if nag == 0 else 0
        elif groups == 4 and nag >= 2:
            group = 3 if nag == 2 else 2
        else:
            group = nag - 1
        return group


class DiredExpand(TextCommand, DiredBaseCommand):
    def run(self, edit, toggle=False):
        self.index = self.get_all()
        filenames = self.get_marked(full=True) or self.get_selected(parent=False, full=True)

        if len(filenames) == 1 and filenames[0][~0] == os.sep:
            return self.expand_single_folder(edit, filenames[0], toggle)
        elif filenames:
            # working with several selections at once is very tricky, thus for reliability we should
            # recreate the entire tree, despite it is supposedly slower, but not really, because
            # one view.replace/insert() call is faster than multiple ones
            self.view.run_command('dired_refresh', {'to_expand': filenames, 'toggle': toggle})
            return
        else:
            return sublime.status_message('Item cannot be expanded')

    def expand_single_folder(self, edit, filename, toggle):
        marked = self.get_marked()
        seled  = self.get_selected()

        if toggle and self.try_to_fold(marked):
            return

        self.view.run_command('dired_fold', {'update': True, 'index': self.index})
        self.index = self.get_all()  # fold changed index, get a new one

        self.show_hidden = self.view.settings().get('dired_show_hidden_files', True)
        self.sel = self.view.get_regions('marked')[0] if marked else list(self.view.sel())[0]
        line     = self.view.line(self.sel)

        # number of next line to make slicing work properly
        self.number_line = 1 + self.view.rowcol(line.a)[0]
        # line may have inline error msg after os.sep
        root = self.view.substr(line).split(os.sep)[0].replace(u'▸', u'▾', 1) + os.sep

        items, error = self.try_listing_directory(filename)
        if error:
            replacement = [u'%s\t<%s>' % (root, error)]
        elif items:
            replacement = [root] + self.prepare_filelist(items, '', filename, '\t')
            dired_count = self.view.settings().get('dired_count', 0)
            self.view.settings().set('dired_count', dired_count + len(items))
        else:  # expanding empty folder, so notify that it is empty
            replacement = [u'%s\t<empty>' % root]

        self.view.set_read_only(False)
        self.view.replace(edit, line, '\n'.join(replacement))
        self.view.set_read_only(True)

        self.view.settings().set('dired_index', self.index)
        self.restore_marks(marked)
        self.restore_sels((seled, [self.sel]))
        self.view.run_command('dired_call_vcs', {'path': self.path})

    def try_to_fold(self, marked):
        line = self.view.line(self.view.get_regions('marked')[0] if marked else
                              list(self.view.sel())[0])
        content = self.view.substr(line).lstrip()[0]
        if content == u'▾':
            self.view.run_command('dired_fold')
            return True
        else:
            return False


class DiredFold(TextCommand, DiredBaseCommand):
    u'''
    This command used to fold/erase/shrink (whatever you like to call it) content
    of some [sub]directory (within current directory, see self.path).
    There are two cases when this command would be fired:
        1. User mean to fold   (key ←)
        2. User mean to unfold (key →)
    In first case we just erase region and set dired_count; however, we need to
    figure out which region to erase:
        (a) if cursor placed on directory item and next line indented
            (representing content of the directory) — erase indented line;
        (b) next line is not indented, but the line of directory item is indented —
            erase directory item itself;
        (c) cursor placed on file item which is indented — erase file item.
    In second case we need to decide if erasing needed or not:
        (a) if directory was unfolded (as in 1.a) — erase that region, so then
            it’ll be filled (basically it is like update/refresh), also set dired_count;
        (b) directory was folded (as in 1.b) — do nothing
    '''
    def run(self, edit, update=None, index=None):
        v = self.view
        self.index  = index or self.get_all()
        self.marked = None
        self.seled  = (self.get_selected(), list(self.view.sel()))
        marks       = self.view.get_regions('marked')
        virt_sels   = []

        if marks:
            for m in marks:
                if 'directory' in self.view.scope_name(m.a):
                    virt_sels.append(Region(m.a, m.a))
            self.marked = self.get_marked()
        sels = virt_sels

        lines = [v.line(s.a) for s in reversed(sels or list(v.sel()))]
        for line in lines:
            self.fold(edit, v, line, update)
        if self.marked:
            self.restore_marks(self.marked)
        if self.seled:
            self.restore_sels(self.seled)

    def fold(self, edit, v, line, update):
        current_region = v.indented_region(line.b)
        next_region    = v.indented_region(line.b + 2)
        is_folder      = 'directory' in v.scope_name(line.a)
        folded_subfolder = update and (next_region.contains(line) or next_region.empty() or next_region.contains(current_region))
        folded_folder    = update and current_region.empty() and next_region.empty()
        file_item_in_root = not is_folder and current_region.empty()

        if 'error' in v.scope_name(line.b - 1):
            # remove inline errors, e.g. <empty>
            indented_region = v.extract_scope(line.b - 1)
        elif folded_subfolder or folded_folder or file_item_in_root:
            return  # folding is not supposed to happen, so we exit
        elif update or (is_folder and not next_region.empty() and not next_region.contains(line)):
            indented_region = next_region
        elif not current_region.empty():
            indented_region = current_region
            line = v.line(indented_region.a - 2)
        else:
            return  # this is not supposed to happen, but it does sometimes
        name_point  = v.extract_scope(line.a).b
        if 'name' in v.scope_name(name_point):
            icon_region = Region(name_point - 2, name_point - 1)
        else:
            icon_region = Region(line.a, line.a + 1)

        # do not set count & index on empty folder
        if not line.contains(indented_region):
            dired_count = v.settings().get('dired_count', 0)
            v.settings().set('dired_count', int(dired_count) - len(v.lines(indented_region)))
            if indented_region.b == v.size():
                # MUST avoid new line at eof
                indented_region = Region(indented_region.a - 1, indented_region.b)

            line_number = 1 + v.rowcol(line.a)[0]
            removed_lines = line_number + len(v.lines(indented_region))
            self.index = self.index[:line_number] + self.index[removed_lines:]
            v.settings().set('dired_index', self.index)

        if self.marked or self.seled:
            path = self.path
            folded_name = self.get_parent(line, path)
            if self.marked:
                self.marked.append(folded_name)
            elif self.seled:
                self.seled[0].append(folded_name)

        v.set_read_only(False)
        v.replace(edit, icon_region, u'▸')
        v.erase(edit, indented_region)
        v.set_read_only(True)


class DiredUpCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        path = self.path
        parent = dirname(path.rstrip(os.sep))
        if parent != os.sep and parent[1:] != ':\\':
            # need to avoid c:\\\\
            parent += os.sep
        if parent == path and NT:
            parent = 'ThisPC'
        elif parent == path:
            return
        elif path == 'ThisPC\\':
            self.view.run_command('dired_refresh')
            return

        view_id = (self.view.id() if reuse_view() else None)
        goto = basename(path.rstrip(os.sep)) or path
        show(self.view.window(), parent, view_id, goto=goto)


class DiredGotoCommand(TextCommand, DiredBaseCommand):
    """
    Prompt for a new directory.
    """
    def run(self, edit):
        prompt.start('Goto:', self.view.window(), self.path, self.goto)

    def goto(self, path):
        show(self.view.window(), path, view_id=self.view.id())


# MARKING ###########################################################

class DiredMarkExtensionCommand(TextCommand, DiredBaseCommand):
    def run(self, edit):
        filergn = self.fileregion()
        if filergn.empty():
            return
        current_item = self.view.substr(self.view.line(self.view.sel()[0].a))
        if current_item.endswith(os.sep) or current_item == PARENT_SYM:
            ext = ''
        else:
            ext = current_item.split('.')[-1]
        pv = self.view.window().show_input_panel('Extension:', ext, self.on_done, None, None)
        pv.run_command("select_all")

    def on_done(self, ext):
        ext = ext.strip()
        if not ext:
            return
        if not ext.startswith('.'):
            ext = '.' + ext
        self._mark(mark=lambda oldmark, filename: filename.endswith(ext) or oldmark,
                   regions=[self.fileregion()])


class DiredMarkCommand(TextCommand, DiredBaseCommand):
    """
    Marks or unmarks files.

    The mark can be set to '*' to mark a file, ' ' to unmark a file,  or 't' to toggle the
    mark.

    By default only selected files are marked, but if markall is True all files are
    marked/unmarked and the selection is ignored.

    If there is no selection and mark is '*', the cursor is moved to the next line so
    successive files can be marked by repeating the mark key binding (e.g. 'm').
    """
    def run(self, edit, mark=True, markall=False, forward=True):
        assert mark in (True, False, 'toggle')

        filergn = self.fileregion()
        if filergn.empty():
            return

        if not mark and markall:
            self.view.erase_regions('marked')
            return

        # If markall is set, mark/unmark all files.  Otherwise only those that are selected.
        regions = [filergn] if markall else list(self.view.sel())

        if mark == 'toggle':
            mark = lambda oldmark, filename: not oldmark

        self._mark(mark=mark, regions=regions)

        # If there is no selection, move the cursor forward so the user can keep pressing 'm'
        # to mark successive files.
        if not markall and len(self.view.sel()) == 1 and self.view.sel()[0].empty():
            self.move(forward)


# OTHER #############################################################

class DiredToggleHiddenFilesCommand(TextCommand):
    def run(self, edit):
        show = self.view.settings().get('dired_show_hidden_files', True)
        self.view.settings().set('dired_show_hidden_files', not show)
        self.view.run_command('dired_refresh')


# MOUSE INTERATIONS #################################################

def dired_mouse_arguments(sel):
    if 'directory' in sel:
        return ("dired_expand", {"toggle": True})
    else:
        return ("dired_select", {"other_group": True})

if ST3:
    class DiredDoubleclickCommand(TextCommand, DiredBaseCommand):
        def run_(self, view, args):
            s = self.view.settings()
            if s.get("dired_path") and not s.get("dired_rename_mode"):
                self.view.run_command(*dired_mouse_arguments(self.view.scope_name(self.view.sel()[0].a)))
            else:
                system_command = args["command"] if "command" in args else None
                if system_command:
                    system_args = dict({"event": args["event"]}.items())
                    system_args.update(dict(args["args"].items()))
                    self.view.run_command(system_command, system_args)
else:
    class DiredDoubleclickCommand(TextCommand, DiredBaseCommand):
        def run_(self, args):
            s = self.view.settings()
            if s.get("dired_path") and not s.get("dired_rename_mode"):
                self.view.run_command(*dired_mouse_arguments(self.view.scope_name(self.view.sel()[0].a)))
            else:
                system_command = args["command"] if "command" in args else None
                if system_command:
                    system_args = dict({"event": args["event"]}.items())
                    system_args.update(dict(args["args"].items()))
                    self.view.run_command(system_command, system_args)
