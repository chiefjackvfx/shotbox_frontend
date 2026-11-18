"""Bundled QSS packs and reversible styling for widgets with local styles."""
from pathlib import Path
from datetime import date, datetime, timezone
import re
from weakref import WeakKeyDictionary, WeakSet

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QFrame

DEFAULT_THEME = "dark_v01.qss"
CHRISTMAS_THEME = "christmas_2026.qss"
CHRISTMAS_PARTY_DATE = date(2026, 12, 21)
THEMES = {
    DEFAULT_THEME: "Dark — Original",
    "gta6_vice_sunset.qss": "GTA VI — Vice Sunset",
    CHRISTMAS_THEME: "Christmas — Evergreen & Gold",
}
UI_DIR = Path(__file__).resolve().parent / "ui"
_local_styles = WeakKeyDictionary()
_theme_headers = WeakSet()
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


class _ConsoleHeader(QFrame):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 720
        if self.property("compact") != compact:
            self.setProperty("compact", compact)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
        if hasattr(self, "countdown"):
            self.countdown.setWordWrap(compact)
            self.countdown.setMaximumWidth(max(100, (self.width() - 48) // 2)
                                           if compact else 16777215)
            reserve = 300 if self.width() >= 1000 else (140 if not compact else 0)
            if self.art_space.sizeHint().width() != reserve:
                from PyQt6.QtWidgets import QSizePolicy
                self.art_space.changeSize(reserve, 0, QSizePolicy.Policy.Fixed,
                                          QSizePolicy.Policy.Minimum)
                self.layout().invalidate()


def normalize_theme(filename):
    return filename if isinstance(filename, str) and filename in THEMES else DEFAULT_THEME


def christmas_party_countdown(now=None):
    # During the November/December event season UK time is UTC.
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    days = (CHRISTMAS_PARTY_DATE - today).days
    if days > 0:
        return f"{days} {'DAY' if days == 1 else 'DAYS'} TO THE PARTY"
    if days == 0:
        return "IT'S PARTY DAY!"
    return "MERRY CHRISTMAS!"


def _refresh_header(header, selected, now=None):
    from PyQt6.QtWidgets import QLabel
    christmas = selected == CHRISTMAS_THEME
    eyebrow = header.findChild(QLabel, "vice_console_eyebrow")
    title = header.findChild(QLabel, "vice_console_title")
    eyebrow.setText("SHOTBOX  /  CHRISTMAS PARTY  /  21 DECEMBER 2026" if christmas
                    else "VICE OPERATIONS  /  PRODUCTION DESK")
    title.setText("SHOTBOX")
    header.countdown.setText(christmas_party_countdown(now) if christmas else "")
    header.countdown.setVisible(christmas)
    header.setVisible(christmas or selected == "gta6_vice_sunset.qss")


def refresh_theme_headers(now=None):
    selected = getattr(QApplication.instance(), "_shotbox_theme", DEFAULT_THEME)
    for header in list(_theme_headers):
        try:
            _refresh_header(header, selected, now)
        except RuntimeError:
            _theme_headers.discard(header)


def _recolour(stylesheet, palette):
    def replace(match):
        colour = match.group().lower()
        if len(colour) == 4:
            colour = "#" + "".join(c * 2 for c in colour[1:])
        return palette.get(colour, match.group())

    def recolour_rule(match):
        selector, body = match.groups()
        # Status and data colour selectors are semantic, not decoration.
        selector_text = re.sub(r"/\*.*?\*/", "", selector, flags=re.S)
        if re.search(r'\[(?:status|tone|error|shot_color|color|lock_state|has_matchmove|is_conformed)\s*=', selector_text):
            return selector + body
        return selector + _HEX.sub(replace, body)

    result = (re.sub(r"([^{}]*)(\{[^{}]*\})", recolour_rule, stylesheet)
              if "{" in stylesheet else _HEX.sub(replace, stylesheet))
    # Bright action fills need dark text, including object-specific buttons.
    if palette:
        bright_accents = "|".join(re.escape(palette[key]) for key in
                                  ("#ff6b35", "#d85a28", "#ff8659") if key in palette)
        def readable_action(match):
            block = match.group()
            if bright_accents and re.search(
                    rf"background(?:-color)?\s*:\s*(?:{bright_accents})\b", block, re.I):
                block = re.sub(r"(?<![-\w])color\s*:\s*#(?:FFFFFF|FFF5EA|FFF4DE)\b",
                               "color: " + palette.get("#1a1d23", "#171126"), block, flags=re.I)
            return block
        result = re.sub(r"\{[^{}]*\}|^[^{}]+$", readable_action, result)
    return result


def _palette():
    app = QApplication.instance()
    return getattr(app, "_shotbox_theme_palette", {})


def theme_colour(colour):
    """Resolve a decorative colour used by custom painters."""
    return _palette().get(colour.lower(), colour)


def set_widget_style(widget, stylesheet):
    """Keep original local QSS so theme changes never compound replacements."""
    rendered = _recolour(stylesheet, _palette())
    app = QApplication.instance()
    local_rules = getattr(app, "_shotbox_local_theme_rules", {})
    rendered += local_rules.get(widget.objectName(), "")
    _local_styles[widget] = (stylesheet, rendered)
    widget.setStyleSheet(rendered)


def create_theme_header(parent=None):
    """Create optional console chrome, controlled by the selected QSS pack."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy, QSpacerItem

    header = _ConsoleHeader(parent)
    header.setObjectName("vice_console_header")
    header.setFixedHeight(88)
    layout = QHBoxLayout(header)
    layout.setContentsMargins(24, 12, 24, 12)
    layout.setSpacing(16)
    branding = QVBoxLayout()
    branding.setSpacing(1)
    eyebrow = QLabel("VICE OPERATIONS  /  PRODUCTION DESK", header)
    eyebrow.setObjectName("vice_console_eyebrow")
    title = QLabel("SHOTBOX", header)
    title.setObjectName("vice_console_title")
    for label in (eyebrow, title):
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    branding.addWidget(eyebrow)
    branding.addWidget(title)
    layout.addLayout(branding, 1)
    header.countdown = QLabel(header)
    header.countdown.setObjectName("christmas_party_countdown")
    header.countdown.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    header.countdown.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
    layout.addWidget(header.countdown)
    header.art_space = QSpacerItem(0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
    layout.addItem(header.art_space)
    _theme_headers.add(header)
    _refresh_header(header, getattr(QApplication.instance(), "_shotbox_theme", DEFAULT_THEME))
    return header


def apply_theme(filename, *, fallback=True):
    """Apply a bundled pack, falling back to the original on read failure."""
    app = QApplication.instance()
    if app is None:
        if not fallback:
            raise RuntimeError("A QApplication is required to apply a theme")
        return DEFAULT_THEME
    selected = normalize_theme(filename)
    palette = {}
    local_rules = {}
    try:
        base = (UI_DIR / DEFAULT_THEME).read_text(encoding="utf-8")
        if selected == DEFAULT_THEME:
            stylesheet = base
        else:
            pack = (UI_DIR / selected).read_text(encoding="utf-8")
            pack = pack.replace("@THEME_UI@", UI_DIR.as_posix())
            palette = dict(re.findall(r"@map\s+(#[0-9a-f]{6})\s+(#[0-9A-Fa-f]{6})", pack))
            local_rules = dict(re.findall(
                r"/\* @local ([\w_]+) \*/(.*?)/\* @endlocal \*/", pack, re.S))
            stylesheet = _recolour(base, palette) + "\n" + pack
    except (OSError, UnicodeError) as exc:
        if not fallback:
            raise
        print(f"[Theme] Cannot load {selected}: {exc}")
        if selected != DEFAULT_THEME:
            return apply_theme(DEFAULT_THEME)
        return getattr(app, "_shotbox_theme", DEFAULT_THEME)

    app._shotbox_theme_palette = palette
    app._shotbox_theme = selected
    app._shotbox_local_theme_rules = local_rules
    logo = {
        "gta6_vice_sunset.qss": "shotbox_vice_logo.png",
        CHRISTMAS_THEME: "shotbox_christmas_logo.png",
    }.get(selected, "shotbox_logo.png")
    app.setWindowIcon(QIcon(str(UI_DIR / logo)))
    app.setStyleSheet(stylesheet)
    for widget, (original, previous) in list(_local_styles.items()):
        try:
            # Do not overwrite a style subsequently owned by another feature.
            if widget.styleSheet() == previous:
                set_widget_style(widget, original)
        except RuntimeError:  # Qt may have deleted the underlying widget.
            _local_styles.pop(widget, None)
    refresh_theme_headers()
    return selected
