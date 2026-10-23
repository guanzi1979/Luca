import json
import os
from pathlib import Path


def main():
    options_path = Path("/data/options.json")

    if not options_path.exists():
        raise RuntimeError("Configurazione dell'add-on non disponibile.")

    options = json.loads(options_path.read_text(encoding="utf-8"))

    public_url = str(options.get("public_url", "")).rstrip("/")
    if not public_url:
        raise RuntimeError("Configura public_url nelle opzioni dell'add-on.")

    os.environ["TURNI_PUBLIC_URL"] = public_url
    os.environ["TURNI_DATA_DIR"] = "/data"

    # Non accetta indiscriminatamente header proxy da altri dispositivi.
    os.environ["TURNI_TRUSTED_PROXY_IPS"] = "127.0.0.1"

    from persistence import connect_db, load_autosave
    from web_htmx.accounts import (
        initialize_accounts,
        create_user,
        normalized_username,
        password_hash,
    )

    # Importa il progetto JSON esistente soltanto al primo avvio.
    load_autosave()
    initialize_accounts()

    with connect_db() as db:
        admin_exists = db.execute("""
            SELECT 1 FROM users WHERE role = 'admin' LIMIT 1
        """).fetchone() is not None

    if not admin_exists:
        username = str(
            options.get("initial_admin_username", "amministratore")
        )
        password = str(options.get("initial_admin_password", ""))

        if not password:
            raise RuntimeError(
                "Primo avvio: imposta initial_admin_password "
                "nelle opzioni dell'add-on, almeno 12 caratteri."
            )

        create_user(username, password, "admin")
        print("Account amministratore iniziale creato.")

    if options.get("reset_admin_password", False):
        username = normalized_username(
            options.get("initial_admin_username", "amministratore")
        )
        new_password = str(options.get("initial_admin_password", ""))

        # Verifica anche la lunghezza minima della nuova password.
        encoded = password_hash(new_password)

        with connect_db() as db:
            admin = db.execute("""
                SELECT id
                FROM users
                WHERE username = ? AND role = 'admin'
            """, (username,)).fetchone()

            if admin is None:
                raise RuntimeError(
                    "Ripristino password: amministratore non trovato. "
                    "Controlla initial_admin_username."
                )

            db.execute("""
                UPDATE users SET password_hash = ? WHERE id = ?
            """, (encoded, admin["id"]))

            # Revoca le sessioni esistenti e sblocca i tentativi di login.
            db.execute(
                "DELETE FROM sessions WHERE user_id = ?",
                (admin["id"],),
            )
            db.execute(
                "DELETE FROM login_failures WHERE username = ?",
                (username,),
            )

        print(
            "Password amministratore aggiornata. "
            "Disattiva reset_admin_password e svuota "
            "initial_admin_password nella configurazione."
        )

    import uvicorn

    uvicorn.run(
        "web_htmx.app:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        reload=False,
        proxy_headers=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
