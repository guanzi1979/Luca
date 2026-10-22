import uuid
from constants import FIXED_ONLY_SHIFTS
from utils import next_month_year, day_info_list, week_ranges_for_month, italian_weekday_name_to_index, normalize_names_list


def should_prefill_by_category(category):
    return category in {"Solo ambulatori", "CapoSala", "Infermiera di Sala"}


def prefill_value_for_category(category, day_info):
    if category == "Solo ambulatori":
        return "R" if (day_info["is_saturday"] or day_info["is_sunday"]) else "Amb"
    if category == "CapoSala":
        return "R" if (day_info["is_saturday"] or day_info["is_sunday"]) else "G"
    if category == "Infermiera di Sala":
        return "R" if (day_info["is_saturday"] or day_info["is_sunday"]) else "G"
    return ""


def category_allowed_solver_shifts(category):
    if category == "Turni standard":
        return {"M", "P", "N", "R", "S"}
    if category == "solo notti":
        return {"N", "R", "S"}
    if category == "solo mattina":
        return {"M", "R"}
    if category == "solo pomeriggio":
        return {"P", "R"}
    if category == "solo mattina e pomeriggio":
        return {"M", "P", "R"}
    if category in {"Solo ambulatori", "CapoSala", "Infermiera di Sala"}:
        return {"R"}
    return {"M", "P", "N", "R", "S"}


