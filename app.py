import math
import sqlite3
import hashlib
from flask import Flask, render_template, request, redirect, url_for, flash, session, g


import os


app = Flask(__name__)
app.secret_key = "cámbiala_por_una_clave_segura"  # Para sesiones y flash messages

DB_PATH = "database.db"

# Filtro para formatear montos con separador de miles
@app.template_filter('money')
def money_filter(value):
    try:
        return "{:,.2f}".format(float(value))
    except (ValueError, TypeError):
        return value

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # users…
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    );""")
    # valoracion_bono original…
    cur.execute("""CREATE TABLE IF NOT EXISTS valoracion_bono (
        id_valoracion   INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        monto_bono      REAL NOT NULL,
        tasa_anual      REAL NOT NULL,
        plazo_meses     INTEGER NOT NULL,
        periodicidad    TEXT NOT NULL,
        plazo_gracia    TEXT NOT NULL,
        metodo          TEXT NOT NULL,
        precio_compra   REAL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );""")
    # añade columnas nuevas si no existían:
    try:
        cur.execute("ALTER TABLE valoracion_bono ADD COLUMN currency TEXT DEFAULT 'PEN'")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE valoracion_bono ADD COLUMN rate_type TEXT DEFAULT 'efectiva'")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE valoracion_bono ADD COLUMN capitalization INTEGER DEFAULT 12")
    except sqlite3.OperationalError:
        pass

    # nuevas tablas para flujos y resultados
    cur.execute("""CREATE TABLE IF NOT EXISTS flujo_caja (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        valoracion_id INTEGER NOT NULL,
        periodo INTEGER NOT NULL,
        saldo_ini REAL,
        cuota REAL,
        interes REAL,
        amortizacion REAL,
        saldo_fin REAL,
        FOREIGN KEY(valoracion_id) REFERENCES valoracion_bono(id_valoracion)
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS resultados_financieros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        valoracion_id INTEGER NOT NULL UNIQUE,
        eff_annual REAL,
        tasa_periodica REAL,
        price_max REAL,
        duration REAL,
        duration_mod REAL,
        convexity REAL,
        tcea REAL,
        trea REAL,
        FOREIGN KEY(valoracion_id) REFERENCES valoracion_bono(id_valoracion)
    );""")

    conn.commit()
    conn.close()



# Inicializa la DB antes de arrancar la app
init_db()

def irr(cashflows, guess=0.1, tol=1e-6, maxiter=100):
    """Calcula la tasa interna de retorno periódica con Newton-Raphson."""
    rate = guess
    for _ in range(maxiter):
        # f(r) = Σ CFₖ/(1+r)ᵏ
        f = sum(cf / ((1 + rate) ** k) for k, cf in enumerate(cashflows))
        # f'(r) = Σ -k·CFₖ/(1+r)ᵏ⁺¹
        df = sum(-k * cf / ((1 + rate) ** (k + 1)) for k, cf in enumerate(cashflows))
        new_rate = rate - f / df
        if abs(new_rate - rate) < tol:
            return new_rate
        rate = new_rate
    return rate

def calcular_flujo_y_resultados(monto_bono, tasa_anual, plazo_meses,
                                periodicidad, plazo_gracia, precio_compra,
                                rate_type, capitalization):
    """Genera el flujo de caja y los indicadores financieros."""
    tasa_input = tasa_anual / 100.0

    if periodicidad == "trimestral":
        freq = 4
        step = 3
    elif periodicidad == "semestral":
        freq = 2
        step = 6
    else:
        freq = 12
        step = 1

    if rate_type == "efectiva":
        i = (1 + tasa_input) ** (1 / freq) - 1
        eff_annual = tasa_input
    else:
        m = capitalization or freq
        eff_annual = (1 + tasa_input / m) ** m - 1
        i = (1 + eff_annual) ** (1 / freq) - 1

    total_periods = math.ceil(plazo_meses / step)

    flujo = []
    saldo = monto_bono
    start = 1

    if plazo_gracia == "parcial":
        interes0 = saldo * i
        flujo.append({
            "periodo": 1,
            "saldo_ini": round(saldo, 2),
            "cuota": round(interes0, 2),
            "interes": round(interes0, 2),
            "amortizacion": 0.0,
            "saldo_fin": round(saldo, 2),
        })
        n_rest = total_periods - 1
        cuota = saldo * (i / (1 - (1 + i) ** (-n_rest)))
        start = 2
    elif plazo_gracia == "total":
        interes0 = saldo * i
        saldo += interes0
        flujo.append({
            "periodo": 1,
            "saldo_ini": round(monto_bono, 2),
            "cuota": 0.0,
            "interes": round(interes0, 2),
            "amortizacion": 0.0,
            "saldo_fin": round(saldo, 2),
        })
        n_rest = total_periods - 1
        cuota = saldo * (i / (1 - (1 + i) ** (-n_rest)))
        start = 2
    else:
        cuota = monto_bono * (i / (1 - (1 + i) ** (-total_periods)))

    for t in range(start, total_periods + 1):
        interes = saldo * i
        amortiza = cuota - interes
        saldo_fin = saldo - amortiza
        flujo.append({
            "periodo": t,
            "saldo_ini": round(saldo, 2),
            "cuota": round(cuota, 2),
            "interes": round(interes, 2),
            "amortizacion": round(amortiza, 2),
            "saldo_fin": round(saldo_fin, 2),
        })
        saldo = saldo_fin

    pv_flows = [row["cuota"] / ((1 + i) ** row["periodo"]) for row in flujo]
    price_max = round(sum(pv_flows), 2)

    duration = sum(row["periodo"] * pv for row, pv in zip(flujo, pv_flows)) / price_max
    duration_mod = duration / (1 + i)
    convexity = sum(
        row["cuota"] * row["periodo"] * (row["periodo"] + 1) /
        ((1 + i) ** (row["periodo"] + 2))
        for row in flujo
    ) / price_max
    tcea = (1 + i) ** freq - 1

    trea = None
    if precio_compra:
        cfs = [-precio_compra] + [row["cuota"] for row in flujo]
        tir_p = irr(cfs)
        trea = (1 + tir_p) ** freq - 1

    resultados = {
        "eff_annual": eff_annual,
        "tasa_periodica": i,
        "price_max": price_max,
        "duration": duration,
        "duration_mod": duration_mod,
        "convexity": convexity,
        "tcea": tcea,
        "trea": trea,
    }
    return flujo, resultados

@app.route("/test")
def test():
    return "<h1>¡Funciona!</h1>"

@app.route("/")
def home():
    # Mostrar lista de valoraciones del usuario
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    valoraciones = db.execute(
        "SELECT * FROM valoracion_bono WHERE user_id = ?",
        (session["user_id"],)
    ).fetchall()

    return render_template("home.html", valoraciones=valoraciones)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        # hasheamos la contraseña
        password = hashlib.sha256(request.form["password"].encode()).hexdigest()
        try:
            db = get_db()
            db.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, password),
            )
            db.commit()
            flash("Registro exitoso. Ahora puedes iniciar sesión.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("El email ya está registrado.")
            return redirect(url_for("register"))
    return render_template("registro.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = hashlib.sha256(request.form["password"].encode()).hexdigest()
        db = get_db()
        user = db.execute(
            "SELECT id, name FROM users WHERE email = ? AND password = ?",
            (email, password),
        ).fetchone()
        if user:
            session["user_id"] = user[0]
            session["user_name"] = user[1]
            flash(f"Bienvenido, {user[1]}!")
            return redirect(url_for("home"))
        else:
            flash("Credenciales incorrectas.")
            return redirect(url_for("login"))
    return render_template("login.html")

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db:
        db.close()

@app.route("/bono/new", methods=["GET", "POST"])
def new_bono():
    if "user_id" not in session:
        flash("Debes iniciar sesión primero.")
        return redirect(url_for("login"))

    if request.method == "POST":
        user_id       = session["user_id"]
        monto_bono    = float(request.form["monto_bono"])
        tasa_anual    = float(request.form["tasa_anual"])
        plazo_meses   = int(request.form["plazo_meses"])
        periodicidad  = request.form["periodicidad"]
        plazo_gracia  = request.form["plazo_gracia"]
        precio_compra = float(request.form["precio_compra"]) if request.form["precio_compra"] else None

        # nuevos campos
        currency      = request.form["currency"]
        rate_type     = request.form["rate_type"]
        capitalization= int(request.form["capitalization"]) if rate_type == "nominal" else None

        db = get_db()
        cur = db.execute(
            """
            INSERT INTO valoracion_bono
            (user_id, monto_bono, tasa_anual, plazo_meses,
             periodicidad, plazo_gracia, metodo, precio_compra,
             currency, rate_type, capitalization)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                monto_bono,
                tasa_anual,
                plazo_meses,
                periodicidad,
                plazo_gracia,
                "frances",
                precio_compra,
                currency,
                rate_type,
                capitalization,
            ),
        )
        valoracion_id = cur.lastrowid

        flujo, resultados = calcular_flujo_y_resultados(
            monto_bono,
            tasa_anual,
            plazo_meses,
            periodicidad,
            plazo_gracia,
            precio_compra,
            rate_type,
            capitalization,
        )

        for row in flujo:
            db.execute(
                """INSERT INTO flujo_caja
                    (valoracion_id, periodo, saldo_ini, cuota, interes,
                     amortizacion, saldo_fin)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    valoracion_id,
                    row["periodo"],
                    row["saldo_ini"],
                    row["cuota"],
                    row["interes"],
                    row["amortizacion"],
                    row["saldo_fin"],
                ),
            )

        db.execute(
            """INSERT INTO resultados_financieros
                (valoracion_id, eff_annual, tasa_periodica, price_max,
                 duration, duration_mod, convexity, tcea, trea)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                valoracion_id,
                resultados["eff_annual"],
                resultados["tasa_periodica"],
                resultados["price_max"],
                resultados["duration"],
                resultados["duration_mod"],
                resultados["convexity"],
                resultados["tcea"],
                resultados["trea"],
            ),
        )

        db.commit()
        flash("Valoración de bono creada correctamente.")
        return redirect(url_for("home"))

    return render_template("new_bono.html")




