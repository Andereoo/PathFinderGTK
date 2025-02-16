import sys, os, subprocess, shutil
from filecmp import dircmp
import difflib, mimetypes
import threading
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version("GtkSource", "5")
from gi.repository import Gio, Gtk, Adw, Gdk, GObject, GLib, GtkSource, GdkPixbuf, Pango

CHANGE_UNCHANGED = ("", "file-change-none", "file-change-none")
CHANGE_CHANGED = ("Changed", "accent", "accent-button")
CHANGE_LEFT = ("Not backed up", "warning", "warning-button")
CHANGE_RIGHT = ("Only in backup", "missing", "missing-button")
CHANGE_UNKNOWN = ("", "file-change-unknown", "file-change-unknown")
SCHEMA_ID = "ca.andereoo.pathfindergtk"
SCHEMA_PATH = "/usr/share/glib-2.0/schemas/"
UPDATE_SCHEMA = False

APPLICATION_PATH = os.path.abspath(os.path.dirname(__file__))
LICENSE = Gtk.License.GPL_3_0
VERSION = "2.0.0"
NAME = "Pathfinder"
DESCRIPTION = "Compare folders"
AUTHOR = "Andereoo"
ICON = "logviewer"

gio_settings = Gio.Settings.new(SCHEMA_ID)
gtk_settings = Gtk.Settings.get_default()

#TO BE RUN BY INSTALLER
if UPDATE_SCHEMA:
    print("Updating schema...")
    print(subprocess.run(["sudo", "cp", os.path.join(APPLICATION_PATH, SCHEMA_ID+".gschema.xml"), SCHEMA_PATH], 
                        stdout = subprocess.PIPE,
                        stderr = subprocess.PIPE,
                        universal_newlines = True
                        ).stderr)
    print(subprocess.run(["sudo", "glib-compile-schemas", SCHEMA_PATH],
                        stdout = subprocess.PIPE,
                        stderr = subprocess.PIPE,
                        universal_newlines = True
                        ).stderr)
    print("Done!")
    print("Please re-run this script with UPDATE_SCHEMA=False to run the application")
    quit() #Otherwise a breakpoint trap will occur later; file needs to be re-run

def open_file(file):
    subprocess.call(('open', file))

def show_file(file):
    subprocess.call(('nautilus', '--select', file))

