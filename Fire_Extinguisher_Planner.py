import sys
import math
import re
import tempfile
import os
import json
import logging
import traceback
import urllib.request
from datetime import datetime

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QLineEdit, 
                             QPushButton, QTextEdit, QTabWidget, QMessageBox, 
                             QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox,
                             QFileDialog, QSplitter, QRadioButton, QButtonGroup, 
                             QAction, QMenu, QStatusBar, QDialog, QProgressBar)
from PyQt5.QtCore import Qt, QPointF, QRectF, QSettings, QThread, pyqtSignal, QSize
from PyQt5.QtGui import (QPainter, QPen, QColor, QBrush, QPolygonF, QTransform, 
                         QPalette, QCursor, QFont, QIcon)
from PyQt5.QtPrintSupport import QPrinter

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Polygon as MplPolygon

# --- LOGGING SETUP ---
LOG_FILENAME = os.path.join(tempfile.gettempdir(), 'fire_planner_debug.log')
logging.basicConfig(
    filename=LOG_FILENAME,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- GEOMETRY LIBRARY CHECK ---
try:
    from shapely.geometry import Point, Polygon, MultiPoint
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False
    logging.warning("Shapely library not found.")

# --- APP CONSTANTS ---
APP_NAME = "Fire Extinguisher Planner"
APP_VERSION = "1.0.0"
ORG_NAME = ""
UPDATE_URL = "https://raw.githubusercontent.com/Mohammad-Negahdary/Fire-Extinguisher-Planner/main/version.json"
NFPA_DATA = {
    "A": {
        "Light": {"max_area_per_unit_a": 3000, "max_area_per_ext": 11250, "travel_dist": 75},
        "Ordinary": {"max_area_per_unit_a": 1500, "max_area_per_ext": 11250, "travel_dist": 75},
        "Extra": {"max_area_per_unit_a": 1000, "max_area_per_ext": 11250, "travel_dist": 75},
    }
}

STANDARD_RATINGS = [
    "1-A:10-B:C", "2-A:10-B:C", "3-A:40-B:C", "4-A:60-B:C", 
    "6-A:80-B:C", "10-A:120-B:C", "20-A:120-B:C", 
    "30-A:160-B:C", "40-A:240-B:C", 
    "Class D (Metal)", "Class K (Kitchen)"
]

# Conversion Constants
FT_TO_M = 0.3048
SQFT_TO_SQM = 0.092903

# --- Update ---
class UpdateWorker(QThread):
    update_available = pyqtSignal(str, str) # version, url
    no_update = pyqtSignal()
    error = pyqtSignal(str)

    def run(self):
        try:
            with urllib.request.urlopen(UPDATE_URL, timeout=5) as url:
                data = json.loads(url.read().decode())
                remote_ver = data.get("version", "0.0.0")
                download_url = data.get("url", "")
                
                if remote_ver > APP_VERSION:
                    self.update_available.emit(remote_ver, download_url)
                else:
                    self.no_update.emit()
                    
        except Exception as e:
            logging.error(f"Update check failed: {str(e)}")
            self.error.emit(str(e))

# --- UTILITIES ---

class UnitManager:
    IMPERIAL = 0
    METRIC = 1
    
    def __init__(self):
        self.current_system = self.IMPERIAL

    def set_system(self, system):
        self.current_system = system

    def to_ft(self, val):
        return val if self.current_system == self.IMPERIAL else val / FT_TO_M

    def from_ft(self, val):
        return val if self.current_system == self.IMPERIAL else val * FT_TO_M

    def to_sqft(self, val):
        return val if self.current_system == self.IMPERIAL else val / SQFT_TO_SQM

    def from_sqft(self, val):
        return val if self.current_system == self.IMPERIAL else val * SQFT_TO_SQM
    
    def dist_label(self):
        return "ft" if self.current_system == self.IMPERIAL else "m"
    
    def area_label(self):
        return "ft²" if self.current_system == self.IMPERIAL else "m²"

UNITS = UnitManager()

class RatingParser:
    @staticmethod
    def parse(rating_str):
        rating_str = rating_str.upper().replace(" ", "")
        ratings = {"A": 0, "B": 0, "C": False, "D": False, "K": False}
        a_match = re.search(r'(\d+)[\-]A', rating_str)
        if a_match: ratings["A"] = int(a_match.group(1))
        b_match = re.search(r'(\d+)[\-]B', rating_str)
        if b_match: ratings["B"] = int(b_match.group(1))
        if ":C" in rating_str or "-C" in rating_str or "CLASSC" in rating_str: ratings["C"] = True
        if ":D" in rating_str or "-D" in rating_str or "CLASSD" in rating_str: ratings["D"] = True
        if ":K" in rating_str or "-K" in rating_str or "CLASSK" in rating_str: ratings["K"] = True
        return ratings

class ReportEngine:
    @staticmethod
    def generate_html(meta, final_qty, dist_qty, temp_img_path=None):
        css = """
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; color: #333; line-height: 1.6; background-color: #fff; }
            .container { width: 100%; max-width: 800px; margin: 0 auto; }
            h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; margin-top: 0; font-size: 22pt; }
            h2 { color: #2c3e50; margin-top: 25px; font-size: 14pt; background-color: #ecf0f1; padding: 8px; border-left: 5px solid #3498db; page-break-after: avoid; }
            h3 { font-size: 12pt; color: #2c3e50; margin-top: 20px; }
            .meta-box { background-color: #f8f9fa; border: 1px solid #e0e0e0; padding: 15px; margin-bottom: 20px; font-size: 11pt; color: #2c3e50; }
            .meta-row { margin-bottom: 5px; }
            .warning-box { background-color: #ffebee; border: 1px solid #ef9a9a; color: #c62828; padding: 10px; margin: 15px 0; font-weight: bold; font-size: 12pt; text-align: center; }
            .success-box { background-color: #e8f5e9; border: 1px solid #a5d6a7; color: #2e7d32; padding: 10px; margin: 15px 0; font-weight: bold; font-size: 12pt; text-align: center; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11pt; page-break-inside: avoid; }
            th { background-color: #34495e; color: white; text-align: left; padding: 8px; border: 1px solid #bdc3c7; }
            td { border: 1px solid #bdc3c7; padding: 8px; color: #000; }
            tr:nth-child(even) { background-color: #f2f2f2; }
            .footer { margin-top: 40px; font-size: 9pt; text-align: center; border-top: 1px solid #999; padding-top: 10px; color: #7f8c8d; }
            .map-container { text-align: center; margin-top: 20px; border: 1px solid #ddd; padding: 10px; page-break-inside: avoid; }
            img { max-width: 100%; height: auto; }
        </style>
        """
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_warnings = meta.get('current_warnings', meta['warnings'])

        status_div = ""
        if current_warnings:
            status_div = "<div class='warning-box'>⚠️ NON-COMPLIANT: ISSUES DETECTED</div>"
        else:
            status_div = "<div class='success-box'>✅ COMPLIANT: DESIGN MEETS CRITERIA</div>"

        warn_html = ""
        if current_warnings:
            warn_html = "<ul>"
            for w in current_warnings:
                warn_html += f"<li>{w}</li>"
            warn_html += "</ul>"

        area_disp = UNITS.from_sqft(meta['area_sqft'])
        dist_disp = UNITS.from_ft(meta['req_dist'])
        
        design_radius_ft = meta['req_dist'] * meta['safety_factor']
        design_rad_disp = UNITS.from_ft(design_radius_ft)
        
        u_dist = UNITS.dist_label()
        u_area = UNITS.area_label()

        map_html = ""
        if temp_img_path:
            map_html = f"<div class='map-container'><h3>3. Coverage Map</h3><img src='{temp_img_path}' width='600'></div>"

        html = f"""
        <html>
        <head>{css}</head>
        <body>
            <div class="container">
                <h1>Fire Extinguisher Planner Report</h1>
                
                <div class="meta-box">
                    <div class="meta-row"><strong>Project Name:</strong> {meta['inputs']['project']}</div>
                    <div class="meta-row"><strong>Date Generated:</strong> {timestamp}</div>
                    <div class="meta-row"><strong>Standard Reference:</strong> NFPA 10 (2022 Edition)</div>
                    <div class="meta-row"><strong>Selected Configuration:</strong> {meta.get('option_name', 'N/A')}</div>
                    <div class="meta-row"><strong>Software Version:</strong> {APP_VERSION}</div>
                </div>
                
                {status_div}
                {warn_html}
                
                <h2>1. Facility & Hazard Definition</h2>
                <table>
                    <tr><th width="40%">Parameter</th><th>Value</th></tr>
                    <tr><td>Hazard Classification</td><td>{meta['inputs']['class']}</td></tr>
                    <tr><td>Hazard Type</td><td>{meta['inputs']['type']}</td></tr>
                    <tr><td>Extinguisher Model/Rating</td><td>{meta['inputs']['rating']}</td></tr>
                    <tr><td>Calculated Floor Area</td><td>{area_disp:.2f} {u_area}</td></tr>
                </table>

                <h2>2. Compliance Calculations</h2>
                <table>
                    <tr><th>Metric</th><th>Result</th><th>NFPA 10 Limit</th></tr>
                    <tr><td>Max Travel Distance</td><td>{dist_disp:.2f} {u_dist}</td><td>Max {dist_disp:.2f} {u_dist}</td></tr>
                    <tr><td>Design Radius (Safety Factor {meta['safety_factor']})</td><td>{design_rad_disp:.2f} {u_dist}</td><td>(Used for Layout)</td></tr>
                    <tr><td>Units Required by Area Rule</td><td>{meta['min_qty_area']}</td><td>Ref: 6.2.1.2.1</td></tr>
                    <tr><td>Units Required by Travel Dist</td><td>{dist_qty}</td><td>Ref: Annex E</td></tr>
                    <tr><td><strong>TOTAL RECOMMENDED</strong></td><td><strong>{final_qty}</strong></td><td>-</td></tr>
                </table>
                
                {map_html}
                
                <div class="footer">
                    Generated by {APP_NAME} v{APP_VERSION}
                </div>
            </div>
        </body>
        </html>
        """
        return html

class CADCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(700, 500)
        self.setStyleSheet("background-color: #1e1e1e;") 
        self.setMouseTracking(True)
        
        self.points = [] # Always stored in logical pixels
        self.scale_unit_per_grid = 5.0
        self.grid_size_logical = 40.0 
        
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self.last_mouse_pos = QPointF(0,0)
        self.current_mouse_logical = None 
        self.panning = False
        
        self.drawing_active = True
        self.polygon_closed = False
        self.snap_enabled = True
        self.snapped_pos = None
        self.has_unsaved_changes = False

    def set_scale(self, val):
        self.scale_unit_per_grid = val
        self.update()

    def set_snap(self, enabled):
        self.snap_enabled = enabled
        self.update()

    def screen_to_logical(self, screen_pos):
        return (screen_pos - self.offset) / self.zoom

    def logical_to_screen(self, logical_pos):
        return (logical_pos * self.zoom) + self.offset

    def get_snapped_logical(self, logical_pos):
        if not self.snap_enabled: return logical_pos
        x = round(logical_pos.x() / self.grid_size_logical) * self.grid_size_logical
        y = round(logical_pos.y() / self.grid_size_logical) * self.grid_size_logical
        return QPointF(x, y)

    def wheelEvent(self, event):
        old_logical = self.screen_to_logical(event.pos())
        angle = event.angleDelta().y()
        factor = 1.1 if angle > 0 else 0.9
        self.zoom *= factor
        new_screen_expected = self.logical_to_screen(old_logical)
        self.offset += event.pos() - new_screen_expected
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.panning = True
            self.last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return

        if not self.drawing_active or self.polygon_closed: return
        
        if event.button() == Qt.LeftButton:
            logical_pos = self.screen_to_logical(event.pos())
            pt = self.get_snapped_logical(logical_pos)
            self.points.append(pt)
            self.has_unsaved_changes = True
            self.update()
            
        elif event.button() == Qt.RightButton and len(self.points) > 2:
            self.polygon_closed = True
            self.drawing_active = False
            self.update()

    def mouseMoveEvent(self, event):
        logical_pos = self.screen_to_logical(event.pos())
        self.current_mouse_logical = logical_pos
        
        if self.panning:
            delta = event.pos() - self.last_mouse_pos
            self.offset += delta
            self.last_mouse_pos = event.pos()
            self.update()
            return
        
        self.snapped_pos = self.get_snapped_logical(logical_pos)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))

        transform = QTransform()
        transform.translate(self.offset.x(), self.offset.y())
        transform.scale(self.zoom, self.zoom)
        painter.setTransform(transform)

        self.draw_grid(painter)

        pen_line = QPen(QColor("#00ff00"), 2)
        pen_line.setCosmetic(True)
        painter.setPen(pen_line)
        
        poly_pts = QPolygonF()
        for pt in self.points:
            poly_pts.append(pt)
            painter.drawEllipse(pt, 3/self.zoom, 3/self.zoom)

        if self.polygon_closed:
            brush = QBrush(QColor(0, 255, 0, 40))
            painter.setBrush(brush)
            painter.drawPolygon(poly_pts)
        else:
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(poly_pts)
            
            if self.points and self.drawing_active and self.snapped_pos:
                last_pt = self.points[-1]
                
                painter.setPen(QPen(QColor("#00ff00"), 1, Qt.DashLine))
                painter.drawLine(last_pt, self.snapped_pos)
                
                dx = self.snapped_pos.x() - last_pt.x()
                dy = self.snapped_pos.y() - last_pt.y()
                dist_logical = math.sqrt(dx*dx + dy*dy)
                
                scale_factor = self.scale_unit_per_grid / self.grid_size_logical
                real_dist = dist_logical * scale_factor
                
                unit_label = UNITS.dist_label()
                
                mid_x = (last_pt.x() + self.snapped_pos.x()) / 2
                mid_y = (last_pt.y() + self.snapped_pos.y()) / 2
                
                painter.setPen(QColor("#ffff00"))
                
                font = QFont("Arial", 10)
                font.setPointSizeF(10 / self.zoom)
                painter.setFont(font)
                
                painter.drawText(QPointF(mid_x, mid_y - 5/self.zoom), f"{real_dist:.1f} {unit_label}")

        # Draw Snap Marker
        if self.drawing_active and self.snapped_pos:
            painter.setPen(QPen(QColor("#00ffff"), 2/self.zoom))
            painter.setBrush(Qt.NoBrush)
            r = 8 / self.zoom
            sp = self.snapped_pos
            painter.drawEllipse(sp, r, r)

        # Draw HUD (Heads Up Display)
        painter.setTransform(QTransform())
        painter.setPen(QColor("white"))
        unit_str = UNITS.dist_label()
        painter.setFont(QFont("Arial", 10))
        painter.drawText(10, 20, f"Zoom: {self.zoom:.2f}x | Scale: 1 Grid = {self.scale_unit_per_grid} {unit_str}")

    def draw_grid(self, painter):
        inv_transform, _ = painter.transform().inverted()
        visible_rect = inv_transform.mapRect(QRectF(self.rect()))
        left = int(visible_rect.left())
        top = int(visible_rect.top())
        right = int(visible_rect.right())
        bottom = int(visible_rect.bottom())
        
        pen_grid = QPen(QColor("#505050"), 1)
        pen_grid.setCosmetic(True)
        painter.setPen(pen_grid)
        step = int(self.grid_size_logical)
        start_x = (left // step) * step
        start_y = (top // step) * step
        
        max_lines = 2000
        if (right - left) / step > max_lines: step *= 5
        
        for x in range(start_x, right, step): painter.drawLine(x, top, x, bottom)
        for y in range(start_y, bottom, step): painter.drawLine(left, y, right, y)

    def get_coordinates_in_ft(self):
        # Convert Logical -> User Units -> Feet
        factor = self.scale_unit_per_grid / self.grid_size_logical
        user_coords = [(p.x() * factor, p.y() * factor) for p in self.points]
        
        if UNITS.current_system == UnitManager.METRIC:
            return [(x / FT_TO_M, y / FT_TO_M) for x, y in user_coords]
        return user_coords

    def reset(self):
        self.points = []
        self.polygon_closed = False
        self.drawing_active = True
        self.has_unsaved_changes = False
        self.update()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1300, 850)
        
        # Ensure fire.png is in the same folder as the script
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fire.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # --- PERSISTENT SETTINGS ---
        self.settings = QSettings(ORG_NAME, APP_NAME.replace(" ", ""))
        self.load_settings()

        # --- Menu Bar ---
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        help_menu = menubar.addMenu("Help")
        
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        update_action = QAction("Check for Updates", self)
        update_action.triggered.connect(self.check_updates_manual)
        help_menu.addAction(update_action)

        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
        # --- Main Layout ---
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        
        # Unit Toggle Header
        unit_layout = QHBoxLayout()
        unit_lbl = QLabel("System of Units:")
        unit_lbl.setStyleSheet("font-weight: bold;")
        self.rb_imp = QRadioButton("Imperial (ft, sq ft)")
        self.rb_met = QRadioButton("Metric (m, sq m)")
        
        # Restore Unit Selection from settings
        if self.settings.value("unit_system", 0, type=int) == UnitManager.IMPERIAL:
            self.rb_imp.setChecked(True)
        else:
            self.rb_met.setChecked(True)
            
        self.rb_imp.toggled.connect(self.change_units)
        
        bg = QButtonGroup(self)
        bg.addButton(self.rb_imp)
        bg.addButton(self.rb_met)
        
        unit_layout.addWidget(unit_lbl)
        unit_layout.addWidget(self.rb_imp)
        unit_layout.addWidget(self.rb_met)
        unit_layout.addStretch()
        main_layout.addLayout(unit_layout)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab { height: 35px; width: 180px; font-weight: bold; font-size: 11pt; }")
        
        self.setup_cad_tab()
        self.setup_data_tab()
        self.setup_report_tab()
        
        main_layout.addWidget(self.tabs)
        
        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        
        self.generated_solutions = []
        self.current_solution_idx = 0
        self.last_analysis_meta = {}
        self.last_polygon = None

        # Run background update check
        self.update_worker = UpdateWorker()
        self.update_worker.update_available.connect(self.on_update_available)
        self.update_worker.no_update.connect(self.on_no_update)
        self.update_worker.error.connect(self.on_update_error)
        self.update_worker.start()

    def load_settings(self):
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        
    def closeEvent(self, event):
        if self.cad.has_unsaved_changes:
            reply = QMessageBox.question(self, 'Unsaved Changes',
                                         "You have unsaved drawing data. Are you sure you want to exit?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                event.ignore()
                return

        # Save Settings
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("unit_system", UnitManager.METRIC if self.rb_met.isChecked() else UnitManager.IMPERIAL)
        self.settings.setValue("grid_scale", self.scale_spin.value())
        self.settings.setValue("snap_enabled", self.snap_chk.isChecked())
        event.accept()

    def show_about(self):
        msg = QMessageBox()
        msg.setWindowTitle(f"About {APP_NAME}")
        msg.setText(f"{APP_NAME} v{APP_VERSION}")
        msg.setInformativeText(
            "Created by: Mohammad Negahdary\n"
            "Email: mohammadn3gahdary@gmail.com\n\n"
            "Features:\n"
            "- Geometric Coverage Analysis\n"
            "- Metric and Imperial Unit Support\n"
            "- Automated PDF Reporting\n"
            "- Persistent Preferences\n\n"
            "Copyright © 2025. All Rights Reserved."
        )
        msg.exec_()

    def check_updates_manual(self):
        self.status_bar.showMessage("Checking for updates...")
        self.update_worker.start()

    def on_update_available(self, version, url):
        self.status_bar.showMessage("Update Available!")
        reply = QMessageBox.question(self, "Update Available", 
                                     f"A new version ({version}) is available. Open download page?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            import webbrowser
            webbrowser.open(url)

    def change_units(self):
        if self.rb_imp.isChecked():
            UNITS.set_system(UnitManager.IMPERIAL)
            self.scale_spin.setSuffix(" ft/grid")
            self.lbl_liquid.setText("Liquid Surface Area (sq ft):")
        else:
            UNITS.set_system(UnitManager.METRIC)
            self.scale_spin.setSuffix(" m/grid")
            self.lbl_liquid.setText("Liquid Surface Area (sq m):")
        self.cad.update()
        self.status_bar.showMessage(f"Units switched to {UNITS.dist_label()}")

    def setup_cad_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        
        instr_label = QLabel("INSTRUCTIONS: Left Click to Add Point | Right Click to Close Polygon | Middle Click/Wheel to Pan/Zoom")
        instr_label.setStyleSheet("background-color: #333; color: #4dc3ff; padding: 5px; font-weight: bold;")
        instr_label.setAlignment(Qt.AlignCenter)
        instr_label.setFixedHeight(24)
        layout.addWidget(instr_label)
        
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>STEP 1: Draw Area</b>"))
        
        self.snap_chk = QCheckBox("Snap")
        self.snap_chk.setChecked(self.settings.value("snap_enabled", True, type=bool))
        self.snap_chk.toggled.connect(lambda x: self.cad.set_snap(x))
        
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 100)
        self.scale_spin.setValue(self.settings.value("grid_scale", 5, type=int))
        self.scale_spin.setSuffix(" ft/grid")
        self.scale_spin.valueChanged.connect(lambda x: self.cad.set_scale(x))
        
        clear_btn = QPushButton("Reset")
        clear_btn.clicked.connect(lambda: self.cad.reset())
        clear_btn.setStyleSheet("background-color: #d32f2f; color: white;")
        
        toolbar.addWidget(QLabel("Scale:"))
        toolbar.addWidget(self.scale_spin)
        toolbar.addWidget(self.snap_chk)
        toolbar.addStretch()
        toolbar.addWidget(clear_btn)
        
        layout.addLayout(toolbar)
        self.cad = CADCanvas()
        layout.addWidget(self.cad)
        
        tab.setLayout(layout)
        self.tabs.addTab(tab, "1. Drawing")

    def setup_data_tab(self):
        tab = QWidget()
        layout = QFormLayout()
        layout.setContentsMargins(50, 30, 50, 30)
        
        self.project_name = QLineEdit()
        self.hazard_class = QComboBox()
        self.hazard_class.addItems(["Light Hazard", "Ordinary Hazard", "Extra Hazard"])
        
        self.hazard_type = QComboBox()
        self.hazard_type.addItems([
            "Class A (Ordinary Combustibles)", 
            "Class B (Spill Fires)", 
            "Class B (Appreciable Depth)",
            "Class C (Electrical Equipment)",
            "Class K (Cooking)", "Class D (Metals)"
        ])
        
        self.rating_combo = QComboBox()
        self.rating_combo.setEditable(True)
        self.rating_combo.addItems(STANDARD_RATINGS)
        self.rating_combo.setCurrentIndex(3)
        
        self.liquid_area = QLineEdit("0")
        self.lbl_liquid = QLabel("Liquid Surface Area (sq ft):")
        
        # New Safety Factor Input
        self.safety_factor_spin = QDoubleSpinBox()
        self.safety_factor_spin.setRange(0.5, 1.0)
        self.safety_factor_spin.setSingleStep(0.05)
        self.safety_factor_spin.setValue(1.0)
        self.safety_factor_spin.setToolTip("Ratio of Euclidean Radius to Walking Distance. Use 1.0 for strict radius, or lower (e.g. 0.85) to account for walls/furniture.")
        
        calc_btn = QPushButton("RUN ANALYSIS")
        calc_btn.setMinimumHeight(40)
        calc_btn.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold;")
        calc_btn.clicked.connect(self.run_analysis)
        
        layout.addRow("Project Name:", self.project_name)
        layout.addRow("Hazard Class:", self.hazard_class)
        layout.addRow("Hazard Type:", self.hazard_type)
        layout.addRow("Extinguisher Rating:", self.rating_combo)
        layout.addRow(self.lbl_liquid, self.liquid_area)
        layout.addRow("Pathing Safety Factor:", self.safety_factor_spin)
        layout.addRow(QLabel("(1.0 = Strict Radius, <1.0 = Allow for Obstacles)"))
        layout.addRow("", calc_btn)
        
        tab.setLayout(layout)
        self.tabs.addTab(tab, "2. Input")

    def setup_report_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        
        bar = QHBoxLayout()
        self.prev_btn = QPushButton("< Previous Option")
        self.prev_btn.clicked.connect(lambda: self.change_sol(-1))
        self.opt_lbl = QLabel("Option 1 of 3")
        self.opt_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.next_btn = QPushButton("Next Option >")
        self.next_btn.clicked.connect(lambda: self.change_sol(1))
        export_btn = QPushButton("Export PDF Report")
        export_btn.setStyleSheet("background-color: #2E7D32; color: white;")
        export_btn.clicked.connect(self.export_pdf)
        
        bar.addWidget(self.prev_btn)
        bar.addStretch()
        bar.addWidget(self.opt_lbl)
        bar.addStretch()
        bar.addWidget(self.next_btn)
        bar.addWidget(export_btn)
        layout.addLayout(bar)
        
        splitter = QSplitter(Qt.Horizontal)
        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        
        self.figure = Figure(facecolor='white')
        self.mpl_canvas = FigureCanvas(self.figure)
        
        splitter.addWidget(self.report_view)
        splitter.addWidget(self.mpl_canvas)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        
        layout.addWidget(splitter)
        tab.setLayout(layout)
        self.tabs.addTab(tab, "3. Results & Options")

    def run_analysis(self):
        logging.info("Starting analysis...")
        if not HAS_SHAPELY:
            QMessageBox.critical(self, "Error", "Shapely library missing. Install via: pip install shapely")
            return
            
        pts = self.cad.get_coordinates_in_ft()
        if len(pts) < 3:
            QMessageBox.warning(self, "Error", "Draw a closed area first.")
            self.tabs.setCurrentIndex(0)
            return

        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        
        self.last_polygon = poly 

        hz_class = self.hazard_class.currentText().split()[0]
        hz_type = self.hazard_type.currentText()
        rating_str = self.rating_combo.currentText()
        ratings = RatingParser.parse(rating_str)
        safety_factor = self.safety_factor_spin.value()
        
        warnings = []
        req_dist = 75 # Feet
        area_sqft = poly.area
        min_qty_area = 0
        
        if "Class A" in hz_type:
            reqs = NFPA_DATA["A"][hz_class]
            if hz_class == "Light" and ratings["A"] < 2: warnings.append("Light Hazard requires 2-A min.")
            if hz_class == "Ordinary" and ratings["A"] < 2: warnings.append("Ordinary Hazard requires 2-A min.")
            if hz_class == "Extra" and ratings["A"] < 4: warnings.append("Extra Hazard requires 4-A min (or two 2.5 gal water units).")
            
            max_area_unit = min(ratings["A"] * reqs["max_area_per_unit_a"], reqs["max_area_per_ext"])
            min_qty_area = math.ceil(area_sqft / max_area_unit)
            
        elif "Spill" in hz_type:
            req_dist = 30 # Default safe
            rating_b = ratings["B"]
            if hz_class == "Light":
                if rating_b >= 10: req_dist = 50
                elif rating_b < 5: warnings.append("Light Hazard Spill requires 5-B min.")
            elif hz_class == "Ordinary":
                if rating_b >= 20: req_dist = 50
                elif rating_b < 10: warnings.append("Ordinary Hazard Spill requires 10-B min.")
            elif hz_class == "Extra":
                if rating_b >= 80: req_dist = 50
                elif rating_b < 40: warnings.append("Extra Hazard Spill requires 40-B min.")
            min_qty_area = 0
            
        elif "Appreciable Depth" in hz_type:
            req_dist = 50
            l_area = float(self.liquid_area.text()) if self.liquid_area.text() else 0
            if UNITS.current_system == UnitManager.METRIC: l_area *= 10.7639 
            
            if l_area > 10.0:
                QMessageBox.critical(self, "Code Violation", "Portable fire extinguishers shall not be installed as the sole protection for flammable liquid hazards of appreciable depth where the surface area exceeds 10 ft² (0.93 m²).")
                return

            req_b = l_area * 2
            if ratings["B"] < req_b: warnings.append(f"Rating too low. Need {req_b}-B (Dry Chem). Foam allows less.")
            min_qty_area = 0

        elif "Class C" in hz_type:
            req_dist = 75
            if not ratings["C"]: warnings.append("Must be Class C listed (Non-Conductive).")
            if "Dry" in rating_str or ("A" in rating_str and "B" in rating_str and "C" in rating_str):
                 warnings.append("WARNING: Dry chemical extinguishers should not be used on sensitive electronic equipment (NFPA 10 5.5.4.6.2).")

        elif "Class K" in hz_type:
            req_dist = 30
            if not ratings["K"]: warnings.append("Must be Class K listed.")
            min_qty_area = 0
        
        elif "Class D" in hz_type:
            req_dist = 75
            if not ratings["D"]: warnings.append("Must be Class D listed.")
            min_qty_area = 0

        self.generated_solutions = self.generate_multiple_solutions(poly, req_dist, safety_factor)
        
        self.last_analysis_meta = {
            "area_sqft": area_sqft,
            "req_dist": req_dist,
            "safety_factor": safety_factor,
            "min_qty_area": min_qty_area,
            "warnings": warnings,
            "inputs": {
                "project": self.project_name.text() if self.project_name.text() else "Untitled Project",
                "class": hz_class,
                "type": hz_type,
                "rating": rating_str
            }
        }
        
        self.current_solution_idx = 0
        self.update_result()
        self.tabs.setCurrentIndex(2)
        self.status_bar.showMessage("Analysis Complete")
        self.cad.has_unsaved_changes = False

    def generate_multiple_solutions(self, poly, radius, safety_factor):
        solutions = []
        effective_radius = radius * safety_factor
        
        s1 = self.calculate_grid_placement(poly, effective_radius, offset_ratio=(0,0))
        solutions.append({"name": "Option A: Standard Grid", "points": s1})
        
        s2 = self.calculate_grid_placement(poly, effective_radius, offset_ratio=(0.5, 0.5))
        solutions.append({"name": "Option B: Offset Grid", "points": s2})
        
        s3 = self.calculate_hex_placement(poly, effective_radius)
        solutions.append({"name": "Option C: Hexagonal Packing", "points": s3})
        
        return solutions

    def calculate_grid_placement(self, poly, radius, offset_ratio=(0,0)):
        spacing = radius * 1.414 * 0.95 
        minx, miny, maxx, maxy = poly.bounds
        points = []
        start_x = minx + (spacing * offset_ratio[0])
        start_y = miny + (spacing * offset_ratio[1])
        x = start_x
        while x < maxx + spacing:
            y = start_y
            while y < maxy + spacing:
                p = Point(x, y)
                if poly.contains(p):
                    points.append((x,y))
                y += spacing
            x += spacing
        return self.ensure_coverage(poly, points, radius)

    def calculate_hex_placement(self, poly, radius):
        spacing_x = radius * 1.732 * 0.95
        spacing_y = radius * 1.5 * 0.95
        minx, miny, maxx, maxy = poly.bounds
        points = []
        row = 0
        y = miny + radius/2
        while y < maxy + radius:
            offset = 0 if row % 2 == 0 else spacing_x / 2
            x = minx + offset + radius/2
            while x < maxx + radius:
                p = Point(x, y)
                if poly.contains(p):
                    points.append((x,y))
                x += spacing_x
            y += spacing_y
            row += 1
        return self.ensure_coverage(poly, points, radius)

    def ensure_coverage(self, poly, points, radius):
        final_points = list(points)
        if not final_points:
            c = poly.centroid
            if poly.contains(c): final_points.append((c.x, c.y))
            else:
                p_rep = poly.representative_point()
                final_points.append((p_rep.x, p_rep.y))
        
        test_points = list(poly.exterior.coords)
        mp = MultiPoint(final_points)
        for tp in test_points:
            pt = Point(tp)
            if mp.distance(pt) > radius:
                new_pt = self.find_internal_spot(poly, pt, radius)
                if new_pt:
                    final_points.append(new_pt)
                    mp = MultiPoint(final_points)
        return final_points

    def find_internal_spot(self, poly, vertex_pt, radius):
        target = poly.representative_point()
        vec_x = target.x - vertex_pt.x
        vec_y = target.y - vertex_pt.y
        dist = math.hypot(vec_x, vec_y)
        if dist == 0: return (target.x, target.y)
        
        steps = int(dist / 2.0)
        for i in range(1, steps):
            ratio = (i * 2.0) / dist
            nx = vertex_pt.x + vec_x * ratio
            ny = vertex_pt.y + vec_y * ratio
            candidate = Point(nx, ny)
            if poly.contains(candidate): return (nx, ny)
        return (target.x, target.y)

    def change_sol(self, delta):
        self.current_solution_idx = max(0, min(len(self.generated_solutions)-1, self.current_solution_idx + delta))
        self.update_result()

    def check_geometric_coverage(self, poly, points, radius, safety_factor):
        effective_r = radius * safety_factor
        if not points: return False
        try:
            circles = [Point(p).buffer(effective_r) for p in points]
            coverage = unary_union(circles)
            return coverage.contains(poly) or (coverage.intersection(poly).area >= poly.area * 0.999)
        except Exception as e:
            logging.error(f"Coverage check failed: {e}")
            return True

    def update_result(self):
        meta = self.last_analysis_meta
        sol = self.generated_solutions[self.current_solution_idx]
        pts = sol['points']
        
        self.opt_lbl.setText(f"{sol['name']} ({self.current_solution_idx+1} of {len(self.generated_solutions)})")
        
        qty_dist = len(pts)
        qty_final = max(qty_dist, meta['min_qty_area'])
        
        display_meta = meta.copy()
        display_meta['current_warnings'] = list(meta['warnings'])
        display_meta['option_name'] = sol['name']

        is_covered = self.check_geometric_coverage(self.last_polygon, pts, meta['req_dist'], meta['safety_factor'])
        if not is_covered:
            display_meta['current_warnings'].append(f"CRITICAL: Extinguisher placement does not fully cover the floor area (Safety Factor {meta['safety_factor']} applied).")

        html = ReportEngine.generate_html(display_meta, qty_final, qty_dist)
        self.report_view.setHtml(html)
        self.plot_map(pts, meta['req_dist'], meta['safety_factor'])
        
        self.prev_btn.setEnabled(self.current_solution_idx > 0)
        self.next_btn.setEnabled(self.current_solution_idx < len(self.generated_solutions)-1)

    def plot_map(self, points, radius_ft, safety_factor):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        
        disp_pts = [ (UNITS.from_ft(p[0]), UNITS.from_ft(p[1])) for p in points ]
        
        eff_radius = radius_ft * safety_factor
        disp_rad = UNITS.from_ft(eff_radius)
        
        poly_pts = self.cad.get_coordinates_in_ft()
        disp_poly = [ (UNITS.from_ft(p[0]), UNITS.from_ft(p[1])) for p in poly_pts ]
        
        poly_patch = MplPolygon(disp_poly, closed=True, fill=True, fc='#d0d0d0', ec='black', alpha=0.5, label='Building Area')
        ax.add_patch(poly_patch)
        
        for i, p in enumerate(disp_pts):
            c = Circle(p, disp_rad, color='blue', alpha=0.1)
            ax.add_patch(c)
            ax.plot(p[0], p[1], 'r^', markersize=8)
            ax.text(p[0], p[1], f" {i+1}", fontsize=9, color='black', fontweight='bold')
            
        ax.set_aspect('equal')
        ax.set_title(f"Coverage Map (Effective Radius: {disp_rad:.1f} {UNITS.dist_label()})")
        ax.grid(True, linestyle=':', alpha=0.6)
        
        if disp_poly:
            xs = [p[0] for p in disp_poly]
            ys = [p[1] for p in disp_poly]
            margin = disp_rad * 1.2
            ax.set_xlim(min(xs)-margin, max(xs)+margin)
            ax.set_ylim(min(ys)-margin, max(ys)+margin)
        
        self.mpl_canvas.draw()

    def export_pdf(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save PDF Report", "Fire_Extinguisher_Plan.pdf", "PDF Files (*.pdf)")
        if not filename: return
        
        temp_img = os.path.join(tempfile.gettempdir(), "nfpa_map.png")
        self.figure.savefig(temp_img, dpi=150, bbox_inches='tight')
        
        meta = self.last_analysis_meta.copy()
        sol = self.generated_solutions[self.current_solution_idx]
        meta['option_name'] = sol['name']
        meta['current_warnings'] = list(meta['warnings'])
        
        pts = sol['points']
        if not self.check_geometric_coverage(self.last_polygon, pts, meta['req_dist'], meta['safety_factor']):
             meta['current_warnings'].append(f"CRITICAL: Extinguisher placement does not fully cover the floor area (Safety Factor {meta['safety_factor']} applied).")

        qty_dist = len(pts)
        qty_final = max(qty_dist, meta['min_qty_area'])
        
        full_html = ReportEngine.generate_html(meta, qty_final, qty_dist, temp_img)
        
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setPageSize(QPrinter.A4) 
        printer.setPageMargins(15, 15, 15, 15, QPrinter.Millimeter) 
        printer.setOutputFileName(filename)
        
        doc = QTextEdit()
        doc.setHtml(full_html)
        doc.print_(printer)
        
        self.status_bar.showMessage("PDF Exported Successfully")
        QMessageBox.information(self, "Success", "Report exported successfully!")
    
    def on_no_update(self):
        self.status_bar.showMessage("You are using the latest version.")

    def on_update_error(self, error_msg):
        self.status_bar.showMessage("Update check failed.")
        logging.error(f"Update Error: {error_msg}")

# --- CRASH HANDLER ---
def exception_hook(exctype, value, tb):
    error_msg = "".join(traceback.format_exception(exctype, value, tb))
    logging.critical(f"Uncaught exception:\n{error_msg}")
    
    # Show GUI dialog
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Critical)
    msg.setWindowTitle("Critical Error")
    msg.setText("An unexpected error occurred.")
    msg.setInformativeText(f"Please check the log file for details:\n{LOG_FILENAME}")
    msg.setDetailedText(error_msg)
    msg.exec_()
    
    sys.__excepthook__(exctype, value, tb)

if __name__ == "__main__":
    sys.excepthook = exception_hook
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Dark Theme
    p = QPalette()
    p.setColor(QPalette.Window, QColor(53, 53, 53))
    p.setColor(QPalette.WindowText, Qt.white)
    p.setColor(QPalette.Base, QColor(25, 25, 25))
    p.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    p.setColor(QPalette.ToolTipBase, Qt.white)
    p.setColor(QPalette.ToolTipText, Qt.white)
    p.setColor(QPalette.Text, Qt.white)
    p.setColor(QPalette.Button, QColor(53, 53, 53))
    p.setColor(QPalette.ButtonText, Qt.white)
    p.setColor(QPalette.BrightText, Qt.red)
    p.setColor(QPalette.Link, QColor(42, 130, 218))
    p.setColor(QPalette.Highlight, QColor(42, 130, 218))
    p.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(p)
    
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())