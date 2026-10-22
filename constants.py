APP_TITLE = "Generatore Turni Mensili - modulare"
AUTOSAVE_FILE = "ultimo_progetto_turni.json"

SOLVER_SHIFTS = ["M", "P", "N", "R", "S"]
FIXED_ONLY_SHIFTS = ["Amb", "G", "F", "A", "Pr", "Ag", "MAL"]
MALATTIA_COVER_SHIFTS = ["MCM", "PCM", "NCM"]
DESIDERATA_VALUES = ["MD", "PD", "ND", "RD"]
FORM2_VALUES = [""] + FIXED_ONLY_SHIFTS + DESIDERATA_VALUES
OUTPUT_EDIT_VALUES = SOLVER_SHIFTS + FIXED_ONLY_SHIFTS + MALATTIA_COVER_SHIFTS

ALL_SHIFTS = SOLVER_SHIFTS + FIXED_ONLY_SHIFTS + MALATTIA_COVER_SHIFTS
ACTIVE_SHIFTS = {"M", "P", "N", "Amb", "G", "MCM", "PCM", "NCM"}
REST_SHIFTS = {"R", "S"}

CATEGORY_OPTIONS = [
    "Turni standard",
    "solo notti",
    "solo mattina",
    "solo pomeriggio",
    "solo mattina e pomeriggio",
    "Solo ambulatori",
    "CapoSala",
    "Infermiera di Sala",
]

ROLE_OPTIONS = ["CapoTurno", "Mezzana", "CapoSala", "Giovane"]
YESNO = ["SI", "NO"]
IVG_OPTIONS = ["MAI", "Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]

ROLE_COLORS = {
    "CapoTurno": "#ffd7d7",
    "Mezzana": "#d7f0ff",
    "CapoSala": "#fff0b3",
    "Giovane": "#d8f3dc",
}

SKILL_COLORS = {
    "obiettore": "#ff8fab",
    "triage": "#80ed99",
    "salaop": "#90caf9",
}

TOGGLE_YES_COLOR = "#caffbf"
TOGGLE_NO_COLOR = "#ffadad"
TOGGLE_EMPTY_COLOR = "white"

HOURS = {
    "M": 7.5,
    "P": 7.5,
    "N": 9.0,
    "S": 0.0,
    "R": 0.0,
    "Amb": 7.0,
    "G": 7.5,
    "F": 8.0,
    "A": 0.0,
    "Pr": 0.0,
    "Ag": 0.0,
    "MCM": 7.5,
    "PCM": 7.5,
    "NCM": 9.0,
    "MAL": 0.0,
}

FATIGUE = {
    "M":  {"M": 2, "P": 1, "N": 2, "S": None, "R": -1, "Amb": 2, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "P":  {"M": None, "P": 2, "N": 2, "S": None, "R": -1, "Amb": 2, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "N":  {"M": 2, "P": None, "N": 3, "S": -2, "R": None, "Amb": 2, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "S":  {"M": 2, "P": 2, "N": 2, "S": None, "R": -1, "Amb": 2, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "R":  {"M": 1, "P": 1, "N": 1, "S": None, "R": -1, "Amb": 1, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "Amb":{"M": 2, "P": 1, "N": 2, "S": None, "R": -1, "Amb": 2, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "G":  {"M": 2, "P": 2, "N": 2, "S": None, "R": -1, "Amb": 2, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "F":  {"M": 1, "P": 1, "N": 1, "S": None, "R": -1, "Amb": 1, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "A":  {"M": 1, "P": 1, "N": 1, "S": None, "R": -1, "Amb": 1, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "Pr": {"M": 1, "P": 1, "N": 1, "S": None, "R": -1, "Amb": 1, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
    "Ag": {"M": 1, "P": 1, "N": 1, "S": None, "R": -1, "Amb": 1, "G": 1, "F": 0, "A": 0, "Pr": 0, "Ag": 0},
}

SHIFT_COLORS = {
    "M": "#d9edf7",
    "P": "#ffe0b2",
    "N": "#d1c4e9",
    "R": "white",
    "S": "white",
    "Amb": "#f8c8ff",
    "G": "#fff2a8",
    "F": "white",
    "A": "#e9ecef",
    "Pr": "#f8d7da",
    "Ag": "#fff3cd",
    "MCM": "#a0e7e5",
    "PCM": "#b4f8c8",
    "NCM": "#bdb2ff",
    "MAL": "#f2f2f2",
}

SATURDAY_COLOR = "#d9ecff"
SUNDAY_COLOR = "#ffdede"
HOLIDAY_COLOR = "#ffe699"
HEADER_COLOR = "#e6e6e6"
OVERRIDE_COLOR = "#caffbf"

WEIGHTS = {
    "gap_hours": 10,
    "gap_fatigue": 35,
    "gap_weekend": 6,
    "soft_ns": 30,
    "soft_mp": 18,
    "soft_pn": 18,
    "soft_rrm": 10,
    "soft_sr": 18,
    "over5active": 16,
    "desired_miss": 10,
    "cover_shortage_M": 1000,
    "cover_shortage_P": 1000,
    "cover_shortage_N": 1000,
    "single_capoturno_without_mezzana": 12,
}

TUTORIAL_TEXT = """
Versione modulare

Funzioni principali:
- skill e ruoli dipendenti
- FORM2
- categorie speciali
- IVG
- editing output con override forti
- figure
- export CSV
- autosave/autoload
""".strip()