@app.route("/bono/<int:id_valoracion>/flow")
def bono_flow(id_valoracion):
    if "user_id" not in session:
        flash("Debes iniciar sesión primero.")
        return redirect(url_for("login"))

    db = get_db()
    bono = db.execute(
        "SELECT * FROM valoracion_bono WHERE id_valoracion = ? AND user_id = ?",
        (id_valoracion, session["user_id"])
    ).fetchone()
    if not bono:
        flash("Valoración no encontrada.")
        return redirect(url_for("home"))

    flujo = db.execute(
        "SELECT * FROM flujo_caja WHERE valoracion_id = ? ORDER BY periodo",
        (id_valoracion,)
    ).fetchall()
    resultados = db.execute(
        "SELECT * FROM resultados_financieros WHERE valoracion_id = ?",
        (id_valoracion,)
    ).fetchone()

    return render_template(
        "flow.html",
        bono=bono,
        flujo=flujo,
        currency=bono["currency"],
        rate_type=bono["rate_type"],
        capitalization=bono["capitalization"],
        eff_annual=round(resultados["eff_annual"] * 100, 4),
        tasa_periodica=round(resultados["tasa_periodica"] * 100, 4),
        price_max=resultados["price_max"],
        duration=round(resultados["duration"], 4),
        duration_mod=round(resultados["duration_mod"], 4),
        convexity=round(resultados["convexity"], 4),
        tcea=round(resultados["tcea"] * 100, 4),
        trea=(
            round(resultados["trea"] * 100, 4)
            if resultados["trea"] is not None
            else None
        )
    )