class AppState:
    def __init__(self):
        m, y = next_month_year()
        self.num_employees = 30
        self.month = m
        self.year = y
        self.desiderata_mode = "vincolo debole"
        self.min_m = 5
        self.min_p = 5
        self.min_n = 5
        self.seed = 1
        self.allow_elastic_coverage = True

        self.names = [f"DIP{i+1}" for i in range(self.num_employees)]
        self.employee_ids = [
            str(uuid.uuid4()) for _ in range(self.num_employees)
        ]
        self.categories = ["Turni standard"] * self.num_employees

        self.roles = ["Giovane"] * self.num_employees
        self.obiettore = ["NO"] * self.num_employees
        self.abil_triage = ["NO"] * self.num_employees
        self.abil_salaop = ["NO"] * self.num_employees

        self.form2_entries = {}
        self.solution = None
        self.violations = {"hard": [], "soft": []}

        self.figure_assignments = {}
        self.output_overrides = {}
        self.ivg_by_week = {}

        self.previous_hours_by_employee = {}
        self.previous_fatigue_by_employee = {}

    def resize_employees(self, n):
        self.num_employees = n

        if len(self.employee_ids) < n:
            self.employee_ids.extend(
                str(uuid.uuid4())
                for _ in range(n - len(self.employee_ids))
            )
        else:
            del self.employee_ids[n:]

        def resize_list(lst, default):
            if len(lst) < n:
                lst.extend([default] * (n - len(lst)))
            else:
                del lst[n:]

        self.names = normalize_names_list(self.names, n)
        resize_list(self.categories, "Turni standard")
        resize_list(self.roles, "Giovane")
        resize_list(self.obiettore, "NO")
        resize_list(self.abil_triage, "NO")
        resize_list(self.abil_salaop, "NO")

        self.form2_entries = {(e, d): v for (e, d), v in self.form2_entries.items() if e < n}
        self.output_overrides = {(e, d): v for (e, d), v in self.output_overrides.items() if e < n}
        self.previous_hours_by_employee = {e: v for e, v in self.previous_hours_by_employee.items() if e < n}
        self.previous_fatigue_by_employee = {e: v for e, v in self.previous_fatigue_by_employee.items() if e < n}

    def set_names_preserve(self, names, n):
        self.names = normalize_names_list(names, n)
        self.resize_employees(n)

    def apply_category_prefill(self):
        infos = day_info_list(self.year, self.month)
        n_days = len(infos)
        for e in range(self.num_employees):
            cat = self.categories[e]
            if should_prefill_by_category(cat):
                for d in range(n_days):
                    self.form2_entries[(e, d)] = prefill_value_for_category(cat, infos[d])

    def ensure_ivg_defaults(self):
        weeks = week_ranges_for_month(self.year, self.month)
        for w in range(len(weeks)):
            if w not in self.ivg_by_week:
                self.ivg_by_week[w] = "Lun"

    def get_ivg_day_index_for_week(self, week_idx):
        self.ensure_ivg_defaults()
        choice = self.ivg_by_week.get(week_idx, "Lun")
        wd = italian_weekday_name_to_index(choice)
        if wd is None:
            return None

        weeks = week_ranges_for_month(self.year, self.month)
        if week_idx >= len(weeks):
            return None

        start, end = weeks[week_idx]
        infos = day_info_list(self.year, self.month)
        for d in range(start, end + 1):
            if infos[d]["weekday"] == wd:
                return d
        return None

    def effective_form2_display_value(self, e, d):
        return self.output_overrides.get((e, d), self.form2_entries.get((e, d), ""))

    def to_json_dict(self):
        return {
            "num_employees": self.num_employees,
            "month": self.month,
            "year": self.year,
            "desiderata_mode": self.desiderata_mode,
            "min_m": self.min_m,
            "min_p": self.min_p,
            "min_n": self.min_n,
            "seed": self.seed,
            "allow_elastic_coverage": self.allow_elastic_coverage,
            "names": self.names,
            "categories": self.categories,
            "roles": self.roles,
            "obiettore": self.obiettore,
            "abil_triage": self.abil_triage,
            "abil_salaop": self.abil_salaop,
            "form2_entries": [
                {"employee": e, "day": d, "value": v}
                for (e, d), v in sorted(self.form2_entries.items())
                if v != ""
            ],
            "figure_assignments": [
                {"day": d, "shift": s, "figure": f, "name": name}
                for (d, s, f), name in sorted(self.figure_assignments.items())
                if name
            ],
            "output_overrides": [
                {"employee": e, "day": d, "value": v}
                for (e, d), v in sorted(self.output_overrides.items())
                if v
            ],
            "ivg_by_week": [
                {"week_idx": w, "value": v}
                for w, v in sorted(self.ivg_by_week.items())
            ],
            "previous_hours_by_employee": [
                {"employee": e, "value": v}
                for e, v in sorted(self.previous_hours_by_employee.items())
            ],
            "previous_fatigue_by_employee": [
                {"employee": e, "value": v}
                for e, v in sorted(self.previous_fatigue_by_employee.items())
            ],
            "employee_ids": self.employee_ids,
        }

    def load_json_dict(self, data):
        self.num_employees = int(data["num_employees"])
        ids = data.get("employee_ids")

        if ids is None:
            ids = [str(uuid.uuid4()) for _ in range(self.num_employees)]

        if (
            not isinstance(ids, list)
            or len(ids) != self.num_employees
            or any(not isinstance(value, str) or not value for value in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("Identificativi dipendenti non validi.")

        self.employee_ids = ids
        self.month = int(data["month"])
        self.year = int(data["year"])
        self.desiderata_mode = str(data["desiderata_mode"])
        self.min_m = int(data["min_m"])
        self.min_p = int(data["min_p"])
        self.min_n = int(data["min_n"])
        self.seed = int(data.get("seed", 1))
        self.allow_elastic_coverage = bool(data.get("allow_elastic_coverage", True))

        self.names = normalize_names_list(data.get("names", []), self.num_employees)
        self.categories = list(data.get("categories", []))
        self.roles = list(data.get("roles", []))
        self.obiettore = list(data.get("obiettore", []))
        self.abil_triage = list(data.get("abil_triage", []))
        self.abil_salaop = list(data.get("abil_salaop", []))

        self.resize_employees(self.num_employees)

        self.form2_entries = {}
        for row in data.get("form2_entries", []):
            self.form2_entries[(int(row["employee"]), int(row["day"]))] = str(row["value"])

        self.figure_assignments = {}
        for row in data.get("figure_assignments", []):
            self.figure_assignments[(int(row["day"]), str(row["shift"]), str(row["figure"]))] = str(row["name"])

        self.output_overrides = {}
        for row in data.get("output_overrides", []):
            self.output_overrides[(int(row["employee"]), int(row["day"]))] = str(row["value"])

        self.ivg_by_week = {}
        for row in data.get("ivg_by_week", []):
            self.ivg_by_week[int(row["week_idx"])] = str(row["value"])

        self.previous_hours_by_employee = {}
        for row in data.get("previous_hours_by_employee", []):
            self.previous_hours_by_employee[int(row["employee"])] = float(row["value"])

        self.previous_fatigue_by_employee = {}
        for row in data.get("previous_fatigue_by_employee", []):
            self.previous_fatigue_by_employee[int(row["employee"])] = float(row["value"])

        self.ensure_ivg_defaults()