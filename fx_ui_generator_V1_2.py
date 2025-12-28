import re
import sys
import os
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QFormLayout, QComboBox, QSlider, QSpinBox, QDoubleSpinBox, QLineEdit, QLabel, QCheckBox, QPushButton, QHBoxLayout, QScrollArea, QFileDialog, QMessageBox, QComboBox as QComboBoxWidget
)
import configparser
import time
try:
    import win32gui
    import win32con
    import pyautogui
except ImportError:
    win32gui = win32con = pyautogui = None

def parse_fx_uniforms(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    # Parse #define constants
    defines = {}
    for m in re.finditer(r'#define\s+(\w+)\s+([\d\.]+)', text):
        defines[m.group(1)] = m.group(2)
    # Find all uniform blocks
    pattern = re.compile(r'uniform\s+(\w+)\s+(\w+)\s*<([^>]*)>\s*=\s*([^;]+);', re.MULTILINE)
    meta_pattern = re.compile(r'(\w+)\s*=\s*"?([^;\n"]+)"?;?')
    uniforms = []
    for match in pattern.finditer(text):
        utype, name, meta, default = match.groups()
        meta_dict = dict(meta_pattern.findall(meta))
        # Resolve ui_min/ui_max if they reference a #define
        for k in ('ui_min', 'ui_max'):
            v = meta_dict.get(k)
            if v and not re.match(r'^-?\d+(\.\d+)?$', v):
                if v in defines:
                    meta_dict[k] = defines[v]
                else:
                    meta_dict[k] = '0' if k == 'ui_min' else '100'
        uniforms.append({
            'type': utype,
            'name': name,
            'meta': meta_dict,
            'default': default.strip()
        })
    return uniforms

def find_ini_for_fx(fx_path):
    # Try to find an ini file with the same base name as the fx file
    base = os.path.splitext(os.path.basename(fx_path))[0]
    dir_ = os.path.dirname(fx_path)
    for fname in os.listdir(dir_):
        if fname.lower().endswith('.ini') and base.lower() in fname.lower():
            return os.path.join(dir_, fname)
    return None

def parse_ini(ini_path):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str  # preserve key case
    section_name_map = {}
    key_name_map = {}  # {section_lower: {key_lower: original_key}}
    if not os.path.exists(ini_path):
        return config, section_name_map, key_name_map
    with open(ini_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # Map original section headers and keys to preserve case
    current_section = None
    for line in content.splitlines():
        section_match = re.match(r'^\[(.+?)\]', line)
        if section_match:
            current_section = section_match.group(1)
            section_name_map[current_section.lower()] = current_section
            key_name_map[current_section.lower()] = {}
        elif '=' in line and current_section:
            key, _ = line.split('=', 1)
            key = key.strip()
            key_name_map[current_section.lower()][key.lower()] = key
    if not re.match(r'\s*\[.*?\]', content):
        content = '[GLOBAL]\n' + content
        section_name_map['global'] = 'GLOBAL'
        key_name_map['global'] = {k: k for k in re.findall(r'^(\w+)\s*=', content, re.MULTILINE)}
    try:
        config.read_string(content)
    except configparser.DuplicateSectionError as e:
        return None, section_name_map, key_name_map, str(e)
    return config, section_name_map, key_name_map, None

def update_ini_from_widgets(config, section, uniforms, widgets):
    if section not in config:
        config.add_section(section)
    for u in uniforms:
        name = u['name']
        widget = widgets.get(name)
        if widget is None:
            continue
        if isinstance(widget, QComboBox):
            value = str(widget.currentIndex())
        elif isinstance(widget, QSpinBox) or isinstance(widget, QDoubleSpinBox):
            value = str(widget.value())
        elif isinstance(widget, QCheckBox):
            value = '1' if widget.isChecked() else '0'
        else:
            value = widget.text()
        config[section][name] = value

def set_widgets_from_ini(config, section, uniforms, widgets):
    if section not in config:
        return
    for u in uniforms:
        name = u['name']
        widget = widgets.get(name)
        if widget is None:
            continue
        value = config[section].get(name, u['default'])
        try:
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(int(value))
            elif isinstance(widget, QSpinBox) or isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value.lower() in ('1', 'true', 'yes'))
            else:
                widget.setText(value)
        except Exception:
            pass

class FXUniformUI(QWidget):
    def __init__(self, fx_path=None):
        super().__init__()
        self.setWindowTitle('FX Uniform UI Generator')
        self.fx_path = fx_path
        self.ini_path = None
        self.uniforms = []
        self.widgets = {}
        self.form = None
        self.form_widget = None
        self.scroll = None
        self.config = None
        self.section = None
        self.game_title = ''
        self.init_ui()
        if fx_path:
            self.load_fx(fx_path)

    def get_all_window_titles(self):
        titles = []
        if not win32gui:
            return titles
        def enum_handler(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    titles.append(title)
        win32gui.EnumWindows(enum_handler, None)
        return sorted(set(titles))

    def init_ui(self):
        self.layout = QVBoxLayout()
        btn_layout = QHBoxLayout()
        self.load_fx_btn = QPushButton('Load FX File')
        self.load_ini_btn = QPushButton('Load INI')
        self.save_ini_btn = QPushButton('Save INI')
        self.load_fx_btn.clicked.connect(self.load_fx_dialog)
        self.load_ini_btn.clicked.connect(self.load_ini_dialog)
        self.save_ini_btn.clicked.connect(self.save_ini)
        btn_layout.addWidget(self.load_fx_btn)
        btn_layout.addWidget(self.load_ini_btn)
        btn_layout.addWidget(self.save_ini_btn)
        self.layout.addLayout(btn_layout)
        # Add input for game window title
        title_layout = QHBoxLayout()
        self.title_label = QLabel('Game Window Title:')
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText('Enter exact game window title')
        self.title_input.textChanged.connect(self.set_game_title)
        # Add window selector dropdown
        self.window_selector = QComboBoxWidget()
        self.window_selector.setEditable(False)
        self.window_selector.addItem('Select window...')
        self.window_selector.addItems(self.get_all_window_titles())
        self.window_selector.currentIndexChanged.connect(self.window_selector_changed)
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.title_input)
        title_layout.addWidget(self.window_selector)
        self.layout.addLayout(title_layout)
        refresh_btn = QPushButton('Refresh Windows')
        refresh_btn.clicked.connect(self.refresh_window_selector)
        self.layout.addWidget(refresh_btn)
        self.status = QLabel()
        self.layout.addWidget(self.status)
        self.setLayout(self.layout)

    def refresh_window_selector(self):
        self.window_selector.clear()
        self.window_selector.addItem('Select window...')
        self.window_selector.addItems(self.get_all_window_titles())

    def window_selector_changed(self, idx):
        if idx > 0:
            title = self.window_selector.currentText()
            self.title_input.setText(title)

    def set_game_title(self, text):
        self.game_title = text

    def send_ctrl_r_to_window(self, window_title):
        if not win32gui or not pyautogui:
            self.status.setText('pywin32/pyautogui not installed.')
            return
        hwnd = win32gui.FindWindow(None, window_title)
        if hwnd:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            pyautogui.hotkey('ctrl', 'r')
            self.status.setText(f'Sent Ctrl+R to: {window_title}')
        else:
            self.status.setText(f'Window not found: {window_title}')

    def clear_form(self):
        if self.scroll:
            self.layout.removeWidget(self.scroll)
            self.scroll.deleteLater()
            self.scroll = None
        self.widgets.clear()

    def build_form(self):
        self.clear_form()
        self.form = QFormLayout()
        for u in self.uniforms:
            label = u['meta'].get('ui_label', u['name'])
            tooltip = u['meta'].get('ui_tooltip', '')
            w = self.make_widget(u)
            if tooltip:
                w.setToolTip(tooltip)
            self.form.addRow(label, w)
            self.widgets[u['name']] = w
        self.form_widget = QWidget()
        self.form_widget.setLayout(self.form)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.form_widget)
        self.layout.insertWidget(2, self.scroll)

    def make_widget(self, u):
        utype = u['type']
        meta = u['meta']
        default = u['default']
        if meta.get('ui_type') == 'combo':
            cb = QComboBox()
            items = meta.get('ui_items', '').split('\\0')
            cb.addItems([i for i in items if i])
            try:
                cb.setCurrentIndex(int(default))
            except Exception:
                pass
            return cb
        elif meta.get('ui_type') in ('slider', 'drag') or utype in ('int', 'float'):
            minv = float(meta.get('ui_min', 0))
            maxv = float(meta.get('ui_max', 100))
            step = float(meta.get('ui_step', 1))
            if utype == 'int':
                sb = QSpinBox()
                sb.setMinimum(int(minv))
                sb.setMaximum(int(maxv))
                sb.setSingleStep(int(step))
                try:
                    sb.setValue(int(float(default)))
                except Exception:
                    pass
                return sb
            else:
                dsb = QDoubleSpinBox()
                dsb.setMinimum(minv)
                dsb.setMaximum(maxv)
                dsb.setSingleStep(step)
                dsb.setDecimals(6)
                try:
                    dsb.setValue(float(default))
                except Exception:
                    pass
                return dsb
        elif utype == 'bool':
            cb = QCheckBox()
            cb.setChecked(default.lower() in ('1', 'true', 'yes'))
            return cb
        else:
            le = QLineEdit(default)
            return le

    def load_fx(self, fx_path):
        self.fx_path = fx_path
        self.uniforms = parse_fx_uniforms(fx_path)
        self.section = os.path.basename(fx_path)
        self.build_form()
        self.status.setText(f'Loaded FX: {fx_path}')
        # Try to find and load the ini
        ini_path = find_ini_for_fx(fx_path)
        if ini_path:
            self.load_ini(ini_path)

    def load_fx_dialog(self):
        file, _ = QFileDialog.getOpenFileName(self, 'Open FX File', '', 'FX Files (*.fx);;All Files (*)')
        if file:
            self.load_fx(file)

    def load_ini(self, ini_path):
        self.ini_path = ini_path
        result = parse_ini(ini_path)
        if len(result) == 4:
            self.config, self.section_name_map, self.key_name_map, err = result
            if self.config is None:
                QMessageBox.critical(self, 'INI Parse Error', f'Error parsing INI: {err}')
                self.status.setText('INI not loaded: parse error.')
                self.title_input.setText('')
                return
        else:
            self.config, self.section_name_map, self.key_name_map = result
        # Only require all FX keys to be present, allow extra keys/headers
        def normkey(k):
            return k.replace('_', '').lower()
        fx_keys = set(normkey(u['name']) for u in self.uniforms)
        ini_keys = set(normkey(k) for k in self.config[self.section].keys()) if self.section in self.config else set()
        missing = fx_keys - ini_keys
        if missing:
            orig_fx = [u['name'] for u in self.uniforms if normkey(u['name']) in missing]
            msg = 'INI file does not match FX layout!'
            if orig_fx:
                msg += f"\nMissing keys: {', '.join(sorted(orig_fx))}"
            QMessageBox.critical(self, 'INI Layout Error', msg)
            self.status.setText('INI not loaded: layout mismatch.')
            self.title_input.setText('')
            return
        set_widgets_from_ini(self.config, self.section, self.uniforms, self.widgets)
        self.status.setText(f'Loaded INI: {ini_path}')
        # Set game window title from INI if available
        title = self.config[self.section].get('game_window_title', '')
        self.title_input.setText(title)

    def load_ini_dialog(self):
        file, _ = QFileDialog.getOpenFileName(self, 'Open INI File', '', 'INI Files (*.ini);;All Files (*)')
        if file:
            self.load_ini(file)

    def save_ini(self):
        if not self.ini_path or not self.config:
            QMessageBox.warning(self, 'Error', 'No INI file loaded.')
            return
        # Only update the relevant section and keys, preserve all others
        update_ini_from_widgets(self.config, self.section, self.uniforms, self.widgets)
        with open(self.ini_path, 'w', encoding='utf-8') as f:
            wrote_any = False
            if 'GLOBAL' in self.config:
                for k, v in self.config['GLOBAL'].items():
                    orig_key = self.key_name_map.get('global', {}).get(k, k)
                    line = f'{orig_key}={v}\n'
                    f.write(line)
                wrote_any = True
            for section in self.config.sections():
                if section == 'GLOBAL':
                    continue
                if wrote_any:
                    f.write('\n')
                orig_section = self.section_name_map.get(section.lower(), section)
                section_header = f'[{orig_section}]\n'
                f.write(section_header)
                for k, v in self.config[section].items():
                    orig_key = self.key_name_map.get(section.lower(), {}).get(k, k)
                    line = f'{orig_key}={v}\n'
                    f.write(line)
                wrote_any = True
        self.status.setText(f'Saved INI: {self.ini_path}')
        # Send Ctrl+R to game window if title is set
        time.sleep(0.2)
        self.status.setText(f'game window to update: {self.game_title}')
        if hasattr(self, 'game_title') and self.game_title.strip():
            self.send_ctrl_r_to_window(self.game_title.strip())

if __name__ == '__main__':
    if len(sys.argv) > 1:
        fx_path = sys.argv[1]
    else:
        fx_path = None
    app = QApplication(sys.argv)
    win = FXUniformUI(fx_path)
    win.resize(600, 800)
    win.show()
    sys.exit(app.exec())