@app.route("/bono/<int:id_valoracion>/edit", methods=["GET", "POST"])
def edit_bono(id_valoracion):
    if "user_id" not in session:
        flash("Debes iniciar sesión primero.")
        return redirect(url_for("login"))

    db = get_db()
    bono = db.execute(
        "SELECT * FROM valoracion_bono WHERE id_valoracion = ? AND user_id = ?",
        (id_valoracion, session["user_id"])
    ).fetchone()
    if not bono:
        flash("Valoración no encontrada.")
        return redirect(url_for("home"))

    if request.method == "POST":
        monto_bono     = float(request.form["monto_bono"])
        tasa_anual     = float(request.form["tasa_anual"])
        plazo_meses    = int(request.form["plazo_meses"])
        periodicidad   = request.form["periodicidad"]
        plazo_gracia   = request.form["plazo_gracia"]
        precio_compra  = float(request.form["precio_compra"]) if request.form["precio_compra"] else None
        currency       = request.form["currency"]
        rate_type      = request.form["rate_type"]
        capitalization = int(request.form["capitalization"]) if rate_type == "nominal" else None

        db.execute(
            """
            UPDATE valoracion_bono SET
                monto_bono     = ?,
                tasa_anual     = ?,
                plazo_meses    = ?,
                periodicidad   = ?,
                plazo_gracia   = ?,
                precio_compra  = ?,
                currency       = ?,
                rate_type      = ?,
                capitalization = ?
            WHERE id_valoracion = ? AND user_id = ?
            """,
            (
                monto_bono,
                tasa_anual,
                plazo_meses,
                periodicidad,
                plazo_gracia,
                precio_compra,
                currency,
                rate_type,
                capitalization,
                id_valoracion,
                session["user_id"],
            ),
        )

        db.execute(
            "DELETE FROM flujo_caja WHERE valoracion_id = ?",
            (id_valoracion,),
        )
        db.execute(
            "DELETE FROM resultados_financieros WHERE valoracion_id = ?",
            (id_valoracion,),
        )

        flujo, resultados = calcular_flujo_y_resultados(
            monto_bono,
            tasa_anual,
            plazo_meses,
            periodicidad,
            plazo_gracia,
            precio_compra,
            rate_type,
            capitalization,
        )

        for row in flujo:
            db.execute(
                """INSERT INTO flujo_caja
                    (valoracion_id, periodo, saldo_ini, cuota, interes,
                     amortizacion, saldo_fin)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    id_valoracion,
                    row["periodo"],
                    row["saldo_ini"],
                    row["cuota"],
                    row["interes"],
                    row["amortizacion"],
                    row["saldo_fin"],
                ),
            )

        db.execute(
            """INSERT INTO resultados_financieros
                (valoracion_id, eff_annual, tasa_periodica, price_max,
                 duration, duration_mod, convexity, tcea, trea)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                id_valoracion,
                resultados["eff_annual"],
                resultados["tasa_periodica"],
                resultados["price_max"],
                resultados["duration"],
                resultados["duration_mod"],
                resultados["convexity"],
                resultados["tcea"],
                resultados["trea"],
            ),
        )

        db.commit()
        flash("Valoración actualizada correctamente.")
        return redirect(url_for("home"))

    # GET: precargar formulario
    return render_template("edit_bono.html", bono=bono)