class CustomFolderSelector(Gtk.Button):
    def __init__(self, master, settings_key, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.settings_key = settings_key
        self.folder = gio_settings.get_string(settings_key)

        self.button_content = button_content = Adw.ButtonContent()
        self.dialog = dialog = Gtk.FileDialog(title="Choose directory")

        button_content.set_icon_name("document-open-symbolic")
        button_content.set_label("Open")

        self.set_child(button_content)        

        self.connect('clicked', lambda event: dialog.select_folder(master, None, self.on_navigator_response))

        dnd = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        dnd.set_preload(True)
        dnd.connect('drop', self.on_dnd_drop)
        #dnd.connect('accept', self.on_dnd_accept)
        dnd.connect("notify::value", self.on_value_found)
        self.add_controller(dnd)

        self.current_drop_value = None

        self.update_label()

    def on_value_found(self, drop_target, value):
        value = drop_target.get_value()
        if value:
            for gfile in value.get_files():
                uri = gfile.get_path()
                if os.path.isdir(uri):
                    self.current_drop_value = uri
                    drop_target.reset()
                else:
                    self.current_drop_value = None
                    drop_target.reject()      
        else:
            self.current_drop_value = None
    
    def on_dnd_accept(self, drop_target, user_data):
        return False

    def on_dnd_drop(self, drop_target, value, x, y):
        if self.current_drop_value:
            self.folder = self.current_drop_value
            gio_settings.set_string(self.settings_key, self.current_drop_value)
            self.update_label()

    def update_label(self):
        if self.folder:
            path = list(Path(self.folder).parts)

            if path[0] == os.sep:
                path[0:2] = [''.join(path[0:2])]
            if len(path) <= 3:
                label = self.folder
            elif len(path) > 3:
                label = f"{path[0]}{os.sep}...{os.sep}{path[-1]}"

            self.button_content.set_label(label)

    def on_navigator_response(self, event, response):
        if not response.had_error():
            file = event.select_folder_finish(response)
            self.folder = folder = file.get_path()
            gio_settings.set_string(self.settings_key, folder)
            self.update_label()

class FileMenu(Gtk.PopoverMenu):
    def __init__(self, *args, halign=Gtk.Align.START, **kwargs):
        super().__init__(*args, halign=halign, **kwargs)
        self.menu = menu = Gio.Menu()
        menu.append("Open", "win.open_file")
        menu.append("Show in folder", "win.show_file")
        self.set_has_arrow(False)
        self.set_menu_model(menu)
        self.set_offset(2, 2)
    
    def add_resolve(self):
        self.menu.append("Resolve changes", "win.resolve_file")

class FileBrowser(Gtk.ScrolledWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_vexpand(True)

        self.store = store = Gio.ListStore.new(DataObject)
        model = Gtk.TreeListModel.new(store, False, False, self.add_tree_node)
        self.selection = selection = Gtk.SingleSelection.new(model)#MultiSelection
        factory = Gtk.SignalListItemFactory()
        self.view = view = Gtk.ListView.new(selection, factory) #Columnview
        
        model.set_autoexpand(True) #GtkTreeListRow.set_expanded     
        factory.connect("setup", self.setup_list_item)
        factory.connect("bind", self.bind_list_item) 

        self.set_child(view)

        view.add_css_class("navigation-sidebar") #rich-list
        #view.set_tab_behavior(Gtk.ListTabBehavior.LIST_TAB_ALL)

    def remove_item(self, item):
        found = item._store.find(item)
        item._store.remove(found.position)
        item._is_visible = False
    
    def on_right_click(self, controller, n_press, x, y):
        #self.selection.set_selected(controller._widget.get_position())
        #controller._menu.set_offset(x, y)
        self.right_clicked_item = controller.get_widget().get_item()

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        controller._menu.set_pointing_to(rect)
        if not controller._menu.get_parent():
            if self.right_clicked_item.change != CHANGE_UNKNOWN:
                controller._menu.add_resolve()
            controller._menu.set_parent(controller.get_widget()) #ideally this should be done in setup_list_item, but for some reason that messes up item indenting
        controller._menu.popup()

    def add_tree_node(self, item):
        if not (item):
                return None
        else:        
            if type(item) == Gtk.TreeListRow:
                item = item.get_item()

            if not item._store:
                item._store = self.store

            if item.children:
                store = Gio.ListStore.new(DataObject)
                for child in item.children:
                    if child._is_visible:
                        store.append(child)
                        child._store = store
                return store
            else:
                return None

    def setup_list_item(self, widget, item):
        file_name = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE)
        file_change = Gtk.Label()
        file_icon = Gtk.Image()
        file_name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.FILL)
        file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.FILL)
        expander = Gtk.TreeExpander()
        right_align = Gtk.Box(hexpand=True, halign=Gtk.Align.END)

        click = Gtk.GestureClick.new()
        click.set_button(3)
        click._menu = FileMenu()
        click.connect("released", self.on_right_click)
        expander.add_controller(click)

        #################
        self.ev_drag = ev_drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)#ASK)
        ev_drag.connect('prepare', self.on_drag_prepare)
        ev_drag.connect('drag_begin', lambda event, _drag, file_box=file_name_box: self.on_drag_begin(event, _drag, file_box))
        expander.add_controller(ev_drag)
        #self.icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        #self.drag_icon = None
        #################

        expander.set_child(file_box)
        file_name_box.append(file_icon)
        file_name_box.append(file_name)
        file_box.append(file_name_box)
        file_box.append(right_align)
        right_align.append(file_change)
        item.set_child(expander)

        file_box.file_name = file_name
        file_box.file_change = file_change
        file_box.file_icon = file_icon

    def bind_list_item(self, widget, item):
        expander = item.get_child()
        file_box = expander.get_child()
        row = item.get_item()
        expander.set_list_row(row)
        obj = row.get_item()

        file_box.file_name.set_label(obj.name)
        file_box.file_change.set_label(obj.change[0])
        file_box.file_icon.set_from_icon_name(obj.icon)
        file_box.file_name.add_css_class(obj.change[1])
        file_box.file_change.add_css_class(obj.change[2])

    def on_drag_prepare(self, event, x, y):
        widget = event.get_widget()
        gfile = Gio.File.new_for_path(widget.get_item().path)
        content = Gdk.FileList.new_from_list([gfile])
        return Gdk.ContentProvider.new_for_value(content)

    def on_drag_begin(self, event, _drag, file_box):
        """widget = event.get_widget()
        icon = widget.get_item().icon
        self.drag_icon = self.icon_theme.lookup_icon(icon, None, 64, 1, Gtk.TextDirection.RTL, Gtk.IconLookupFlags.PRELOAD)
        #img = Gtk.Image.new_from_paintable(self.drag_file_icon)
        #file_box.append(img)
        self.ev_drag.set_icon(self.drag_icon, 0, 0)"""
        widget = event.get_widget()
        icon = widget.get_item().icon
        icon = Gtk.WidgetPaintable.new(file_box)#event.get_widget())
        event.set_icon(icon, 0, 0)


