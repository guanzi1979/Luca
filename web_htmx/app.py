import calendar
import csv
import io
import json
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from constants import (
    APP_TITLE,
    TUTORIAL_TEXT,
    CATEGORY_OPTIONS,
    ROLE_OPTIONS,
    IVG_OPTIONS,
    FORM2_VALUES,
    OUTPUT_EDIT_VALUES,
    SHIFT_COLORS,
    OVERRIDE_COLOR,
)
from persistence import load_autosave, save_autosave
from solver import ScheduleSolver
from state import should_prefill_by_category, prefill_value_for_category
from utils import (
    parse_names,
    day_info_list,
    week_ranges_for_month,
    days_in_month,
)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.exception_handler(Exception)
async def server_exception_handler(request: Request, exc: Exception):
    import logging

    logging.getLogger(__name__).error(
        "Errore durante la richiesta %s",
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

    return Response(
        content="Errore interno. Contatta l'amministratore.",
        status_code=500,
        media_type="text/plain",
    )


def get_state():
    if not hasattr(app.state, "shared_state"):
        state = load_autosave()
        state.ensure_ivg_defaults()
        app.state.shared_state = state
    return app.state.shared_state


def save_state(state):
    save_autosave(state)


def invalidate_solution(state):
    state.solution = None
    state.violations = {"hard": [], "soft": []}


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def htmx_redirect(url: str):
    return Response(status_code=200, headers={"HX-Redirect": url})


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def view_state(state):
    return {
        "num_employees": state.num_employees,
        "month": state.month,
        "year": state.year,
        "seed": state.seed,
        "desiderata_mode": state.desiderata_mode,
        "min_m": state.min_m,
        "min_p": state.min_p,
        "min_n": state.min_n,
        "allow_elastic_coverage": state.allow_elastic_coverage,
        "names": state.names,
        "categories": state.categories,
        "roles": state.roles,
        "obiettore": state.obiettore,
        "abil_triage": state.abil_triage,
        "abil_salaop": state.abil_salaop,
        "solution": state.solution,
        "violations": state.violations,
        "output_overrides": state.output_overrides,
        "figure_assignments": state.figure_assignments,
        "ivg_by_week": state.ivg_by_week,
    }


def ctx(request: Request, **kwargs):
    state = get_state()
    data = {
        "request": request,
        "app_title": APP_TITLE,
        "state": view_state(state),
    }
    data.update(kwargs)
    return data


def render_template(name: str, context: dict):
    request = context.get("request")
    if request is None:
        raise RuntimeError("request mancante nel context")
    return templates.TemplateResponse(request, name, context)

def redirect_to(path: str, msg: str | None = None, err: str | None = None):
    params = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    if params:
        path = f"{path}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(path, status_code=303)


def render_flash(request: Request, msg=None, err=None, warn_list=None):
    return render_template(
        "partials/flash.html",
        ctx(request, msg=msg, err=err, warn_list=warn_list or []),
    )


def build_dict_table(rows):
    return {
        "cols": list(rows[0].keys()) if rows else [],
        "rows": rows,
    }


def build_form2_rows(state):
    infos = day_info_list(state.year, state.month)
    rows = []
    for e, name in enumerate(state.names):
        cells = []
        for d, info in enumerate(infos):
            cells.append({
                "name": f"c_{e}_{d}",
                "value": state.form2_entries.get((e, d), ""),
                "header_label": info["header_label"],
            })
        rows.append({
            "label": f"{name} [{state.roles[e]}]",
            "cells": cells,
        })
    return infos, rows


def parse_form2_entries(form, state):
    n_days = days_in_month(state.year, state.month)
    entries = {}

    for e in range(state.num_employees):
        desired = 0
        restd = 0

        for d in range(n_days):
            value = clean_text(form.get(f"c_{e}_{d}", ""))

            if value not in FORM2_VALUES:
                raise ValueError(f"{state.names[e]} giorno {d+1}: valore non valido '{value}'")

            if value in {"MD", "PD", "ND"}:
                desired += 1
            elif value == "RD":
                restd += 1

            if value:
                entries[(e, d)] = value

        if desired > 3:
            raise ValueError(f"{state.names[e]} ha più di 3 turni desiderati")
        if restd > 2:
            raise ValueError(f"{state.names[e]} ha più di 2 riposi desiderati")

    return entries


def apply_form2_entries(state, entries):
    state.form2_entries = entries
    invalidate_solution(state)
    save_state(state)


def build_schedule_rows(state):
    if not state.solution:
        return []

    rows = []
    for e, row in enumerate(state.solution["schedule"]):
        summary = state.solution["summary"][e]
        cells = []
        for d, val in enumerate(row):
            bg = OVERRIDE_COLOR if (e, d) in state.output_overrides else SHIFT_COLORS.get(val, "white")
            cells.append({
                "value": val,
                "bg": bg,
            })

        rows.append({
            "label": f"{state.names[e]} [{state.roles[e]}]",
            "cells": cells,
            "fatigue": summary["punti_stanchezza"],
            "hours": summary["ore_lavorate"],
        })
    return rows


def figure_candidates(state, day_idx, shift, figure, capo_name=""):
    if not state.solution:
        return []

    weeks = week_ranges_for_month(state.year, state.month)
    ivg_day = None

    if shift == "M":
        for w, (start, end) in enumerate(weeks):
            if start <= day_idx <= end:
                ivg_day = state.get_ivg_day_index_for_week(w)
                break

    out = []
    for e, row in enumerate(state.solution["schedule"]):
        if row[day_idx] != shift:
            continue

        name = state.names[e]
        role = state.roles[e]
        tri = state.abil_triage[e]
        so = state.abil_salaop[e]
        obj = state.obiettore[e]

        if figure == "Capoturnazione":
            if role == "CapoTurno":
                out.append(name)

        elif figure == "SalaOperatoria":
            if name == capo_name:
                continue
            if so != "SI":
                continue
            if shift == "M" and ivg_day == day_idx and obj == "SI":
                continue
            out.append(name)

        elif figure == "Triage":
            if name == capo_name:
                continue
            if tri == "SI":
                out.append(name)

    return out


def jolly_names(state, day_idx, shift, assigned):
    if not state.solution:
        return []

    assigned = {x for x in assigned if x}
    out = []
    for e, row in enumerate(state.solution["schedule"]):
        if row[day_idx] == shift and state.names[e] not in assigned:
            out.append(state.names[e])
    return out


def build_figure_rows(state):
    if not state.solution:
        return []

    weeks = week_ranges_for_month(state.year, state.month)
    ivg_days = set()
    for w, _ in enumerate(weeks):
        d = state.get_ivg_day_index_for_week(w)
        if d is not None:
            ivg_days.add(d)

    rows = []
    for d, info in enumerate(state.solution["day_infos"]):
        for shift in ["M", "P", "N"]:
            capo = state.figure_assignments.get((d, shift, "Capoturnazione"), "")
            sala = state.figure_assignments.get((d, shift, "SalaOperatoria"), "")
            tri = state.figure_assignments.get((d, shift, "Triage"), "")

            rows.append({
                "day_idx": d,
                "day_label": info["header_label"],
                "shift": shift,
                "note": "IVG: obiettori esclusi da SalaOperatoria" if shift == "M" and d in ivg_days else "",
                "capo": capo,
                "sala": sala,
                "tri": tri,
                "capo_options": [""] + figure_candidates(state, d, shift, "Capoturnazione"),
                "sala_options": [""] + [x for x in figure_candidates(state, d, shift, "SalaOperatoria", capo) if x != capo],
                "tri_options": [""] + [x for x in figure_candidates(state, d, shift, "Triage", capo) if x not in {capo, sala}],
                "jolly": ", ".join(jolly_names(state, d, shift, {capo, sala, tri})),
            })
    return rows


def render_output_root(request: Request, msg=None, err=None):
    state = get_state()
    sol = state.solution

    return render_template(
        "partials/output_root.html",
        ctx(
            request,
            msg=msg,
            err=err,
            has_solution=bool(sol),
            day_headers=[info["header_label"] for info in sol["day_infos"]] if sol else [],
            schedule_rows=build_schedule_rows(state),
            summary_table=build_dict_table(sol["summary"]) if sol else build_dict_table([]),
            stats_table=build_dict_table(sol["stats"]) if sol else build_dict_table([]),
            figures_table=build_dict_table(sol.get("figure_stats", [])) if sol else build_dict_table([]),
            diagnostics_table=build_dict_table(sol.get("diagnostics", [])) if sol else build_dict_table([]),
            shortages_table=build_dict_table(sol.get("coverage_shortages", [])) if sol else build_dict_table([]),
            employee_choices=[{"idx": i, "label": n} for i, n in enumerate(state.names)],
            day_choices=[{"idx": i, "label": info["header_label"]} for i, info in enumerate(sol["day_infos"])] if sol else [],
            output_values=OUTPUT_EDIT_VALUES,
        ),
    )


def build_csv_export(state):
    if not state.solution:
        return b""

    sol = state.solution
    buffer = io.StringIO()
    w = csv.writer(buffer, delimiter=";")

    w.writerow(["TABELLA TURNI"])
    headers = ["Dipendente"] + [info["header_label"] for info in sol["day_infos"]] + ["Stanchezza", "Ore"]
    w.writerow(headers)

    for e, row in enumerate(sol["schedule"]):
        summary = sol["summary"][e]
        w.writerow([state.names[e]] + row + [summary["punti_stanchezza"], summary["ore_lavorate"]])

    w.writerow([])
    w.writerow(["RIEPILOGO"])
    if sol["summary"]:
        cols = list(sol["summary"][0].keys())
        w.writerow(cols)
        for row in sol["summary"]:
            w.writerow([row[c] for c in cols])

    w.writerow([])
    w.writerow(["STATISTICHE"])
    if sol["stats"]:
        cols = list(sol["stats"][0].keys())
        w.writerow(cols)
        for row in sol["stats"]:
            w.writerow([row[c] for c in cols])

    w.writerow([])
    w.writerow(["STATISTICHE FIGURE"])
    if sol.get("figure_stats"):
        cols = list(sol["figure_stats"][0].keys())
        w.writerow(cols)
        for row in sol["figure_stats"]:
            w.writerow([row[c] for c in cols])

    w.writerow([])
    w.writerow(["SHORTAGE COPERTURE"])
    if sol.get("coverage_shortages"):
        cols = list(sol["coverage_shortages"][0].keys())
        w.writerow(cols)
        for row in sol["coverage_shortages"]:
            w.writerow([row[c] for c in cols])

    w.writerow([])
    w.writerow(["VIOLAZIONI FORTI"])
    for item in sol["violations"].get("hard", []):
        w.writerow([item])

    w.writerow([])
    w.writerow(["VIOLAZIONI DEBOLI"])
    for item in sol["violations"].get("soft", []):
        w.writerow([item])

    return buffer.getvalue().encode("utf-8-sig")

async def get_form(request: Request):
    return await request.form(max_fields=10000)


def build_form2_grid_payload(state):
    infos = day_info_list(state.year, state.month)
    columns = [
        {
            "field": "employeeLabel",
            "headerName": "Dipendente",
            "pinned": "left",
            "editable": False,
            "width": 220,
            "lockPinned": True,
        },
        {
            "field": "employeeIndex",
            "headerName": "employeeIndex",
            "hide": True,
        },
        {
            "field": "category",
            "headerName": "category",
            "hide": True,
        },
        {
            "field": "role",
            "headerName": "role",
            "hide": True,
        },
        {
            "field": "prefilledCategory",
            "headerName": "prefilledCategory",
            "hide": True,
        },
    ]

    for d, info in enumerate(infos):
        header_class = ""
        if info["is_holiday"]:
            header_class = "day-hol"
        elif info["is_sunday"]:
            header_class = "day-sun"
        elif info["is_saturday"]:
            header_class = "day-sat"

        columns.append({
            "field": f"d_{d}",
            "headerName": info["header_label"],
            "editable": True,
            "width": 88,
            "cellEditor": "agSelectCellEditor",
            "cellEditorParams": {"values": FORM2_VALUES},
            "headerClass": header_class,
        })

    rows = []
    for e, name in enumerate(state.names):
        row = {
            "employeeIndex": e,
            "employeeLabel": f"{name} [{state.roles[e]}]",
            "category": state.categories[e],
            "role": state.roles[e],
            "prefilledCategory": should_prefill_by_category(state.categories[e]),
        }
        for d in range(len(infos)):
            row[f"d_{d}"] = state.form2_entries.get((e, d), "")
        rows.append(row)

    return {
        "columns": columns,
        "rows": rows,
        "allowedValues": FORM2_VALUES,
        "numDays": len(infos),
    }

def parse_form2_grid_rows(rows, state):
    n_days = days_in_month(state.year, state.month)
    entries = {}

    for row in rows:
        e = int(row["employeeIndex"])
        desired = 0
        restd = 0

        for d in range(n_days):
            key = f"d_{d}"
            value = clean_text(row.get(key, ""))

            if value not in FORM2_VALUES:
                raise ValueError(f"{state.names[e]} giorno {d+1}: valore non valido '{value}'")

            if value in {"MD", "PD", "ND"}:
                desired += 1
            elif value == "RD":
                restd += 1

            if value:
                entries[(e, d)] = value

        if desired > 3:
            raise ValueError(f"{state.names[e]} ha più di 3 turni desiderati")
        if restd > 2:
            raise ValueError(f"{state.names[e]} ha più di 2 riposi desiderati")

    return entries


@app.get("/")
async def home(request: Request):
    user = request.state.user
    destination = "/config" if user["role"] == "admin" else "/employee"
    return RedirectResponse(destination, status_code=303)

@app.get("/config")
async def config_page(request: Request):
    return render_template(
        "config.html",
        ctx(
            request,
            title="Configurazione",
            tutorial_text=TUTORIAL_TEXT,
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )


@app.post("/config/save")
async def config_save(request: Request):
    state = get_state()
    form = await request.form(max_fields=10000)

    try:
        n = int(clean_text(form.get("num_employees", state.num_employees)))
        names = parse_names(clean_text(form.get("names", "")), n)

        state.set_names_preserve(names, n)
        state.month = int(clean_text(form.get("month", state.month)))
        state.year = int(clean_text(form.get("year", state.year)))
        state.desiderata_mode = clean_text(form.get("desiderata_mode", state.desiderata_mode))
        state.min_m = int(clean_text(form.get("min_m", state.min_m)))
        state.min_p = int(clean_text(form.get("min_p", state.min_p)))
        state.min_n = int(clean_text(form.get("min_n", state.min_n)))
        state.seed = int(clean_text(form.get("seed", state.seed)))
        state.allow_elastic_coverage = form.get("allow_elastic_coverage") == "on"
        state.ensure_ivg_defaults()

        invalidate_solution(state)
        save_state(state)

        if is_htmx(request):
            return render_flash(request, msg="Configurazione salvata.")
        return redirect_to("/config", msg="Configurazione salvata.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/config", err=str(ex))


@app.get("/project/download")
async def project_download():
    state = get_state()
    payload = json.dumps(state.to_json_dict(), indent=2, ensure_ascii=False).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="progetto_turni_{state.year}_{state.month:02d}.json"'},
    )


@app.post("/project/upload")
async def project_upload(request: Request, file: UploadFile = File(...)):
    state = get_state()
    try:
        raw = await file.read()
        data = json.loads(raw.decode("utf-8-sig"))
        state.load_json_dict(data)
        invalidate_solution(state)
        save_state(state)

        if is_htmx(request):
            return render_flash(request, msg="Progetto caricato.")
        return redirect_to("/config", msg="Progetto caricato.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/config", err=str(ex))


@app.get("/skills")
async def skills_page(request: Request):
    state = get_state()
    rows = [{
        "idx": i,
        "name": state.names[i],
        "role": state.roles[i],
        "ob": state.obiettore[i],
        "tri": state.abil_triage[i],
        "so": state.abil_salaop[i],
    } for i in range(state.num_employees)]

    return render_template(
        "skills.html",
        ctx(
            request,
            title="Competenze",
            rows=rows,
            role_options=ROLE_OPTIONS,
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )


@app.post("/skills/save")
async def skills_save(request: Request):
    state = get_state()
    form = await request.form(max_fields=10000)

    try:
        state.roles = [clean_text(form.get(f"role_{i}", "Giovane")) or "Giovane" for i in range(state.num_employees)]
        state.obiettore = [clean_text(form.get(f"ob_{i}", "NO")) or "NO" for i in range(state.num_employees)]
        state.abil_triage = [clean_text(form.get(f"tri_{i}", "NO")) or "NO" for i in range(state.num_employees)]
        state.abil_salaop = [clean_text(form.get(f"so_{i}", "NO")) or "NO" for i in range(state.num_employees)]

        invalidate_solution(state)
        save_state(state)

        if is_htmx(request):
            return render_flash(request, msg="Competenze salvate.")
        return redirect_to("/skills", msg="Competenze salvate.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/skills", err=str(ex))


@app.get("/categories")
async def categories_page(request: Request):
    state = get_state()
    rows = [{
        "idx": i,
        "name": state.names[i],
        "category": state.categories[i],
    } for i in range(state.num_employees)]

    return render_template(
        "categories.html",
        ctx(
            request,
            title="Categorie",
            rows=rows,
            category_options=CATEGORY_OPTIONS,
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )


@app.post("/categories/save")
async def categories_save(request: Request):
    state = get_state()
    form = await request.form(max_fields=10000)

    try:
        old_categories = list(state.categories)
        new_categories = [
            clean_text(form.get(f"cat_{i}", "Turni standard")) or "Turni standard"
            for i in range(state.num_employees)
        ]
        state.categories = new_categories

        infos = day_info_list(state.year, state.month)
        for e, (old_cat, new_cat) in enumerate(zip(old_categories, new_categories)):
            if old_cat == new_cat:
                continue

            if should_prefill_by_category(new_cat):
                for d in range(len(infos)):
                    state.form2_entries[(e, d)] = prefill_value_for_category(new_cat, infos[d])
            elif should_prefill_by_category(old_cat):
                for d in range(len(infos)):
                    state.form2_entries.pop((e, d), None)

        invalidate_solution(state)
        save_state(state)

        if is_htmx(request):
            return render_flash(request, msg="Categorie salvate.")
        return redirect_to("/categories", msg="Categorie salvate.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/categories", err=str(ex))


@app.get("/ivg")
async def ivg_page(request: Request):
    state = get_state()
    state.ensure_ivg_defaults()

    infos = day_info_list(state.year, state.month)
    weeks = week_ranges_for_month(state.year, state.month)

    rows = []
    for w, (start, end) in enumerate(weeks):
        rows.append({
            "idx": w,
            "label": f"Settimana {w+1}",
            "days_range": f"{infos[start]['header_label']} - {infos[end]['header_label']}",
            "value": state.ivg_by_week.get(w, "Lun"),
        })

    return render_template(
        "ivg.html",
        ctx(
            request,
            title="IVG",
            rows=rows,
            options=IVG_OPTIONS,
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )


@app.post("/ivg/save")
async def ivg_save(request: Request):
    state = get_state()
    form = await request.form(max_fields=10000)

    try:
        weeks = week_ranges_for_month(state.year, state.month)
        state.ivg_by_week = {
            w: clean_text(form.get(f"ivg_{w}", "Lun")) or "Lun"
            for w in range(len(weeks))
        }

        invalidate_solution(state)
        save_state(state)

        if is_htmx(request):
            return render_flash(request, msg="IVG salvato.")
        return redirect_to("/ivg", msg="IVG salvato.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/ivg", err=str(ex))


@app.get("/form2")
async def form2_page(request: Request):
    return render_template(
        "form2.html",
        ctx(
            request,
            title="Turni specifici",
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )

@app.get("/api/form2-grid")
async def api_form2_grid():
    state = get_state()
    return build_form2_grid_payload(state)


@app.post("/api/form2-grid/save")
async def api_form2_grid_save(payload: dict):
    state = get_state()

    try:
        rows = payload.get("rows", [])
        entries = parse_form2_grid_rows(rows, state)
        apply_form2_entries(state, entries)
        return {"ok": True, "message": "Turni specifici salvati."}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


@app.post("/api/form2-grid/verify")
async def api_form2_grid_verify(payload: dict):
    state = get_state()

    try:
        rows = payload.get("rows", [])
        entries = parse_form2_grid_rows(rows, state)
        apply_form2_entries(state, entries)
        warnings = ScheduleSolver(state).precheck()
        return {"ok": True, "message": "Input coerenti.", "warnings": warnings}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


@app.post("/api/form2-grid/solve")
async def api_form2_grid_solve(payload: dict):
    state = get_state()

    try:
        rows = payload.get("rows", [])
        entries = parse_form2_grid_rows(rows, state)
        apply_form2_entries(state, entries)
        state.solution = ScheduleSolver(state).solve()
        state.violations = state.solution["violations"]
        save_state(state)
        return {"ok": True, "redirect": "/output"}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}

@app.post("/form2/save")
async def form2_save(request: Request):
    state = get_state()
    form = await request.form(max_fields=10000)

    try:
        entries = parse_form2_entries(form, state)
        apply_form2_entries(state, entries)

        if is_htmx(request):
            return render_flash(request, msg="Turni specifici salvati.")
        return redirect_to("/form2", msg="Turni specifici salvati.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/form2", err=str(ex))


@app.post("/form2/verify")
async def form2_verify(request: Request):
    state = get_state()
    form = await request.form(max_fields=10000)

    try:
        entries = parse_form2_entries(form, state)
        apply_form2_entries(state, entries)
        warnings = ScheduleSolver(state).precheck()

        if is_htmx(request):
            return render_flash(request, msg="Input coerenti.", warn_list=warnings)
        return redirect_to("/form2", msg="Input coerenti.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/form2", err=str(ex))


@app.post("/solve")
async def solve_now(request: Request):
    state = get_state()

    try:
        form = await request.form(max_fields=10000)
        entries = parse_form2_entries(form, state)
        apply_form2_entries(state, entries)
        state.solution = ScheduleSolver(state).solve()
        state.violations = state.solution["violations"]
        save_state(state)

        if is_htmx(request):
            return htmx_redirect("/output")
        return redirect_to("/output", msg="Soluzione generata.")
    except Exception as ex:
        import traceback
        tb = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
        print("\n=== SOLVE ERROR ===")
        print(tb)

        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/form2", err=str(ex))

@app.post("/overrides/clear")
async def clear_overrides(request: Request):
    state = get_state()
    state.output_overrides.clear()
    invalidate_solution(state)
    save_state(state)

    if is_htmx(request):
        return render_flash(request, msg="Override rimossi.")
    return redirect_to("/form2", msg="Override rimossi.")


@app.get("/output")
async def output_page(request: Request):
    return render_template(
        "output.html",
        ctx(
            request,
            title="Output",
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )


@app.get("/output/root")
async def output_root(request: Request):
    return render_output_root(request)


@app.post("/output/regenerate")
async def output_regenerate(request: Request):
    state = get_state()
    if not state.solution:
        if is_htmx(request):
            return render_output_root(request, err="Nessuna soluzione disponibile.")
        return redirect_to("/output", err="Nessuna soluzione disponibile.")

    try:
        state.seed += 1
        state.solution = ScheduleSolver(state).solve()
        state.violations = state.solution["violations"]
        save_state(state)

        if is_htmx(request):
            return render_output_root(request, msg="Soluzione rigenerata.")
        return redirect_to("/output", msg="Soluzione rigenerata.")
    except Exception as ex:
        if is_htmx(request):
            return render_output_root(request, err=str(ex))
        return redirect_to("/output", err=str(ex))


@app.post("/output/override")
async def output_override(request: Request):
    state = get_state()
    if not state.solution:
        if is_htmx(request):
            return render_output_root(request, err="Nessuna soluzione disponibile.")
        return redirect_to("/output", err="Nessuna soluzione disponibile.")

    form = await request.form(max_fields=10000)
    e = int(clean_text(form.get("employee_idx", "0")))
    d = int(clean_text(form.get("day_idx", "0")))
    value = clean_text(form.get("value", ""))

    old_solution = state.solution
    old_viol = state.violations
    old_present = (e, d) in state.output_overrides
    old_val = state.output_overrides.get((e, d), "")

    try:
        if value not in OUTPUT_EDIT_VALUES:
            raise ValueError("Override non valido.")

        state.output_overrides[(e, d)] = value
        state.solution = ScheduleSolver(state).solve()
        state.violations = state.solution["violations"]
        save_state(state)

        if is_htmx(request):
            return render_output_root(request, msg="Override applicato.")
        return redirect_to("/output", msg="Override applicato.")
    except Exception as ex:
        if old_present:
            state.output_overrides[(e, d)] = old_val
        else:
            state.output_overrides.pop((e, d), None)
        state.solution = old_solution
        state.violations = old_viol
        save_state(state)

        if is_htmx(request):
            return render_output_root(request, err=str(ex))
        return redirect_to("/output", err=str(ex))


@app.post("/output/override/remove")
async def output_override_remove(request: Request):
    state = get_state()
    if not state.solution:
        if is_htmx(request):
            return render_output_root(request, err="Nessuna soluzione disponibile.")
        return redirect_to("/output", err="Nessuna soluzione disponibile.")

    form = await request.form(max_fields=10000)
    e = int(clean_text(form.get("employee_idx", "0")))
    d = int(clean_text(form.get("day_idx", "0")))

    old_solution = state.solution
    old_viol = state.violations
    old_present = (e, d) in state.output_overrides
    old_val = state.output_overrides.get((e, d), "")

    try:
        state.output_overrides.pop((e, d), None)
        state.solution = ScheduleSolver(state).solve()
        state.violations = state.solution["violations"]
        save_state(state)

        if is_htmx(request):
            return render_output_root(request, msg="Override rimosso.")
        return redirect_to("/output", msg="Override rimosso.")
    except Exception as ex:
        if old_present:
            state.output_overrides[(e, d)] = old_val
        state.solution = old_solution
        state.violations = old_viol
        save_state(state)

        if is_htmx(request):
            return render_output_root(request, err=str(ex))
        return redirect_to("/output", err=str(ex))


@app.get("/solution/download")
async def solution_download():
    state = get_state()
    if not state.solution:
        payload = b'{"error":"Nessuna soluzione disponibile"}'
        return StreamingResponse(io.BytesIO(payload), media_type="application/json")

    payload = json.dumps(state.solution, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="soluzione_turni_{state.year}_{state.month:02d}.json"'},
    )


@app.get("/output/csv")
async def output_csv():
    state = get_state()
    payload = build_csv_export(state) if state.solution else b"Nessuna soluzione disponibile."
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="turni_{state.year}_{state.month:02d}.csv"'},
    )


@app.get("/figures")
async def figures_page(request: Request):
    return render_template(
        "figures.html",
        ctx(
            request,
            title="Figure",
            rows=build_figure_rows(get_state()),
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )


@app.post("/figures/save")
async def figures_save(request: Request):
    state = get_state()
    if not state.solution:
        if is_htmx(request):
            return render_flash(request, err="Nessuna soluzione disponibile.")
        return redirect_to("/figures", err="Nessuna soluzione disponibile.")

    form = await request.form(max_fields=10000)
    new_assignments = {}

    try:
        for d, _info in enumerate(state.solution["day_infos"]):
            for shift in ["M", "P", "N"]:
                capo = clean_text(form.get(f"fig_{d}_{shift}_capo", ""))
                sala = clean_text(form.get(f"fig_{d}_{shift}_sala", ""))
                tri = clean_text(form.get(f"fig_{d}_{shift}_tri", ""))

                chosen = [x for x in [capo, sala, tri] if x]
                if len(chosen) != len(set(chosen)):
                    raise ValueError(f"{state.solution['day_infos'][d]['header_label']} {shift}: figure duplicate non ammesse")

                if capo:
                    new_assignments[(d, shift, "Capoturnazione")] = capo
                if sala:
                    new_assignments[(d, shift, "SalaOperatoria")] = sala
                if tri:
                    new_assignments[(d, shift, "Triage")] = tri

        state.figure_assignments = new_assignments
        state.solution["figure_stats"] = ScheduleSolver(state).compute_figure_stats(state.solution["schedule"])
        save_state(state)

        if is_htmx(request):
            return render_flash(request, msg="Figure salvate.")
        return redirect_to("/figures", msg="Figure salvate.")
    except Exception as ex:
        if is_htmx(request):
            return render_flash(request, err=str(ex))
        return redirect_to("/figures", err=str(ex))


@app.get("/violations")
async def violations_page(request: Request):
    state = get_state()
    return render_template(
        "violations.html",
        ctx(
            request,
            title="Violazioni",
            hard=state.violations.get("hard", []),
            soft=state.violations.get("soft", []),
            initial_msg=request.query_params.get("msg"),
            initial_err=request.query_params.get("err"),
        ),
    )

from web_htmx.accounts import install_accounts

install_accounts(
    app=app,
    get_state=get_state,
    save_state=save_state,
    invalidate_solution=invalidate_solution,
    render_template=render_template,
)