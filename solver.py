from constants import (
    SOLVER_SHIFTS,
    FIXED_ONLY_SHIFTS,
    ACTIVE_SHIFTS,
    HOURS,
    FATIGUE,
    WEIGHTS,
    MALATTIA_COVER_SHIFTS,
    OUTPUT_EDIT_VALUES,
)
from state import category_allowed_solver_shifts

from utils import (
    days_in_month,
    day_info_list,
    count_weekend_work,
    max_consecutive,
    max_consecutive_equals,
    is_active_shift,
    week_ranges_for_month,
)

try:
    from ortools.sat.python import cp_model
except Exception:
    cp_model = None


class ScheduleSolver:
    def __init__(self, state):
        self.state = state

    def normalize_shift_for_rules(self, shift):
        mapping = {
            "MCM": "M",
            "PCM": "P",
            "NCM": "N",
            "MAL": "A",
        }
        return mapping.get(shift, shift)

    def normalized_allowed_transition(self, prev_shift):
        prev_shift = self.normalize_shift_for_rules(prev_shift)
        if prev_shift == "N":
            return {"N", "S"}
        if prev_shift == "M":
            return {"M", "P", "N", "R", "Amb", "Ag"}
        if prev_shift == "P":
            return {"P", "N", "R", "Amb", "G"}
        if prev_shift == "R":
            return {"M", "R", "P", "N", "Amb", "G"}
        if prev_shift == "S":
            return {"M", "R", "P", "N", "Amb", "G"}
        if prev_shift in {"Amb", "G", "F", "A", "Pr", "Ag"}:
            return {"M", "P", "N", "R", "Amb", "G", "Ag"}
        return set(SOLVER_SHIFTS + FIXED_ONLY_SHIFTS)

    def allowed_transition(self, prev_shift):
        return self.normalized_allowed_transition(prev_shift)

    def normalized_shift_matches(self, shift_value, target):
        if shift_value is None:
            return False
        return self.normalize_shift_for_rules(shift_value) == target

    def count_day_resources(self):
        n_emp = self.state.num_employees
        n_days = days_in_month(self.state.year, self.state.month)
        rows = []

        for d in range(n_days):
            possible_m = possible_p = possible_n = 0
            cap_m = cap_p = cap_n = 0
            fixed_count = 0

            for e in range(n_emp):
                override = self.state.output_overrides.get((e, d), "")
                cell = self.state.form2_entries.get((e, d), "")
                allowed = category_allowed_solver_shifts(self.state.categories[e])
                is_capo = self.state.roles[e] == "CapoTurno"

                if override:
                    ov_norm = self.normalize_shift_for_rules(override)
                    if ov_norm == "M":
                        possible_m += 1
                        if is_capo:
                            cap_m += 1
                    elif ov_norm == "P":
                        possible_p += 1
                        if is_capo:
                            cap_p += 1
                    elif ov_norm == "N":
                        possible_n += 1
                        if is_capo:
                            cap_n += 1
                    fixed_count += 1
                    continue

                if cell in FIXED_ONLY_SHIFTS:
                    fixed_count += 1
                    continue
                if cell == "RD":
                    continue
                if cell == "MD":
                    possible_m += 1
                    if is_capo:
                        cap_m += 1
                    continue
                if cell == "PD":
                    possible_p += 1
                    if is_capo:
                        cap_p += 1
                    continue
                if cell == "ND":
                    possible_n += 1
                    if is_capo:
                        cap_n += 1
                    continue

                if "M" in allowed:
                    possible_m += 1
                    if is_capo:
                        cap_m += 1
                if "P" in allowed:
                    possible_p += 1
                    if is_capo:
                        cap_p += 1
                if "N" in allowed:
                    possible_n += 1
                    if is_capo:
                        cap_n += 1

            rows.append({
                "giorno": d + 1,
                "teorici_M": possible_m,
                "teorici_P": possible_p,
                "teorici_N": possible_n,
                "capo_M": cap_m,
                "capo_P": cap_p,
                "capo_N": cap_n,
                "fissi": fixed_count,
            })
        return rows

    def diagnostics_text(self):
        rows = self.count_day_resources()
        out = []
        for r in rows:
            flags = []
            if r["teorici_M"] < self.state.min_m:
                flags.append("M")
            if r["teorici_P"] < self.state.min_p:
                flags.append("P")
            if r["teorici_N"] < self.state.min_n:
                flags.append("N")
            if r["capo_M"] < 1:
                flags.append("CT-M")
            if r["capo_P"] < 1:
                flags.append("CT-P")
            if r["capo_N"] < 1:
                flags.append("CT-N")
            if flags:
                out.append(
                    f"G{r['giorno']}: teorici M/P/N={r['teorici_M']}/{r['teorici_P']}/{r['teorici_N']} "
                    f"| Capo M/P/N={r['capo_M']}/{r['capo_P']}/{r['capo_N']} "
                    f"| Fissi={r['fissi']} "
                    f"| Criticità={','.join(flags)}"
                )
        return "\n".join(out)


    def soft_preference_penalties(self, model, x, fixed, active, n_emp, n_days):
        penalties = []

        def is_shift(e, d, target):
            fd = fixed[(e, d)]
            if fd is not None:
                return 1 if self.normalize_shift_for_rules(fd) == target else 0
            return x[(e, d, target)]

        def is_s(e, d):
            fd = fixed[(e, d)]
            if fd is not None:
                return 1 if self.normalize_shift_for_rules(fd) == "S" else 0
            return x[(e, d, "S")]

        def is_r(e, d):
            fd = fixed[(e, d)]
            if fd is not None:
                return 1 if self.normalize_shift_for_rules(fd) == "R" else 0
            return x[(e, d, "R")]

        for e in range(n_emp):
            for d in range(n_days - 1):
                m_today = is_shift(e, d, "M")
                p_today = is_shift(e, d, "P")
                n_today = is_shift(e, d, "N")
                s_today = is_s(e, d)
                r_today = is_r(e, d)

                m_next = is_shift(e, d + 1, "M")
                p_next = is_shift(e, d + 1, "P")
                n_next = is_shift(e, d + 1, "N")
                s_next = is_s(e, d + 1)
                r_next = is_r(e, d + 1)

                mp_bad = model.NewIntVar(0, 1, f"soft_mp_bad_{e}_{d}")
                pn_bad = model.NewIntVar(0, 1, f"soft_pn_bad_{e}_{d}")
                ns_bad = model.NewIntVar(0, 1, f"soft_ns_bad_{e}_{d}")
                sr_bad = model.NewIntVar(0, 1, f"soft_sr_bad_{e}_{d}")
                rrm_bad = model.NewIntVar(0, 1, f"soft_rrm_bad_{e}_{d}")

                mm_bad = model.NewIntVar(0, 1, f"soft_mm_bad_{e}_{d}")
                pp_bad = model.NewIntVar(0, 1, f"soft_pp_bad_{e}_{d}")
                nn_bad = model.NewIntVar(0, 1, f"soft_nn_bad_{e}_{d}")

                model.Add(mp_bad >= m_today - p_next)
                model.Add(pn_bad >= p_today - n_next)
                model.Add(ns_bad >= n_today - s_next)
                model.Add(sr_bad >= s_today - r_next)

                model.Add(mm_bad >= m_today + m_next - 1)
                model.Add(pp_bad >= p_today + p_next - 1)
                model.Add(nn_bad >= n_today + n_next - 1)

                rm_or_rr = model.NewIntVar(0, 1, f"rm_or_rr_{e}_{d}")
                model.Add(rm_or_rr >= r_next)
                model.Add(rm_or_rr >= m_next)
                model.Add(rm_or_rr <= r_next + m_next)

                model.Add(rrm_bad >= r_today - rm_or_rr)

                penalties.append(mp_bad * WEIGHTS["soft_mp"])
                penalties.append(pn_bad * WEIGHTS["soft_pn"])
                penalties.append(ns_bad * WEIGHTS["soft_ns"])
                penalties.append(sr_bad * WEIGHTS["soft_sr"])
                penalties.append(rrm_bad * WEIGHTS["soft_rrm"])

                penalties.append(mm_bad * WEIGHTS["soft_mp"])
                penalties.append(pp_bad * WEIGHTS["soft_pn"])
                penalties.append(nn_bad * WEIGHTS["soft_ns"])

            for d in range(n_days - 5):
                window_sum = sum(active[(e, d + k)] for k in range(6))
                over5 = model.NewIntVar(0, 1, f"soft_over5_{e}_{d}")
                model.Add(window_sum <= 5 + over5)
                model.Add(window_sum >= 6 * over5)
                penalties.append(over5 * WEIGHTS["over5active"])

        return penalties
    
    def precheck(self):
        n_emp = self.state.num_employees
        n_days = days_in_month(self.state.year, self.state.month)
        problems = []
        warnings = []

        for (e, d), override in self.state.output_overrides.items():
            if override not in OUTPUT_EDIT_VALUES:
                problems.append(f"{self.state.names[e]} giorno {d+1}: override non valido {override}")

        for e in range(n_emp):
            allowed = category_allowed_solver_shifts(self.state.categories[e])
            for d in range(n_days):
                cell = self.state.form2_entries.get((e, d), "")
                if cell == "MD" and "M" not in allowed:
                    problems.append(f"{self.state.names[e]} giorno {d+1}: MD incompatibile con categoria {self.state.categories[e]}")
                elif cell == "PD" and "P" not in allowed:
                    problems.append(f"{self.state.names[e]} giorno {d+1}: PD incompatibile con categoria {self.state.categories[e]}")
                elif cell == "ND" and "N" not in allowed:
                    problems.append(f"{self.state.names[e]} giorno {d+1}: ND incompatibile con categoria {self.state.categories[e]}")
                elif cell == "RD" and "R" not in allowed:
                    problems.append(f"{self.state.names[e]} giorno {d+1}: RD incompatibile con categoria {self.state.categories[e]}")

        for d in range(n_days):
            for shift in ["M", "P", "N"]:
                possible = 0
                for e in range(n_emp):
                    if self.state.roles[e] != "CapoTurno":
                        continue
                    cell = self.state.form2_entries.get((e, d), "")
                    override = self.state.output_overrides.get((e, d), "")
                    allowed = category_allowed_solver_shifts(self.state.categories[e])

                    if override:
                        if self.normalized_shift_matches(override, shift):
                            possible += 1
                        continue

                    if cell in FIXED_ONLY_SHIFTS:
                        continue
                    if cell == "RD":
                        continue
                    if cell == "MD" and shift != "M":
                        continue
                    if cell == "PD" and shift != "P":
                        continue
                    if cell == "ND" and shift != "N":
                        continue
                    if shift in allowed or cell in {f"{shift}D"}:
                        possible += 1

                if possible < 1:
                    problems.append(f"Giorno {d+1}: nessuna CapoTurno teoricamente disponibile per turno {shift}")

        for d in range(n_days):
            possible_m = possible_p = possible_n = 0
            unavailable = 0

            for e in range(n_emp):
                override = self.state.output_overrides.get((e, d), "")
                cell = self.state.form2_entries.get((e, d), "")
                allowed = category_allowed_solver_shifts(self.state.categories[e])

                if override:
                    ov_norm = self.normalize_shift_for_rules(override)
                    if ov_norm == "M":
                        possible_m += 1
                    elif ov_norm == "P":
                        possible_p += 1
                    elif ov_norm == "N":
                        possible_n += 1
                    unavailable += 1 if self.normalize_shift_for_rules(override) not in {"M", "P", "N", "R", "S"} else 0
                    continue

                if cell in FIXED_ONLY_SHIFTS:
                    unavailable += 1
                    continue
                if cell == "MD":
                    possible_m += 1
                    continue
                if cell == "PD":
                    possible_p += 1
                    continue
                if cell == "ND":
                    possible_n += 1
                    continue
                if cell == "RD":
                    continue

                if "M" in allowed:
                    possible_m += 1
                if "P" in allowed:
                    possible_p += 1
                if "N" in allowed:
                    possible_n += 1

            if possible_m < self.state.min_m:
                msg = f"Giorno {d+1}: disponibilità teorica M insufficiente ({possible_m} < {self.state.min_m})"
                if self.state.allow_elastic_coverage:
                    warnings.append(msg)
                else:
                    problems.append(msg)

            if possible_p < self.state.min_p:
                msg = f"Giorno {d+1}: disponibilità teorica P insufficiente ({possible_p} < {self.state.min_p})"
                if self.state.allow_elastic_coverage:
                    warnings.append(msg)
                else:
                    problems.append(msg)

            if possible_n < self.state.min_n:
                msg = f"Giorno {d+1}: disponibilità teorica N insufficiente ({possible_n} < {self.state.min_n})"
                if self.state.allow_elastic_coverage:
                    warnings.append(msg)
                else:
                    problems.append(msg)

            max_active_possible = n_emp - unavailable
            if self.state.min_m + self.state.min_p + self.state.min_n > max_active_possible:
                msg = (
                    f"Giorno {d+1}: richieste minime M+P+N="
                    f"{self.state.min_m + self.state.min_p + self.state.min_n} "
                    f"superano i dipendenti teoricamente disponibili {max_active_possible}"
                )
                if self.state.allow_elastic_coverage:
                    warnings.append(msg)
                else:
                    problems.append(msg)

        if problems:
            raise ValueError("Precheck fallito:\n- " + "\n- ".join(problems))
        return warnings

    def solve(self):
        if cp_model is None:
            raise RuntimeError("OR-Tools non installato. Esegui: pip install ortools")

        warnings = self.precheck()

        n_emp = self.state.num_employees
        year = self.state.year
        month = self.state.month
        n_days = days_in_month(year, month)
        day_infos = day_info_list(year, month)

        model = cp_model.CpModel()
        x = {}
        fixed = {}

        for e in range(n_emp):
            allowed_cat = category_allowed_solver_shifts(self.state.categories[e])

            for d in range(n_days):
                override = self.state.output_overrides.get((e, d), "")
                cell = self.state.form2_entries.get((e, d), "")

                if override:
                    fixed[(e, d)] = override
                    allowed_here = set()
                elif cell in FIXED_ONLY_SHIFTS:
                    fixed[(e, d)] = cell
                    allowed_here = set()
                else:
                    fixed[(e, d)] = None
                    allowed_here = set(allowed_cat)

                for s in SOLVER_SHIFTS:
                    x[(e, d, s)] = model.NewBoolVar(f"x_{e}_{d}_{s}")
                    if s not in allowed_here:
                        model.Add(x[(e, d, s)] == 0)

                if fixed[(e, d)] is not None:
                    for s in SOLVER_SHIFTS:
                        model.Add(x[(e, d, s)] == 0)
                else:
                    model.Add(sum(x[(e, d, s)] for s in SOLVER_SHIFTS) == 1)


        shortage_m = {}
        shortage_p = {}
        shortage_n = {}

        for d in range(n_days):
            cover_m = sum(x[(e, d, "M")] for e in range(n_emp))
            cover_p = sum(x[(e, d, "P")] for e in range(n_emp))
            cover_n = sum(x[(e, d, "N")] for e in range(n_emp))

            fixed_cover_m = sum(
                1 for e in range(n_emp)
                if fixed[(e, d)] is not None and self.normalized_shift_matches(fixed[(e, d)], "M")
            )
            fixed_cover_p = sum(
                1 for e in range(n_emp)
                if fixed[(e, d)] is not None and self.normalized_shift_matches(fixed[(e, d)], "P")
            )
            fixed_cover_n = sum(
                1 for e in range(n_emp)
                if fixed[(e, d)] is not None and self.normalized_shift_matches(fixed[(e, d)], "N")
            )

            if self.state.allow_elastic_coverage:
                shortage_m[d] = model.NewIntVar(0, self.state.min_m, f"short_m_{d}")
                shortage_p[d] = model.NewIntVar(0, self.state.min_p, f"short_p_{d}")
                shortage_n[d] = model.NewIntVar(0, self.state.min_n, f"short_n_{d}")

                model.Add(cover_m + fixed_cover_m + shortage_m[d] >= self.state.min_m)
                model.Add(cover_p + fixed_cover_p + shortage_p[d] >= self.state.min_p)
                model.Add(cover_n + fixed_cover_n + shortage_n[d] >= self.state.min_n)
            else:
                model.Add(cover_m + fixed_cover_m >= self.state.min_m)
                model.Add(cover_p + fixed_cover_p >= self.state.min_p)
                model.Add(cover_n + fixed_cover_n >= self.state.min_n)

        desired_miss_vars = []

        for e in range(n_emp):
            for d in range(n_days):
                cell = self.state.form2_entries.get((e, d), "")
                override = self.state.output_overrides.get((e, d), "")

                if override:
                    continue

                if cell == "MD":
                    if self.state.desiderata_mode == "vincolo forte":
                        model.Add(x[(e, d, "M")] == 1)
                    else:
                        miss = model.NewBoolVar(f"miss_MD_{e}_{d}")
                        model.Add(x[(e, d, "M")] + miss == 1)
                        desired_miss_vars.append(miss)

                elif cell == "PD":
                    if self.state.desiderata_mode == "vincolo forte":
                        model.Add(x[(e, d, "P")] == 1)
                    else:
                        miss = model.NewBoolVar(f"miss_PD_{e}_{d}")
                        model.Add(x[(e, d, "P")] + miss == 1)
                        desired_miss_vars.append(miss)

                elif cell == "ND":
                    if self.state.desiderata_mode == "vincolo forte":
                        model.Add(x[(e, d, "N")] == 1)
                    else:
                        miss = model.NewBoolVar(f"miss_ND_{e}_{d}")
                        model.Add(x[(e, d, "N")] + miss == 1)
                        desired_miss_vars.append(miss)

                elif cell == "RD":
                    if self.state.desiderata_mode == "vincolo forte":
                        model.Add(x[(e, d, "R")] == 1)
                    else:
                        miss = model.NewBoolVar(f"miss_RD_{e}_{d}")
                        model.Add(x[(e, d, "R")] + miss == 1)
                        desired_miss_vars.append(miss)

        active = {}
        for e in range(n_emp):
            for d in range(n_days):
                if fixed[(e, d)] is not None:
                    active[(e, d)] = 1 if fixed[(e, d)] in ACTIVE_SHIFTS else 0
                else:
                    a = model.NewBoolVar(f"active_{e}_{d}")
                    model.Add(a == x[(e, d, "M")] + x[(e, d, "P")] + x[(e, d, "N")])
                    active[(e, d)] = a

        for e in range(n_emp):
            for d in range(n_days - 1):
                today_fixed = fixed[(e, d)]
                next_fixed = fixed[(e, d + 1)]

                if today_fixed is None:
                    model.Add(x[(e, d + 1, "N")] + x[(e, d + 1, "S")] >= x[(e, d, "N")])

                    if next_fixed is None:
                        model.Add(sum(x[(e, d + 1, s)] for s in ["M", "P", "N", "R"]) >= x[(e, d, "M")])
                    elif self.normalize_shift_for_rules(next_fixed) not in {"Amb", "Ag"}:
                        model.Add(x[(e, d, "M")] == 0)

                    if next_fixed is None:
                        model.Add(sum(x[(e, d + 1, s)] for s in ["P", "N", "R"]) >= x[(e, d, "P")])
                    elif self.normalize_shift_for_rules(next_fixed) not in {"Amb", "G"}:
                        model.Add(x[(e, d, "P")] == 0)

                    if next_fixed is None:
                        model.Add(sum(x[(e, d + 1, s)] for s in ["M", "R", "P", "N"]) >= x[(e, d, "R")])
                    elif self.normalize_shift_for_rules(next_fixed) not in {"Amb", "G"}:
                        model.Add(x[(e, d, "R")] == 0)

                    if next_fixed is None:
                        model.Add(sum(x[(e, d + 1, s)] for s in ["M", "R", "P", "N"]) >= x[(e, d, "S")])
                    elif self.normalize_shift_for_rules(next_fixed) not in {"Amb", "G"}:
                        model.Add(x[(e, d, "S")] == 0)

                else:
                    allowed = self.allowed_transition(today_fixed)
                    if next_fixed is not None:
                        if self.normalize_shift_for_rules(next_fixed) not in allowed:
                            raise ValueError(
                                f"Transizione fissa non ammessa per {self.state.names[e]}: "
                                f"giorno {d+1} {today_fixed} -> giorno {d+2} {next_fixed}"
                            )
                    else:
                        allowed_solver = [s for s in SOLVER_SHIFTS if s in allowed]
                        for s in SOLVER_SHIFTS:
                            if s not in allowed_solver:
                                model.Add(x[(e, d + 1, s)] == 0)

        for e in range(n_emp):
            for d in range(n_days - 2):
                vals = []
                for k in range(3):
                    fd = fixed[(e, d + k)]
                    if fd is not None:
                        vals.append(1 if self.normalize_shift_for_rules(fd) == "N" else 0)
                    else:
                        vals.append(x[(e, d + k, "N")])
                model.Add(sum(vals) <= 2)

        for e in range(n_emp):
            for d in range(n_days - 6):
                seq = [active[(e, d + k)] for k in range(7)]
                model.Add(sum(seq) <= 6)

        capoturno_idx = [e for e in range(n_emp) if self.state.roles[e] == "CapoTurno"]
        for d in range(n_days):
            for shift in ["M", "P", "N"]:
                total_cap = []
                for e in capoturno_idx:
                    if fixed[(e, d)] is not None:
                        total_cap.append(1 if self.normalized_shift_matches(fixed[(e, d)], shift) else 0)
                    else:
                        total_cap.append(x[(e, d, shift)])
                model.Add(sum(total_cap) >= 1)

        emp_hours = []
        scale_hours = 2
        scaled_hours = {k: int(v * scale_hours) for k, v in HOURS.items()}

        for e in range(n_emp):
            prev_h = int(self.state.previous_hours_by_employee.get(e, 0.0) * scale_hours)
            h = model.NewIntVar(0, 200000, f"h_{e}")
            parts = [prev_h]

            for d in range(n_days):
                info = day_infos[d]
                if fixed[(e, d)] is not None:
                    fx = fixed[(e, d)]
                    if fx in scaled_hours:
                        parts.append(scaled_hours[fx])
                        if info["is_holiday"] and fx in ACTIVE_SHIFTS:
                            parts.append(int(8 * scale_hours))
                    else:
                        parts.append(0)
                else:
                    for s in SOLVER_SHIFTS:
                        parts.append(scaled_hours[s] * x[(e, d, s)])
                    if info["is_holiday"]:
                        parts.append(int(8 * scale_hours) * (x[(e, d, "M")] + x[(e, d, "P")] + x[(e, d, "N")]))

            model.Add(h == sum(parts))
            emp_hours.append(h)

        emp_fatigue = []

        approx_fatigue_points = {
            "M": 2,
            "P": 2,
            "N": 3,
            "R": -1,
            "S": 0,
            "Amb": 2,
            "G": 1,
            "F": 0,
            "A": 0,
            "Pr": 0,
            "Ag": 0,
            "MCM": 3,
            "PCM": 3,
            "NCM": 4,
            "MAL": 0,
        }

        fatigue_shift_cost = {k: v + 5 for k, v in approx_fatigue_points.items()}

        for e in range(n_emp):
            prev_f = int(self.state.previous_fatigue_by_employee.get(e, 0.0))
            f = model.NewIntVar(0, 200000, f"fat_{e}")
            parts = [prev_f]

            for d in range(n_days):
                info = day_infos[d]

                if fixed[(e, d)] is not None:
                    fx = fixed[(e, d)]
                    parts.append(fatigue_shift_cost.get(fx, 5))
                    if fx in ACTIVE_SHIFTS:
                        if info["is_saturday"]:
                            parts.append(1)
                        if info["is_sunday"]:
                            parts.append(1)
                        if info["is_holiday"]:
                            parts.append(1)
                else:
                    parts.append(fatigue_shift_cost["M"] * x[(e, d, "M")])
                    parts.append(fatigue_shift_cost["P"] * x[(e, d, "P")])
                    parts.append(fatigue_shift_cost["N"] * x[(e, d, "N")])
                    parts.append(fatigue_shift_cost["R"] * x[(e, d, "R")])
                    parts.append(fatigue_shift_cost["S"] * x[(e, d, "S")])

                    if info["is_saturday"]:
                        parts.append(x[(e, d, "M")] + x[(e, d, "P")] + x[(e, d, "N")])
                    if info["is_sunday"]:
                        parts.append(x[(e, d, "M")] + x[(e, d, "P")] + x[(e, d, "N")])
                    if info["is_holiday"]:
                        parts.append(x[(e, d, "M")] + x[(e, d, "P")] + x[(e, d, "N")])

            model.Add(f == sum(parts))
            emp_fatigue.append(f)

        max_h = model.NewIntVar(0, 200000, "max_h")
        min_h = model.NewIntVar(0, 200000, "min_h")
        gap_h = model.NewIntVar(0, 200000, "gap_h")
        model.AddMaxEquality(max_h, emp_hours)
        model.AddMinEquality(min_h, emp_hours)
        model.Add(gap_h == max_h - min_h)

        max_f = model.NewIntVar(0, 200000, "max_f")
        min_f = model.NewIntVar(0, 200000, "min_f")
        gap_f = model.NewIntVar(0, 200000, "gap_f")
        model.AddMaxEquality(max_f, emp_fatigue)
        model.AddMinEquality(min_f, emp_fatigue)
        model.Add(gap_f == max_f - min_f)

        emp_weekend = []
        for e in range(n_emp):
            wv = model.NewIntVar(0, n_days, f"weekend_{e}")
            parts = []
            for d in range(n_days):
                info = day_infos[d]
                weekend_day = info["is_saturday"] or info["is_sunday"]
                if not weekend_day:
                    continue

                if fixed[(e, d)] is not None:
                    parts.append(1 if fixed[(e, d)] in ACTIVE_SHIFTS else 0)
                else:
                    parts.append(x[(e, d, "M")] + x[(e, d, "P")] + x[(e, d, "N")])
            model.Add(wv == (sum(parts) if parts else 0))
            emp_weekend.append(wv)

        max_w = model.NewIntVar(0, n_days, "max_weekend")
        min_w = model.NewIntVar(0, n_days, "min_weekend")
        gap_w = model.NewIntVar(0, n_days, "gap_weekend")
        model.AddMaxEquality(max_w, emp_weekend)
        model.AddMinEquality(min_w, emp_weekend)
        model.Add(gap_w == max_w - min_w)

        objective_terms = [
            gap_h * WEIGHTS["gap_hours"],
            gap_f * WEIGHTS["gap_fatigue"],
            gap_w * WEIGHTS["gap_weekend"],
        ]

        objective_terms.extend(
            self.soft_preference_penalties(model, x, fixed, active, n_emp, n_days)
        )

        if self.state.allow_elastic_coverage:
            for d in range(n_days):
                objective_terms.append(shortage_m[d] * WEIGHTS["cover_shortage_M"])
                objective_terms.append(shortage_p[d] * WEIGHTS["cover_shortage_P"])
                objective_terms.append(shortage_n[d] * WEIGHTS["cover_shortage_N"])

        for miss in desired_miss_vars:
            objective_terms.append(miss * WEIGHTS["desired_miss"])

        model.Minimize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.random_seed = self.state.seed
        solver.parameters.max_time_in_seconds = 50
        solver.parameters.num_search_workers = 8

        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            status_name = {
                cp_model.UNKNOWN: "UNKNOWN",
                cp_model.MODEL_INVALID: "MODEL_INVALID",
                cp_model.INFEASIBLE: "INFEASIBLE",
            }.get(status, str(status))

            diag = self.diagnostics_text()
            detail = f"\n\nDiagnostica:\n{diag}" if diag else ""

            raise RuntimeError(
                f"Nessuna soluzione trovata ({status_name}).\n\n"
                "Controlla questi punti:\n"
                "- minimi M/P/N troppo alti\n"
                "- numero di CapoTurno insufficiente per coprire M, P e N ogni giorno\n"
                "- troppe assegnazioni fisse in FORM2 (Amb, G, F, A, Pr, Ag)\n"
                "- desiderata forti incompatibili con categorie o vincoli\n"
                "- categorie troppo restrittive\n"
                "- override manuali incompatibili\n"
                "- transizioni non ammesse tra giorni consecutivi"
                f"{detail}"
            )

        schedule = []
        for e in range(n_emp):
            row = []
            for d in range(n_days):
                if fixed[(e, d)] is not None:
                    row.append(fixed[(e, d)])
                else:
                    chosen = None
                    for s in SOLVER_SHIFTS:
                        if solver.Value(x[(e, d, s)]) == 1:
                            chosen = s
                            break
                    row.append(chosen if chosen else "?")
            schedule.append(row)

        summary, stats = self.compute_stats(schedule, day_infos)
        figure_stats = self.compute_figure_stats(schedule)

        shortages = []
        if self.state.allow_elastic_coverage:
            for d in range(n_days):
                shortages.append({
                    "giorno": d + 1,
                    "shortage_M": solver.Value(shortage_m[d]),
                    "shortage_P": solver.Value(shortage_p[d]),
                    "shortage_N": solver.Value(shortage_n[d]),
                })

        violations = self.verify(schedule, day_infos, shortages)

        return {
            "schedule": schedule,
            "summary": summary,
            "stats": stats,
            "figure_stats": figure_stats,
            "day_infos": day_infos,
            "seed": self.state.seed,
            "year": year,
            "month": month,
            "violations": violations,
            "precheck_warnings": warnings,
            "coverage_shortages": shortages,
            "diagnostics": self.count_day_resources(),
        }

    def compute_stats(self, schedule, day_infos):
        summary = []
        stats = []

        for e, row in enumerate(schedule):
            active_count = sum(1 for s in row if s in ACTIVE_SHIFTS)
            cM = sum(1 for s in row if s in {"M", "MCM"})
            cP = sum(1 for s in row if s in {"P", "PCM"})
            cN = sum(1 for s in row if s in {"N", "NCM"})

            req_rest = 0
            sat_rest = 0
            req_turn = 0
            sat_turn = 0
            fatigue = float(self.state.previous_fatigue_by_employee.get(e, 0.0))
            hours = 0.0
            desired_penalty = 0
            prev = "R"

            for d, s in enumerate(row):
                cell = self.state.form2_entries.get((e, d), "")

                if cell == "RD":
                    req_rest += 1
                    if self.normalize_shift_for_rules(s) == "R":
                        sat_rest += 1
                    elif self.state.desiderata_mode == "vincolo debole" and not self.state.output_overrides.get((e, d), ""):
                        desired_penalty += 1

                elif cell in {"MD", "PD", "ND"}:
                    req_turn += 1
                    exp = {"MD": "M", "PD": "P", "ND": "N"}[cell]
                    if self.normalize_shift_for_rules(s) == exp:
                        sat_turn += 1
                    elif self.state.desiderata_mode == "vincolo debole" and not self.state.output_overrides.get((e, d), ""):
                        desired_penalty += 1

                prev_norm = self.normalize_shift_for_rules(prev)
                s_norm = self.normalize_shift_for_rules(s)
                val = FATIGUE.get(prev_norm, {}).get(s_norm, 0)
                if val is None:
                    val = 0
                fatigue += val

                if s in {"MCM", "PCM", "NCM"}:
                    fatigue += 1

                info = day_infos[d]
                if is_active_shift(s):
                    if info["is_saturday"]:
                        fatigue += 1
                    if info["is_sunday"]:
                        fatigue += 1
                    if info["is_holiday"]:
                        fatigue += 1

                hours += HOURS[s]
                if info["is_holiday"] and is_active_shift(s):
                    hours += 8.0

                prev = s

            fatigue += desired_penalty

            summary.append({
                "dipendente": self.state.names[e],
                "turni_attivi": active_count,
                "M": cM,
                "P": cP,
                "N": cN,
                "riposi_desiderati_soddisfatti": sat_rest,
                "riposi_desiderati_richiesti": req_rest,
                "turni_desiderati_soddisfatti": sat_turn,
                "turni_desiderati_richiesti": req_turn,
                "punti_stanchezza": round(fatigue, 1),
                "ore_lavorate": round(hours + self.state.previous_hours_by_employee.get(e, 0.0), 1),
            })

            weekend, saturday, sunday = count_weekend_work(row, day_infos)
            stats.append({
                "dipendente": self.state.names[e],
                "weekend_lavorati": weekend,
                "sabati_lavorati": saturday,
                "domeniche_lavorate": sunday,
                "max_turni_attivi_consecutivi": max_consecutive(row, ACTIVE_SHIFTS),
                "max_notti_consecutive": max_consecutive_equals([self.normalize_shift_for_rules(x) for x in row], "N"),
            })

        return summary, stats

    def compute_figure_stats(self, schedule):
        out = []
        by_name = {name: {"capoturnazioni": 0, "sala_operatoria": 0, "triage": 0, "jolly": 0} for name in self.state.names}

        day_infos = self.state.solution["day_infos"] if self.state.solution else day_info_list(self.state.year, self.state.month)

        for d, _ in enumerate(day_infos):
            for shift in ["M", "P", "N"]:
                capo = self.state.figure_assignments.get((d, shift, "Capoturnazione"), "")
                sala = self.state.figure_assignments.get((d, shift, "SalaOperatoria"), "")
                tri = self.state.figure_assignments.get((d, shift, "Triage"), "")

                if capo in by_name:
                    by_name[capo]["capoturnazioni"] += 1
                if sala in by_name:
                    by_name[sala]["sala_operatoria"] += 1
                if tri in by_name:
                    by_name[tri]["triage"] += 1

                jolly_names = self.compute_jolly_names(schedule, d, shift, {capo, sala, tri})
                for n in jolly_names:
                    if n in by_name:
                        by_name[n]["jolly"] += 1

        for name in self.state.names:
            row = {"dipendente": name}
            row.update(by_name[name])
            out.append(row)
        return out

    def compute_jolly_names(self, schedule, day_idx, shift, assigned_set):
        assigned = set(x for x in assigned_set if x)
        out = []
        for e, row in enumerate(schedule):
            if row[day_idx] == shift and self.state.names[e] not in assigned:
                out.append(self.state.names[e])
        return out

    def verify(self, schedule, day_infos, shortages=None):
        hard = []
        soft = []

        for d in range(len(day_infos)):
            cM = sum(1 for e in range(len(schedule)) if self.normalize_shift_for_rules(schedule[e][d]) == "M")
            cP = sum(1 for e in range(len(schedule)) if self.normalize_shift_for_rules(schedule[e][d]) == "P")
            cN = sum(1 for e in range(len(schedule)) if self.normalize_shift_for_rules(schedule[e][d]) == "N")

            if cM < self.state.min_m:
                hard.append(f"Giorno {d+1}: copertura M insufficiente ({cM} < {self.state.min_m})")
            if cP < self.state.min_p:
                hard.append(f"Giorno {d+1}: copertura P insufficiente ({cP} < {self.state.min_p})")
            if cN < self.state.min_n:
                hard.append(f"Giorno {d+1}: copertura N insufficiente ({cN} < {self.state.min_n})")

            if sum(1 for e in range(len(schedule)) if self.state.roles[e] == "CapoTurno" and self.normalize_shift_for_rules(schedule[e][d]) == "M") < 1:
                hard.append(f"Giorno {d+1}: manca una CapoTurno nel turno M")
            if sum(1 for e in range(len(schedule)) if self.state.roles[e] == "CapoTurno" and self.normalize_shift_for_rules(schedule[e][d]) == "P") < 1:
                hard.append(f"Giorno {d+1}: manca una CapoTurno nel turno P")
            if sum(1 for e in range(len(schedule)) if self.state.roles[e] == "CapoTurno" and self.normalize_shift_for_rules(schedule[e][d]) == "N") < 1:
                hard.append(f"Giorno {d+1}: manca una CapoTurno nel turno N")

        for e, row in enumerate(schedule):
            name = self.state.names[e]
            allowed = category_allowed_solver_shifts(self.state.categories[e]).union(FIXED_ONLY_SHIFTS).union(MALATTIA_COVER_SHIFTS)

            for d, s in enumerate(row):
                if s not in allowed and (e, d) not in self.state.output_overrides:
                    hard.append(f"{name} giorno {d+1}: turno {s} non ammesso per categoria {self.state.categories[e]}")

            for d in range(len(row) - 1):
                a = row[d]
                b = row[d + 1]
                if self.normalize_shift_for_rules(b) not in self.allowed_transition(a):
                    hard.append(f"{name} giorno {d+1}->{d+2}: transizione forte non ammessa {a}->{b}")

            for d in range(len(row) - 2):
                if (
                    self.normalize_shift_for_rules(row[d]) == "N"
                    and self.normalize_shift_for_rules(row[d+1]) == "N"
                    and self.normalize_shift_for_rules(row[d+2]) == "N"
                ):
                    hard.append(f"{name} giorni {d+1}-{d+3}: più di 2 N consecutive")

            for d in range(len(row) - 6):
                if sum(1 for k in range(7) if row[d+k] in ACTIVE_SHIFTS) == 7:
                    hard.append(f"{name} giorni {d+1}-{d+7}: più di 6 attivi consecutivi")

        if shortages:
            for row in shortages:
                if row["shortage_M"] > 0:
                    soft.append(f"Giorno {row['giorno']}: shortage M = {row['shortage_M']}")
                if row["shortage_P"] > 0:
                    soft.append(f"Giorno {row['giorno']}: shortage P = {row['shortage_P']}")
                if row["shortage_N"] > 0:
                    soft.append(f"Giorno {row['giorno']}: shortage N = {row['shortage_N']}")

        return {"hard": hard, "soft": soft}