class FileViewer(Gtk.ScrolledWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.style_scheme = None
        self.display_stte = 0
        self.top_image_shown = False
        self.bottom_image_shown = False
        self.image_formats = self.get_supported_image_formats()
        self.open_files = []

        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        self.language_manager = language_manager = GtkSource.LanguageManager()
        self.buffer = buffer = GtkSource.Buffer()
        self.file = GtkSource.File()
        self.style_manager = GtkSource.StyleSchemeManager.get_default()
        self.code_view = code_view = GtkSource.View.new_with_buffer(buffer)

        buffer.set_highlight_syntax(True)
        code_view.set_show_line_numbers(True)
        code_view.set_wrap_mode(Gtk.WrapMode.WORD)#NONE)

        self.image_view = image_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, spacing=10)
        self.top_image_box = top_image_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.FILL, halign=Gtk.Align.CENTER, vexpand=True)
        self.bottom_image_box = bottom_image_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.FILL, halign=Gtk.Align.CENTER, vexpand=True)
        self.top_image = top_image = Gtk.Picture(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        self.bottom_image = bottom_image = Gtk.Picture(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)

        image_view.add_css_class("picture-container")
        top_image.add_css_class("picture-top")
        bottom_image.add_css_class("picture-bottom")
        
        top_image_box.append(top_image)
        bottom_image_box.append(bottom_image)

        self.useless_view = useless_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, vexpand=True, spacing=10)
        open_label_main = Gtk.Label(label="Cannot display file")
        open_label_sub = Gtk.Label(label="Open the file instead to view it")
        open_button = Gtk.Button(halign=Gtk.Align.CENTER)
        open_button_content = Adw.ButtonContent()
        open_button_content.set_icon_name("document-new-symbolic")
        open_button_content.set_label("Open file")
        open_label_main.add_css_class("title-1")
        open_label_sub.add_css_class("body")
        open_button.set_margin_top(10)

        open_button.set_child(open_button_content)        
        useless_view.append(open_label_main)
        useless_view.append(open_label_sub)
        useless_view.append(open_button)

        open_button.connect('clicked', self.open_file_system)

        self.addition = buffer.create_tag("addition")
        self.removal = buffer.create_tag("removal")
        self.on_theme_changed(gtk_settings, None)

        gtk_settings.connect("notify::gtk-application-prefer-dark-theme", self.on_theme_changed)
    
    def open_file_system(self, *args):
        for file in self.open_files:
            if file:
                open_file(file)
    
    def get_supported_image_formats(self):
        supported_formats = GdkPixbuf.Pixbuf.get_formats()
        supported_mime_types = [mime_type for format in supported_formats for mime_type in format.get_mime_types()]
        return supported_mime_types

    def on_theme_changed(self, settings, pspec):
        if settings.get_property("gtk-application-prefer-dark-theme"):
            new_style_scheme = self.style_manager.get_scheme("Adwaita-dark")
            self.addition.set_property("background", "#244a2d")
            self.removal.set_property("background", "#54001f")
        else:
            new_style_scheme = self.style_manager.get_scheme("Adwaita")
            self.addition.set_property("background", "#ccebd3")
            self.removal.set_property("background", "#ffdedf")
        
        if new_style_scheme != self.style_scheme:
            self.style_scheme = new_style_scheme
            self.buffer.set_style_scheme(self.style_scheme)

    def manage_image_view(self, path1, path2):
        if path1 and path2:
            if not self.top_image_shown:
                self.image_view.append(self.top_image_box)
                self.top_image_shown = True
            if not self.bottom_image_shown:
                self.image_view.append(self.bottom_image_box)
                self.bottom_image_shown = True
        elif path1:
            if not self.top_image_shown:
                self.image_view.append(self.top_image_box)
                self.top_image_shown = True
            if self.bottom_image_shown:
                self.image_view.remove(self.bottom_image_box)
                self.bottom_image_shown = False
        elif path2:
            if self.top_image_shown:
                self.image_view.remove(self.top_image_box)
                self.top_image_shown = False
            if not self.bottom_image_shown:
                self.image_view.append(self.bottom_image_box)
                self.bottom_image_shown = True

    def show_diff(self, path1, path2):
        self.open_files = [path1, path2]
        try:
            if path1:
                mime_type, encoding = mimetypes.guess_type(path1)
            else:
                mime_type, encoding = mimetypes.guess_type(path2)
            if not mime_type: mime_type = "text/unknown"

            if mime_type in self.image_formats:
                if self.get_child() != self.image_view:
                    self.set_child(self.image_view)
                if path1:
                    self.top_image.set_filename(path1)
                if path2:
                    self.bottom_image.set_filename(path2)
                self.manage_image_view(path1, path2)
                
            elif mime_type.startswith("text/"):
                if self.get_child() != self.code_view:
                    self.set_child(self.code_view)

                if path1 and path2:
                    language = self.language_manager.guess_language(path1, None)
                    self.buffer.set_language(language)

                    with open(path1, 'r') as file:
                        content1 = file.read()
                    with open(path2, 'r') as file:
                        content2 = file.read()
                    diff = difflib.Differ().compare(content1.splitlines(), content2.splitlines())

                    self.buffer.set_text('')

                    for line in diff:
                        if line.startswith('+'):
                            self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), line + '\n', "addition")
                        elif line.startswith('-'):
                            self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), line + '\n', "removal")
                        else:
                            self.buffer.insert(self.buffer.get_end_iter(), line + '\n')
                elif path1:
                    language = self.language_manager.guess_language(path1, None)
                    self.buffer.set_language(language)

                    with open(path1, 'r') as file:
                        content1 = file.read()

                    self.buffer.set_text('')

                    self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), content1, "addition")
                elif path2:
                    language = self.language_manager.guess_language(path2, None)
                    self.buffer.set_language(language)

                    with open(path2, 'r') as file:
                        content2 = file.read()

                    self.buffer.set_text('')

                    self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), content2, "removal")
            else:
                if self.get_child() != self.useless_view:
                    self.set_child(self.useless_view)
        except UnicodeDecodeError:
            if self.get_child() != self.useless_view:
                self.set_child(self.useless_view)
            