@app.route("/bono/<int:id_valoracion>/delete", methods=["POST"])
def delete_bono(id_valoracion):
    if "user_id" not in session:
        flash("Debes iniciar sesión primero.")
        return redirect(url_for("login"))
    db = get_db()
    db.execute("DELETE FROM flujo_caja WHERE valoracion_id = ?", (id_valoracion,))
    db.execute(
        "DELETE FROM resultados_financieros WHERE valoracion_id = ?",
        (id_valoracion,),
    )
    db.execute(
        "DELETE FROM valoracion_bono WHERE id_valoracion = ? AND user_id = ?",
        (id_valoracion, session["user_id"]),
    )
    db.commit()
    flash("Valoración eliminada correctamente.")
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Has cerrado sesión.")
    return redirect(url_for("login"))

@app.route("/bono/clear", methods=["POST"])
def clear_valoraciones():
    if "user_id" not in session:
        flash("Debes iniciar sesión primero.")
        return redirect(url_for("login"))
    db = get_db()
    db.execute(
        "DELETE FROM flujo_caja WHERE valoracion_id IN (SELECT id_valoracion FROM valoracion_bono WHERE user_id = ?)",
        (session["user_id"],),
    )
    db.execute(
        "DELETE FROM resultados_financieros WHERE valoracion_id IN (SELECT id_valoracion FROM valoracion_bono WHERE user_id = ?)",
        (session["user_id"],),
    )
    db.execute(
        "DELETE FROM valoracion_bono WHERE user_id = ?",
        (session["user_id"],),
    )
    db.commit()
    flash("Todas tus valoraciones han sido eliminadas.")
    return redirect(url_for("home"))

@app.route("/help")
def help_page():
    return render_template("help.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_env = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug_env)

