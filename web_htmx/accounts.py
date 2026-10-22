import asyncio
import calendar
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from persistence import connect_db
from utils import parse_names


COOKIE_NAME = "turni_session"
SESSION_SECONDS = 8 * 60 * 60
LOGIN_WINDOW = 15 * 60
MAX_LOGIN_FAILURES = 5

PUBLIC_URL = os.environ.get(
    "TURNI_PUBLIC_URL", "http://localhost:8000"
).rstrip("/")

public_parts = urlsplit(PUBLIC_URL)

if (
    public_parts.scheme not in {"http", "https"}
    or not public_parts.netloc
    or public_parts.path not in {"", "/"}
    or public_parts.query
    or public_parts.fragment
    or public_parts.username
    or public_parts.password
):
    raise RuntimeError(
        "TURNI_PUBLIC_URL deve essere un'origine, "
        "ad esempio https://turni-esempio.duckdns.org"
    )

PUBLIC_ORIGIN = f"{public_parts.scheme}://{public_parts.netloc}"
SECURE_COOKIES = public_parts.scheme == "https"

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
DESIRED_VALUES = {"", "MD", "PD", "ND", "RD"}


def initialize_accounts():
    with connect_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'employee')),
                employee_id TEXT UNIQUE,
                CHECK (
                    (role = 'admin' AND employee_id IS NULL)
                    OR
                    (role = 'employee' AND employee_id IS NOT NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id)
                    ON DELETE CASCADE,
                csrf TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS login_failures (
                username TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                user_id INTEGER,
                path TEXT NOT NULL,
                status INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portal_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                requests_open INTEGER NOT NULL DEFAULT 1
            );

            INSERT OR IGNORE INTO portal_settings(id, requests_open)
            VALUES (1, 1);
        """)


def normalized_username(value):
    value = str(value).strip().lower()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError(
            "Username: 3-64 caratteri, lettere minuscole, "
            "numeri, punto, trattino o underscore."
        )
    return value


def validate_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError("La password deve contenere da 12 a 128 caratteri.")


def password_hash(password):
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def password_matches(password, stored):
    if not isinstance(password, str) or len(password) > 128:
        return False

    try:
        algorithm, salt_hex, expected_hex = stored.split("$")
        if algorithm != "scrypt":
            return False

        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=16384,
            r=8,
            p=1,
            dklen=32,
        )
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(username, password, role, employee_id=None):
    username = normalized_username(username)
    encoded = password_hash(password)

    with connect_db() as db:
        db.execute("""
            INSERT INTO users(username, password_hash, role, employee_id)
            VALUES (?, ?, ?, ?)
        """, (username, encoded, role, employee_id))


def has_employee_accounts():
    with connect_db() as db:
        return db.execute("""
            SELECT 1 FROM users WHERE role = 'employee' LIMIT 1
        """).fetchone() is not None


def requests_are_open():
    with connect_db() as db:
        row = db.execute("""
            SELECT requests_open FROM portal_settings WHERE id = 1
        """).fetchone()
    return bool(row["requests_open"])


def find_session(request):
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 256:
        return None

    with connect_db() as db:
        row = db.execute("""
            SELECT
                users.id,
                users.username,
                users.role,
                users.employee_id,
                sessions.csrf,
                sessions.expires_at
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
              AND sessions.expires_at > ?
        """, (token_hash(token), int(time.time()))).fetchone()

    return dict(row) if row else None


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)

    with connect_db() as db:
        db.execute(
            "DELETE FROM sessions WHERE expires_at <= ?",
            (int(time.time()),),
        )
        db.execute("""
            INSERT INTO sessions(token_hash, user_id, csrf, expires_at)
            VALUES (?, ?, ?, ?)
        """, (
            token_hash(token),
            user_id,
            csrf,
            int(time.time()) + SESSION_SECONDS,
        ))

    return token


def install_accounts(
    app, get_state, save_state, invalidate_solution, render_template
):
    initialize_accounts()

    # Questa versione utilizza un solo worker.
    # Serializza accessi e modifiche allo stato condiviso.
    state_lock = asyncio.Lock()

    def page(request, name, **values):
        return render_template(name, {
            "request": request,
            "app_title": "Generatore Turni",
            "current_user": getattr(request.state, "user", None),
            **values,
        })

    def redirect(request, destination):
        if request.headers.get("HX-Request", "").lower() == "true":
            return Response(
                status_code=200,
                headers={"HX-Redirect": destination},
            )
        return RedirectResponse(destination, status_code=303)

    def employee_index(user, state):
        employee_id = user["employee_id"]
        try:
            return state.employee_ids.index(employee_id)
        except ValueError:
            raise ValueError(
                "Account non associato al progetto corrente. "
                "Contatta l'amministratore."
            )

    def row_revision(state, employee):
        payload = {
            "employee_id": state.employee_ids[employee],
            "year": state.year,
            "month": state.month,
            "entries": [
                state.form2_entries.get((employee, day), "")
                for day in range(
                    calendar.monthrange(state.year, state.month)[1]
                )
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def employee_page(request, error=None, message=None):
        state = get_state()

        try:
            employee = employee_index(request.state.user, state)
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=403)

        n_days = calendar.monthrange(state.year, state.month)[1]

        cells = []
        for day in range(n_days):
            value = state.form2_entries.get((employee, day), "")
            locked = (
                value not in DESIRED_VALUES
                or (employee, day) in state.output_overrides
            )
            cells.append({
                "day": day,
                "label": day + 1,
                "value": value,
                "locked": locked,
            })

        return page(
            request,
            "employee.html",
            title="Le mie preferenze",
            employee_name=state.names[employee],
            month=state.month,
            year=state.year,
            cells=cells,
            revision=row_revision(state, employee),
            requests_open=requests_are_open(),
            error=error,
            message=message,
        )

    @app.middleware("http")
    async def account_access(request: Request, call_next):
        path = request.url.path
        mutating = request.method not in {"GET", "HEAD", "OPTIONS"}

        if mutating:
            # Il browser deve inviare richieste dalla stessa origine.
            if request.headers.get("origin") != PUBLIC_ORIGIN:
                return Response(
                    "Origine della richiesta non autorizzata.",
                    status_code=403,
                )

        if path.startswith("/static/"):
            return await call_next(request)

        async with state_lock:
            user = find_session(request)
            request.state.user = user
            request.state.csrf_token = user["csrf"] if user else ""

            if path == "/login":
                response = await call_next(request)
            else:
                if user is None:
                    if path.startswith("/api/"):
                        return Response(
                            '{"ok":false,"error":"Accesso richiesto."}',
                            status_code=401,
                            media_type="application/json",
                        )
                    return redirect(request, "/login")

                employee_routes = {
                    "/", "/employee", "/employee/save",
                    "/logout", "/password",
                }

                if user["role"] != "admin" and path not in employee_routes:
                    return Response(
                        "Operazione riservata all'amministratore.",
                        status_code=403,
                    )

                if mutating:
                    csrf = request.headers.get("X-CSRF-Token", "")

                    if not csrf:
                        content_type = request.headers.get(
                            "content-type", ""
                        )

                        if (
                            content_type.startswith(
                                "application/x-www-form-urlencoded"
                            )
                            or content_type.startswith(
                                "multipart/form-data"
                            )
                        ):
                            # Conserva il corpo per il successivo handler.
                            await request.body()
                            form = await request.form(max_fields=10000)
                            csrf = str(form.get("_csrf", ""))

                    if not hmac.compare_digest(csrf, user["csrf"]):
                        return Response(
                            "Sessione non valida. Ricarica la pagina.",
                            status_code=403,
                        )

                    # Evita che gli account vengano associati alla persona
                    # sbagliata dopo riordini o sostituzioni dell'elenco.
                    if has_employee_accounts():
                        if path == "/project/upload":
                            return Response(
                                "Importazione bloccata mentre esistono "
                                "account dipendenti. Effettua un backup "
                                "e gestisci prima gli account.",
                                status_code=409,
                            )

                        if path == "/config/save":
                            await request.body()
                            form = await request.form(max_fields=10000)
                            state = get_state()

                            try:
                                n = int(form.get(
                                    "num_employees", state.num_employees
                                ))
                                names = parse_names(
                                    str(form.get("names", "")), n
                                )
                            except (TypeError, ValueError):
                                return Response(
                                    "Elenco dipendenti non valido.",
                                    status_code=400,
                                )

                            if (
                                n != state.num_employees
                                or names != state.names
                            ):
                                return Response(
                                    "Non puoi modificare o riordinare "
                                    "l'elenco mentre esistono account "
                                    "dipendenti.",
                                    status_code=409,
                                )

                response = await call_next(request)

                if mutating:
                    with connect_db() as db:
                        db.execute("""
                            INSERT INTO access_audit(
                                created_at, user_id, path, status
                            ) VALUES (?, ?, ?, ?)
                        """, (
                            int(time.time()),
                            user["id"],
                            path,
                            response.status_code,
                        ))

            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "same-origin"
            return response

    @app.get("/login")
    async def login_page(request: Request):
        return page(request, "login.html", title="Accedi", error=None)

    @app.post("/login")
    async def login_submit(request: Request):
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()[:64]
        password = str(form.get("password", ""))

        now = int(time.time())

        with connect_db() as db:
            db.execute("""
                DELETE FROM login_failures WHERE created_at < ?
            """, (now - LOGIN_WINDOW,))

            failures = db.execute("""
                SELECT COUNT(*) AS total FROM login_failures
                WHERE username = ?
            """, (username,)).fetchone()["total"]

            user = db.execute("""
                SELECT * FROM users WHERE username = ?
            """, (username,)).fetchone()

        if failures >= MAX_LOGIN_FAILURES:
            return page(
                request,
                "login.html",
                title="Accedi",
                error="Troppi tentativi. Riprova tra 15 minuti.",
            )

        # Esegue comunque scrypt quando lo username non esiste.
        stored = (
            user["password_hash"]
            if user
            else app.state.dummy_password_hash
        )
        valid = password_matches(password, stored)

        if not user or not valid:
            with connect_db() as db:
                db.execute("""
                    INSERT INTO login_failures(username, created_at)
                    VALUES (?, ?)
                """, (username, now))

            return page(
                request,
                "login.html",
                title="Accedi",
                error="Credenziali non valide.",
            )

        with connect_db() as db:
            db.execute(
                "DELETE FROM login_failures WHERE username = ?",
                (username,),
            )

            previous = request.cookies.get(COOKIE_NAME, "")
            if previous:
                db.execute("""
                    DELETE FROM sessions WHERE token_hash = ?
                """, (token_hash(previous),))

        token = create_session(user["id"])
        destination = (
            "/config" if user["role"] == "admin" else "/employee"
        )
        response = RedirectResponse(destination, status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=SECURE_COOKIES,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request):
        token = request.cookies.get(COOKIE_NAME, "")

        if token:
            with connect_db() as db:
                db.execute("""
                    DELETE FROM sessions WHERE token_hash = ?
                """, (token_hash(token),))

        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/employee")
    async def employee_preferences(request: Request):
        return employee_page(request)

    @app.post("/employee/save")
    async def employee_save(request: Request):
        if not requests_are_open():
            return employee_page(
                request, error="Inserimento preferenze chiuso."
            )

        state = get_state()
        form = await request.form()

        try:
            employee = employee_index(request.state.user, state)

            if not hmac.compare_digest(
                str(form.get("revision", "")),
                row_revision(state, employee),
            ):
                return employee_page(
                    request,
                    error=(
                        "I dati sono cambiati dopo l'apertura della pagina. "
                        "Controlla i valori aggiornati e ripeti il salvataggio."
                    ),
                )

            n_days = calendar.monthrange(state.year, state.month)[1]
            changes = {}

            for day in range(n_days):
                old = state.form2_entries.get((employee, day), "")

                if (
                    old not in DESIRED_VALUES
                    or (employee, day) in state.output_overrides
                ):
                    continue

                value = str(form.get(f"d_{day}", "")).strip()

                if value not in DESIRED_VALUES:
                    raise ValueError("Preferenza non valida.")

                changes[day] = value

            effective = [
                changes.get(
                    day,
                    state.form2_entries.get((employee, day), ""),
                )
                for day in range(n_days)
            ]

            if sum(v in {"MD", "PD", "ND"} for v in effective) > 3:
                raise ValueError("Puoi richiedere al massimo 3 turni.")

            if effective.count("RD") > 2:
                raise ValueError("Puoi richiedere al massimo 2 riposi.")

            for day, value in changes.items():
                if value:
                    state.form2_entries[(employee, day)] = value
                else:
                    state.form2_entries.pop((employee, day), None)

            invalidate_solution(state)
            save_state(state)

            return employee_page(
                request, message="Preferenze salvate."
            )
        except ValueError as exc:
            return employee_page(request, error=str(exc))

    @app.get("/accounts")
    async def accounts_page(request: Request):
        return account_management_page(request)

    def account_management_page(request, error=None, message=None):
        state = get_state()

        with connect_db() as db:
            users = [
                dict(row) for row in db.execute("""
                    SELECT id, username, role, employee_id
                    FROM users ORDER BY username
                """).fetchall()
            ]

        names_by_id = dict(zip(state.employee_ids, state.names))
        assigned = {
            user["employee_id"] for user in users
            if user["employee_id"]
        }

        for user in users:
            user["employee_name"] = names_by_id.get(
                user["employee_id"], ""
            )

        available = [
            {"id": employee_id, "name": name}
            for employee_id, name
            in zip(state.employee_ids, state.names)
            if employee_id not in assigned
        ]

        return page(
            request,
            "accounts.html",
            title="Account",
            users=users,
            available=available,
            requests_open=requests_are_open(),
            error=error,
            message=message,
        )

    @app.post("/accounts/create")
    async def accounts_create(request: Request):
        form = await request.form()
        state = get_state()
        employee_id = str(form.get("employee_id", ""))

        try:
            if employee_id not in state.employee_ids:
                raise ValueError("Dipendente non valido.")

            # Rende persistenti gli identificativi prima dell'associazione.
            save_state(state)

            create_user(
                form.get("username", ""),
                str(form.get("password", "")),
                "employee",
                employee_id,
            )

            return account_management_page(
                request, message="Account creato."
            )
        except ValueError as exc:
            return account_management_page(request, error=str(exc))
        except Exception as exc:
            import sqlite3
            if isinstance(exc, sqlite3.IntegrityError):
                return account_management_page(
                    request,
                    error="Username o dipendente già associato.",
                )
            raise

    @app.post("/accounts/delete")
    async def accounts_delete(request: Request):
        form = await request.form()

        try:
            user_id = int(form.get("user_id", ""))
        except (TypeError, ValueError):
            return account_management_page(
                request, error="Account non valido."
            )

        with connect_db() as db:
            # Gli amministratori non vengono cancellati da questa pagina.
            db.execute("""
                DELETE FROM users WHERE id = ? AND role = 'employee'
            """, (user_id,))

        return account_management_page(
            request, message="Account rimosso."
        )

    @app.post("/accounts/requests")
    async def accounts_requests(request: Request):
        form = await request.form()
        opened = 1 if form.get("open") == "1" else 0

        with connect_db() as db:
            db.execute("""
                UPDATE portal_settings SET requests_open = ? WHERE id = 1
            """, (opened,))

        return account_management_page(
            request,
            message=(
                "Inserimento preferenze aperto."
                if opened
                else "Inserimento preferenze chiuso."
            ),
        )

    @app.get("/password")
    async def password_page(request: Request):
        return page(
            request, "password.html",
            title="Cambia password", error=None
        )

    @app.post("/password")
    async def password_change(request: Request):
        form = await request.form()
        user = request.state.user

        with connect_db() as db:
            row = db.execute("""
                SELECT password_hash FROM users WHERE id = ?
            """, (user["id"],)).fetchone()

        if not row or not password_matches(
            str(form.get("old_password", "")), row["password_hash"]
        ):
            return page(
                request, "password.html",
                title="Cambia password",
                error="Password attuale non corretta.",
            )

        new = str(form.get("new_password", ""))

        try:
            if new != str(form.get("confirm_password", "")):
                raise ValueError("Le nuove password non coincidono.")

            encoded = password_hash(new)

            with connect_db() as db:
                db.execute("""
                    UPDATE users SET password_hash = ? WHERE id = ?
                """, (encoded, user["id"]))
                db.execute("""
                    DELETE FROM sessions WHERE user_id = ?
                """, (user["id"],))

            response = RedirectResponse("/login", status_code=303)
            response.delete_cookie(COOKIE_NAME, path="/")
            return response
        except ValueError as exc:
            return page(
                request, "password.html",
                title="Cambia password", error=str(exc)
            )

    app.state.dummy_password_hash = password_hash(
        secrets.token_urlsafe(32)
    )


def main():
    initialize_accounts()

    if len(sys.argv) != 3 or sys.argv[1] not in {
        "create-admin", "reset-password"
    }:
        print(
            "Uso:\n"
            "  python -m web_htmx.accounts create-admin USERNAME\n"
            "  python -m web_htmx.accounts reset-password USERNAME"
        )
        raise SystemExit(1)

    command, username = sys.argv[1:]
    username = normalized_username(username)

    password = getpass.getpass("Password, minimo 12 caratteri: ")
    confirmation = getpass.getpass("Ripeti password: ")

    if password != confirmation:
        raise SystemExit("Le password non coincidono.")

    if command == "create-admin":
        create_user(username, password, "admin")
        print("Amministratore creato.")
    else:
        encoded = password_hash(password)

        with connect_db() as db:
            result = db.execute("""
                UPDATE users SET password_hash = ? WHERE username = ?
            """, (encoded, username))

            if result.rowcount != 1:
                raise SystemExit("Utente non trovato.")

            db.execute("""
                DELETE FROM sessions
                WHERE user_id = (
                    SELECT id FROM users WHERE username = ?
                )
            """, (username,))
            db.execute(
                "DELETE FROM login_failures WHERE username = ?",
                (username,),
            )

        print("Password aggiornata e sessioni revocate.")


if __name__ == "__main__":
    main()