class DataObject(GObject.GObject):
    def __init__(self, name: str, path:str, icon: str, children=None, change="", alternate_path=None):
        super(DataObject, self).__init__()

        self.name = name
        self.path = path
        self.alternate_path = alternate_path
        self.icon = icon
        self.children = children
        self.change = change

        self._store = None
        self._is_visible = True

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application, *args, **kwargs):
        super().__init__(*args, application=application, **kwargs)

        self.application = application
        titlebar = self.create_title_bar()

        self.no_results = True
        self.sidebar_should_show = False
        self.searching = False

        self.set_icon_name(ICON)

        self.master_container = master_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        header_bar = self.create_header_bar()
        self.content_pane = content_pane = self.create_content_pane()
        self.footer_bar = footer_bar = self.create_footer_bar()

        self.set_titlebar(titlebar)
        self.set_child(master_container)

        master_container.append(header_bar)
        master_container.append(content_pane)      

        #Eventually port this to GSchema        
        css_provider = Gtk.CssProvider()
        css_path = os.path.join(APPLICATION_PATH, "PathFinderGTK.css")
        css_provider.load_from_file(Gio.File.new_for_path(css_path))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        
        gio_settings.connect("changed::path-left", self.prepare_scan_auto)
        gio_settings.connect("changed::path-right", self.prepare_scan_auto)
        
        self.prepare_scan_auto()

        gio_settings.bind("default-width", self, "default-width", Gio.SettingsBindFlags.DEFAULT)
        gio_settings.bind("default-height", self, "default-height", Gio.SettingsBindFlags.DEFAULT)
        gio_settings.bind("is-maximized", self, "maximized", Gio.SettingsBindFlags.DEFAULT)

        """gio_settings.connect("changed::auto-update", self.on_auto_update_setting_change)
        def on_auto_update_setting_change(self, settings, stype):
        if settings.get_boolean(stype):
            #self.header_bar.remove(self.button)
            self.can_auto_update = True
        else:
            #self.header_bar.append(self.button)
            self.can_auto_update = False"""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval == Gdk.KEY_w or keyval == Gdk.KEY_q:
                self.close()

    def create_title_bar(self):
        title_bar = Adw.HeaderBar()
        toggle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)#, spacing=5)
        toggle1 = Gtk.ToggleButton(label="Directory tree")
        self.file_viewer_toggle = toggle2 = Gtk.ToggleButton(label="File viewer", group=toggle1)
        menu = Gio.Menu()
        button = Gtk.MenuButton()
        self.about_dialog = about = Adw.AboutWindow() #Gtk.AboutDialog() #

        menu.append("About Pathfinder", "win.about")
        menu.append("Quit", "win.quit")

        title_bar.set_title_widget(toggle_box)

        toggle_box.add_css_class("linked")
        button.set_menu_model(menu)
        button.set_icon_name("open-menu-symbolic")

        if gio_settings.get_boolean("view-files"):
            toggle2.set_active(True)
        else:
            toggle1.set_active(True)

        #toggle1.connect("toggled", self.hide_sidebar)
        toggle2.connect("toggled", self.show_sidebar)

        about.set_license_type(LICENSE)
        about.set_application_icon(ICON)
        about.set_version(VERSION)
        about.set_application_name(NAME)
        about.set_developer_name(AUTHOR)

        title_bar.pack_end(button)
        #title_bar.pack_end(toggle_box)
        toggle_box.append(toggle1)
        toggle_box.append(toggle2)

        about_action = Gio.SimpleAction.new("about", None)
        quit_action = Gio.SimpleAction.new("quit", None)

        about_action.connect("activate", self.open_about)
        quit_action.connect("activate", self.quit_app)

        self.add_action(about_action)
        self.add_action(quit_action)

        return title_bar

    def create_header_bar(self):
        header_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        button_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_bar.set_margin_top(5)
        header_bar.set_margin_start(5)
        header_bar.set_margin_end(5)
        button_bar.add_css_class("linked")

        scan_button = Gtk.Button(label="Scan")
        self.open_button1 = open_button1 = CustomFolderSelector(self, "path-left")
        self.open_button2 = open_button2 = CustomFolderSelector(self, "path-right")
    
        button_bar.append(open_button1)
        button_bar.append(open_button2)
        header_bar.append(button_bar)
        header_bar.append(scan_button)

        scan_button.connect('clicked', self.prepare_scan_manual)

        return header_bar
    
    def create_content_pane(self):
        content_pane = Gtk.Paned()
        self.file_browser = file_browser = FileBrowser()
        self.no_results_container = no_results_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, vexpand=True)
        self.no_results_page = no_results_page = Adw.StatusPage()
        self.code_viewer = code_viewer = FileViewer()

        gio_settings.bind("sidebar-width", content_pane, "position", Gio.SettingsBindFlags.DEFAULT)
        no_results_page.set_icon_name("search-symbolic")
        no_results_page.set_title("No changes to show")
        no_results_page.set_description("Press scan to search for changes")
        self.no_results_page_new = True

        self.invalid_action_container = invalid_action_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, vexpand=True)
        invalid_action_page = Adw.StatusPage()
        invalid_action_page.set_icon_name("error")
        invalid_action_page.set_title("Invalid selection")
        invalid_action_page.set_description("Try a different search")

        content_pane.set_start_child(no_results_container)
        no_results_container.append(no_results_page)
        invalid_action_container.append(invalid_action_page)

        file_browser.selection.connect("notify::selected", self.on_item_list_selected)    

        open_file = Gio.SimpleAction.new("open_file", None)
        show_file = Gio.SimpleAction.new("show_file", None)
        resolve_file = Gio.SimpleAction.new("resolve_file", None)
        
        open_file.connect("activate", self.open_file_system)
        show_file.connect("activate", self.show_file_system)
        resolve_file.connect("activate", self.resolve_file_change)

        self.add_action(open_file)
        self.add_action(show_file)
        self.add_action(resolve_file)
        

        return content_pane

    def open_file_system(self, *args):
        selected = self.file_browser.right_clicked_item
        if selected: 
            threading.Thread(target=lambda path=selected.path: open_file(path)).start()
            if selected.change == CHANGE_CHANGED or selected.change == CHANGE_UNKNOWN:
                threading.Thread(target=lambda path=selected.alternate_path: open_file(path)).start()    

    def show_file_system(self, *args):
        selected = self.file_browser.right_clicked_item
        if selected: 
            threading.Thread(target=lambda path=selected.path: show_file(path)).start()
            if selected.change == CHANGE_CHANGED or selected.change == CHANGE_UNKNOWN:
                threading.Thread(target=lambda path=selected.alternate_path: show_file(path)).start()  

    def on_response(self, dialog, response_id):
        dialog.close()
        if len(threading.enumerate()) == 1:
            self.master_container.append(self.footer_bar)
            self.spinner.start()
        thread = threading.Thread(target=lambda dialog=dialog, response_id=response_id: self.continue_response(dialog, response_id), daemon=True)
        thread.start()
        
    def continue_response(self, dialog, response_id):
        selected = self.file_browser.right_clicked_item
        prev = self.bottom_label.get_label()
        
        if response_id == Gtk.ResponseType.YES or response_id == 1:
            GLib.idle_add(lambda path=selected.path: self.bottom_label.set_label("Resolving " + path))
            self.file_browser.remove_item(selected)
            if os.path.isfile(selected.path):
                shutil.copy2(selected.path, os.path.dirname(selected.alternate_path))
            else:
                shutil.copytree(selected.path, selected.alternate_path)
        elif response_id == 2:
            GLib.idle_add(lambda path=selected.alternate_path: self.bottom_label.set_label("Resolving " + path))
            self.file_browser.remove_item(selected)
            if os.path.isfile(selected.alternate_path):
                shutil.copy2(selected.alternate_path, os.path.dirname(selected.path))
            else:
                shutil.copytree(selected.alternate_path, selected.path)

        if len(threading.enumerate()) == 2:
            self.master_container.remove(self.footer_bar)
            self.spinner.stop()
        if self.searching:
            GLib.idle_add(lambda prev=prev: self.bottom_label.set_label(prev))            

    def simplify_path(self, folder, path):
        if len(path) > 1:
            if folder == path[-2]:
                folder = folder + os.sep + path[-1]
            elif folder != path[-1]:
                folder = folder + os.sep + "..." + os.sep + path[-1]
        elif len(path) > 2:
            if folder != path[-1]:
                folder = folder + os.sep + "..." + os.sep + path[-1]
        return folder

    def resolve_file_change(self, *args):
        selected = self.file_browser.right_clicked_item
        """dialog = Gtk.AlertDialog() ###Need to wait for Ubuntu 24
        dialog.set_buttons(["Cancel", "Copy left", "Copy right"])
        dialog.set_message("Resolve changes")
        dialog.set_modal(True)
        dialog.set_detail(f"The file {selected.path} is not in the folder {alternate_path}")
        dialog.choose(self, None, self.delete_perform, "dsf")
        """      
        dialog = Gtk.MessageDialog(transient_for=self, modal=True)
        dialog.set_property("text", "Resolve changes")
        
        response = dialog.show()
        dialog.connect("response", self.on_response)
        if selected.change == CHANGE_CHANGED:
            alternate_path = os.path.dirname(selected.alternate_path)
            path = os.path.dirname(selected.path)
            dialog.set_property("secondary_text", f"The file {selected.name} has changed.")

            path1 = list(Path(selected.path).parts)[:-1]
            path2 = list(Path(selected.alternate_path).parts)[:-1]
            folder1 = path1[-1]
            folder2 = path2[-1]

            if folder1 == folder2:
                for num, folder in enumerate(path1):
                    if path2[num] != folder:
                        break
                folder1 = self.simplify_path(folder, path1)
                folder2 = self.simplify_path(path2[num], path2)

            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button(f"Copy to {folder1}", 2)
            dialog.add_button(f"Copy to {folder2}", 1)
        elif selected.change == CHANGE_LEFT:
            alternate_path = os.path.dirname(selected.alternate_path)
            dialog.set_property("secondary_text", f"The file {selected.path} is not in the folder {alternate_path}. Copy over?")
            dialog.add_button("No", Gtk.ResponseType.NO)
            dialog.add_button("Yes", Gtk.ResponseType.YES)
        elif selected.change == CHANGE_RIGHT:
            alternate_path = os.path.dirname(selected.path)
            alternate_path = os.path.dirname(selected.alternate_path)
            dialog.set_property("secondary_text", f"The file {selected.path} is not in the folder {alternate_path}. Copy over?")
            dialog.add_button("No", Gtk.ResponseType.NO)
            dialog.add_button("Yes", Gtk.ResponseType.YES)
        else:
            dialog.set_property("secondary_text", "Horray! No changes to show.")
            dialog.add_button("Ok", Gtk.ResponseType.OK)
    
    def create_footer_bar(self):
        footer_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.FILL, spacing=10)
        footer_bar_box = Gtk.Box(hexpand=True, halign=Gtk.Align.END, spacing=10)
        self.bottom_label = bottom_label = Gtk.Label()
        self.spinner = spinner = Gtk.Spinner()

        footer_bar.append(bottom_label)
        footer_bar.append(footer_bar_box)
        footer_bar_box.append(spinner)

        footer_bar.add_css_class("bottom_bar")

        return footer_bar
    
    def open_about(self, *args):
        self.about_dialog.present()
    
    def quit_app(self, *args):
        self.application.quit()

    def show_sidebar(self, button):
        if button.get_active():
            if self.sidebar_should_show:
                self.content_pane.set_end_child(self.code_viewer)
            gio_settings.set_boolean("view-files", True)
        else:
            self.content_pane.set_end_child(None)
            gio_settings.set_boolean("view-files", False)

    def on_item_list_selected(self, event, evt):
        selected = event.get_selected_item()
        if selected: 
            path = selected.get_item().path
            if os.path.isfile(path):
                if selected.get_item().change == CHANGE_CHANGED:
                    self.code_viewer.show_diff(path, selected.get_item().alternate_path)   
                elif selected.get_item().change == CHANGE_LEFT:               
                    self.code_viewer.show_diff(path, None)
                elif selected.get_item().change == CHANGE_RIGHT:               
                    self.code_viewer.show_diff(None, path)

                self.sidebar_should_show = True
                self.file_viewer_toggle.set_sensitive(True)
                if self.file_viewer_toggle.get_active():
                    self.content_pane.set_end_child(self.code_viewer)
            else:
                self.sidebar_should_show = False
                self.content_pane.set_end_child(None)
                self.file_viewer_toggle.set_sensitive(False)

    def get_thumbnail_name(self, file_path):
        file = Gio.File.new_for_path(file_path)
        name = file.query_info('standard::icon', 0).get_icon().get_names()[0]
        return name
    
    def finish_scan(self, change):
        if change:
            if self.no_results:
                self.content_pane.set_start_child(self.file_browser)
                self.no_results = False
        else:
            if self.no_results_page_new:
                self.no_results_page.set_description("Try a different search")
                self.no_results_page_new = False
            if not self.no_results:
                self.content_pane.set_start_child(self.no_results_container)
                self.no_results = True

        self.master_container.remove(self.footer_bar)
        self.spinner.stop()
        self.searching = False
    
    def finish_invalid_scan(self):
        self.content_pane.set_start_child(self.invalid_action_container)
        self.master_container.remove(self.footer_bar)
        self.spinner.stop()
        self.searching = False

    def begin_scanning(self):
        try:
            dcmp = dircmp(self.open_button1.folder, self.open_button2.folder)
            self.show_all = gio_settings.get_boolean("show-all")
            change = self.find_changes(dcmp) 
            GLib.idle_add(lambda x=change: self.finish_scan(x))
        except FileNotFoundError:
            GLib.idle_add(self.finish_invalid_scan)

    def add_found_item(self, name, root_path, tree_root, change, child=None, alternate_path=None):
        file_path = os.path.join(root_path, name)
        if alternate_path:
            alternate_path = os.path.join(alternate_path, name)
        pix = self.get_thumbnail_name(file_path)
        d2 = DataObject(name, file_path, pix, None, change, alternate_path)
        if child:
            child[tree_root].children.append(d2)
        else:
            GLib.idle_add(lambda x=d2: self.file_browser.store.append(x))

        if self.no_results:
            self.content_pane.set_start_child(self.file_browser)
            self.no_results = False

    def find_changes(self, dcmp, content=None):
        changed = False
        left = dcmp.left
        right = dcmp.right

        for name in dcmp.diff_files:
            self.add_found_item(name, left, left, CHANGE_CHANGED, content, right)
            changed = True
        for name in dcmp.left_only:
            self.add_found_item(name, left, left, CHANGE_LEFT, content, right)
            changed = True
        for name in dcmp.right_only:
            self.add_found_item(name, right, left, CHANGE_RIGHT, content, left)
            changed = True
        if self.show_all:
            for name in dcmp.same_files:
                self.add_found_item(name, left, left, CHANGE_UNCHANGED, content)
            changed = True
        
        for sub_dcmp in dcmp.subdirs.values():
            file_path = sub_dcmp.left
            GLib.idle_add(lambda y=file_path: self.bottom_label.set_label("Scanning "+y))
            pix = self.get_thumbnail_name(file_path)
            subdir_object = DataObject(os.path.basename(file_path), file_path, pix, [], CHANGE_UNKNOWN, sub_dcmp.right)

            if not content:
                content_new = {}                    
                content_new[file_path] = subdir_object
                cg = self.find_changes(sub_dcmp, content_new)
                if cg: 
                    changed = True
                    if self.no_results: #show the listview now so people don't see the no results page
                        self.content_pane.set_start_child(self.file_browser)
                    self.no_results = False
                    GLib.idle_add(lambda x=subdir_object: self.file_browser.store.append(x))
            else:
                content[file_path] = subdir_object
                cg = self.find_changes(sub_dcmp, content)
                if cg: 
                    changed = True
                    content[left].children.append(subdir_object)

        return changed
    
    def prepare_scan_auto(self, *args):
        if gio_settings.get_boolean("auto-update"):
            self.prepare_scan_manual()

    def prepare_scan_manual(self, *args):
        if self.open_button1.folder and self.open_button2.folder:
            self.master_container.append(self.footer_bar)
            self.content_pane.set_end_child(None)
            self.file_browser.store.remove_all()
            self.spinner.start()
            self.searching = True

            thread = threading.Thread(target=self.begin_scanning, daemon = True)
            thread.start()

class MyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        self.win = MainWindow(app)
        self.win.present()

app = MyApp(application_id=SCHEMA_ID)
app.run(sys.argv)
