"""Global Qt design tokens and compact semantic theming for ARPY Studio.

Inspired by the restrained density and semantic color discipline of modern
design systems; no third-party QSS framework and no per-cell style allocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from PySide6.QtGui import QColor, QPalette, QFont


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    canvas: str
    surface: str
    surface_alt: str
    sidebar: str
    hover: str
    selected: str
    text: str
    muted: str
    border: str
    primary: str
    primary_hover: str
    on_primary: str
    success: str
    warning: str
    danger: str
    input: str
    header: str


LIGHT = ThemeTokens(
    "light", "#f7f9fc", "#ffffff", "#f2f5f9", "#f2f5f9",
    "#e8eef6", "#dbeaf7", "#24354a", "#66778b", "#d9e1eb",
    "#286a91", "#205675", "#ffffff", "#14826e", "#a86b19",
    "#c74c59", "#ffffff", "#edf2f8",
)
DARK = ThemeTokens(
    "dark", "#141b25", "#1b2632", "#22303f", "#192330",
    "#283849", "#2b4962", "#e2e9f2", "#9aafc3", "#364557",
    "#69b6de", "#81c6ea", "#0e2230", "#4ac5a2", "#e0ae60",
    "#ec8392", "#1c2836", "#253546",
)
PALETTES = {"light": LIGHT, "dark": DARK}
SPACING = (0, 4, 8, 12, 16, 20, 24, 32)
ROW_HEIGHT = 25
SIDEBAR_WIDTH = 192
EXPLORER_WIDTH = 232


def stylesheet(t: ThemeTokens) -> str:
    return """
    QWidget {{
      color: {text}; background: {canvas};
      font-family: "Segoe UI", "Inter", "Noto Sans"; font-size: 12px;
    }}
    QWidget#Sidebar, QWidget#ExplorerPanel {{
      background: {sidebar}; border-right: 1px solid {border};
    }}
    QFrame#Panel, QFrame#Summary {{
      background: {surface}; border: 1px solid {border};
      border-radius: 6px;
    }}
    QLabel#Brand {{ color: {primary}; font-weight: 600; font-size: 13px; }}
    QLabel#PageTitle {{ font-size: 16px; font-weight: 500; }}
    QLabel#Muted, QLabel#FieldHint {{ color: {muted}; font-size: 11px; }}
    QLabel#SectionTitle {{ font-size: 12px; font-weight: 600; }}
    QLabel#StatusWarning {{ color: {warning}; font-size: 11px; }}
    QLabel#StatusOkay {{ color: {success}; font-size: 11px; }}
    QLabel#Notice {{
      color: {warning}; background: {surface_alt};
      border: 1px solid {border}; padding: 8px; border-radius: 4px;
    }}
    QListWidget, QTreeView {{
      background: {sidebar}; border: 0;
      outline: none; padding: 2px;
    }}
    QListWidget::item, QTreeView::item {{
      min-height: 23px; padding: 2px 5px;
      border-radius: 4px;
    }}
    QListWidget::item:selected, QTreeView::item:selected {{
      background: {selected}; color: {text};
    }}
    QListWidget::item:hover, QTreeView::item:hover {{
      background: {hover};
    }}
    QTableView, QTableWidget {{
      background: {surface}; alternate-background-color: {surface_alt};
      color: {text}; gridline-color: {border};
      border: 1px solid {border}; outline: 0;
      selection-background-color: {selected}; selection-color: {text};
    }}
    QTableView::item {{ padding: 2px 6px; }}
    QHeaderView::section {{
      background: {header}; color: {muted};
      padding: 4px 7px; border: 0;
      border-bottom: 1px solid {border}; border-right: 1px solid {border};
      font-weight: 500;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
      background: {input}; color: {text};
      border: 1px solid {border}; border-radius: 4px;
      padding: 3px 7px; min-height: 23px;
      selection-background-color: {selected};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
      border: 1px solid {primary};
    }}
    QPushButton {{
      background: {surface}; color: {text};
      border: 1px solid {border}; border-radius: 4px;
      padding: 5px 10px; min-height: 22px;
    }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:disabled {{ color: {muted}; background: {surface_alt}; }}
    QPushButton#Primary {{
      color: {on_primary}; background: {primary};
      border-color: {primary};
    }}
    QPushButton#Primary:hover {{ background: {primary_hover}; }}
    QStatusBar {{ background: {surface_alt}; color: {muted}; }}
    QSplitter::handle {{ background: {border}; }}
    QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; }}
    QScrollBar {{ background: {canvas}; border: none; width: 10px; height: 10px; }}
    QScrollBar::handle {{ background: {border}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    """.format(**vars(t))


def apply_theme(app, name: str = "light") -> ThemeTokens:
    t = PALETTES.get(name, LIGHT)
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(t.canvas))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(t.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(t.surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(t.surface_alt))
    palette.setColor(QPalette.ColorRole.Text, QColor(t.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(t.surface))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(t.text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(t.selected))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(t.text))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(t))
    return t
