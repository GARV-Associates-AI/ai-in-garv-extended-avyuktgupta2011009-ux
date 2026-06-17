# app.py
# ============================================================
# Main Flask Application
# UPDATED: Day 11 — Loss carry-forward routes added
# UPDATED: Day 12 — Consolidated portfolio report added
# UPDATED: Day 12.5 — PDF verification (safety net) added
# UPDATED: Day 12.5 — Fixed large file import (temp file instead of session)
# UPDATED: Day 12.5 — Fixed amount=0 skip bug in confirm import
# UPDATED: Day 12.5 — Warnings now separate from errors (smart collapsible UI)
# UPDATED: Day 12.5 — BUY with amount=0 also allowed in confirm import
# UPDATED: Day 12.5 — Two consolidated report layouts (Combined / Per-Client)
# UPDATED: Day 12.6 — Edit Client Details route added
# UPDATED: Day 12.6 — Consolidated report security
# UPDATED: Day 13   — Reconciliation Tool added
# UPDATED: Day 15.1 — Auto-fill ISIN during AIS reconciliation
# ============================================================

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, session, send_file, jsonify
)
import os
import io
import json
import tempfile
from datetime import datetime
from database import (
    init_db,
    add_client, get_all_clients, get_client, delete_client,
    update_client_details,
    add_transaction, get_transactions, get_transaction_by_id, update_transaction,
    get_transactions_display, delete_transaction,
    get_financial_years,
    save_fmv, get_fmv, get_all_fmv, delete_fmv,
    verify_client_password, set_client_password, client_has_password,
    add_loss, get_all_losses, get_loss_by_id, update_loss, delete_loss,
    get_available_losses, update_loss_used, reset_loss_usage,
    get_all_transactions_for_client,
    bulk_rename_company,
    fill_missing_isin,
)
from calculator import (
    run_fifo, calculate_tax_summary, detect_missing_fmv,
    get_closing_stock,
    get_consolidated_holdings,
)
from reconciliation import (
    extract_holdings_from_pdf,
    extract_holdings_from_excel,
    generate_manual_holdings_template,
    combine_holdings,
    reconcile,
    export_reconciliation_to_excel
)
from itr_generator import generate_schedule_cg, export_schedule_cg_to_excel
from ais_processor import (
    extract_ais_from_pdf,
    reconcile_ais,
    export_ais_reconciliation_to_excel
)

app = Flask(__name__)
app.secret_key = "capital_gains_secret_2026"

VALID_TYPES = ["BUY", "SELL", "BUYBACK", "BONUS", "SPLIT", "GIFT", "INHERIT"]

init_db()

# Day 16 — Register Master DB blueprint
from master_db_routes import master_db_bp
app.register_blueprint(master_db_bp)


# ============================================================
# HELPER — Save/Load transactions via temp file
# ============================================================

def save_transactions_to_temp(transactions):
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8"
    )
    json.dump(transactions, tmp, default=str)
    tmp.close()
    return tmp.name


def load_transactions_from_temp(filepath):
    if not filepath:
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def delete_temp_file(filepath):
    if filepath:
        try:
            os.remove(filepath)
        except Exception:
            pass


# ============================================================
# HOME
# ============================================================
@app.route("/")
def home():
    return redirect(url_for("dashboard"))


# ============================================================
# DASHBOARD
# ============================================================
@app.route("/dashboard")
def dashboard():
    clients = get_all_clients()
    return render_template("dashboard.html", clients=clients)


# ============================================================
# ADD CLIENT
# ============================================================
@app.route("/add_client", methods=["GET", "POST"])
def add_client_route():

    if request.method == "POST":
        name     = request.form.get("name",     "").strip()
        pan      = request.form.get("pan",      "").strip()
        email    = request.form.get("email",    "").strip()
        phone    = request.form.get("phone",    "").strip()
        password = request.form.get("password", "").strip()
        confirm  = request.form.get("confirm_password", "").strip()

        if not name or not pan:
            flash("Name and PAN are required.", "error")
            return render_template("add_client.html")

        if len(pan) != 10:
            flash("PAN must be exactly 10 characters.", "error")
            return render_template("add_client.html")

        if password:
            if len(password) < 4:
                flash("Password must be at least 4 characters.", "error")
                return render_template("add_client.html")
            if confirm and password != confirm:
                flash("Passwords do not match.", "error")
                return render_template("add_client.html")

        client_id, error = add_client(
            name, pan, email, phone,
            password=password if password else None
        )

        if error:
            flash(error, "error")
            return render_template("add_client.html")

        session[f"unlocked_{client_id}"] = True

        flash(f"Client '{name}' added successfully!", "success")
        return redirect(url_for("client_page", client_id=client_id))

    return render_template("add_client.html")


# ============================================================
# EDIT CLIENT DETAILS — Day 12.6
# ============================================================
@app.route("/edit_client_details/<int:client_id>", methods=["POST"])
def edit_client_details_route(client_id):

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year = request.form.get("fin_year", "2024-25")

    name  = request.form.get("name",  "").strip()
    pan   = request.form.get("pan",   "").strip().upper()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()

    if not name:
        flash("Name is required.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not pan:
        flash("PAN is required.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if len(pan) != 10:
        flash("PAN must be exactly 10 characters.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    success, error = update_client_details(
        client_id, name, pan, email, phone
    )

    if not success:
        flash(f"❌ {error}", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    old_pan = client[2]
    if old_pan != pan:
        flash(
            f"✅ Client details updated. "
            f"PAN changed from {old_pan} to {pan}.",
            "success"
        )
    else:
        flash("✅ Client details updated successfully!", "success")

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# UNLOCK CLIENT
# ============================================================
@app.route("/unlock_client/<int:client_id>", methods=["POST"])
def unlock_client(client_id):
    password  = request.form.get("password", "").strip()
    fin_year  = request.form.get("fin_year", "2024-25")
    next_page = request.form.get("next_page", "client")

    is_correct = verify_client_password(client_id, password)

    if is_correct:
        session[f"unlocked_{client_id}"] = True
        flash("✅ Client unlocked successfully!", "success")
    else:
        flash("❌ Wrong password. Please try again.", "error")
        return redirect(url_for("dashboard"))

    if next_page == "calculate":
        return redirect(url_for("calculate",
                                client_id=client_id,
                                fin_year=fin_year))
    elif next_page == "stock":
        return redirect(url_for("closing_stock_page",
                                client_id=client_id,
                                fin_year=fin_year))
    else:
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))


# ============================================================
# UNLOCK FOR CONSOLIDATED REPORT
# ============================================================
@app.route("/unlock_for_report/<int:client_id>", methods=["POST"])
def unlock_for_report(client_id):
    password = request.form.get("password", "").strip()

    if not password:
        flash("⚠️ Please enter a password.", "error")
        return redirect(url_for("consolidated_report"))

    is_correct = verify_client_password(client_id, password)

    if is_correct:
        session[f"unlocked_{client_id}"] = True
        client = get_client(client_id)
        client_name = client[1] if client else "Client"
        flash(f"✅ {client_name} unlocked!", "success")
    else:
        flash("❌ Wrong password.", "error")

    return redirect(url_for("consolidated_report"))


# ============================================================
# CLIENT PAGE
# ============================================================
@app.route("/client/<int:client_id>")
def client_page(client_id):

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please enter the password to open this client.", "error")
            return redirect(url_for("dashboard"))

    fin_year = request.args.get("fin_year", "2024-25")

    transactions = get_transactions_display(client_id, fin_year)
    fin_years    = get_financial_years(client_id)
    fmv_list     = get_all_fmv(client_id)
    all_losses   = get_all_losses(client_id)

    all_years = [
        "2024-25", "2025-26", "2026-27",
        "2023-24", "2022-23", "2021-22"
    ]

    tx_for_calc = get_transactions(client_id, fin_year)
    fmv_data    = get_fmv(client_id)
    missing_fmv = detect_missing_fmv(tx_for_calc, fmv_data) if tx_for_calc else []

    return render_template(
        "client.html",
        client       = client,
        transactions = transactions,
        fin_year     = fin_year,
        fin_years    = fin_years,
        all_years    = all_years,
        fmv_list     = fmv_list,
        missing_fmv  = missing_fmv,
        all_losses   = all_losses,
    )


# ============================================================
# SET / CHANGE / REMOVE CLIENT PASSWORD
# ============================================================
@app.route("/set_password/<int:client_id>", methods=["POST"])
def set_password_route(client_id):

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("❌ You must unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year     = request.form.get("fin_year", "2024-25")
    action       = request.form.get("action", "set")
    new_password = request.form.get("new_password", "").strip()
    confirm      = request.form.get("confirm_new_password", "").strip()

    if action == "remove":
        set_client_password(client_id, "")
        flash("🔓 Password removed. Client is now open access.", "success")

    else:
        if not new_password:
            flash("Please enter a new password.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        if len(new_password) < 4:
            flash("Password must be at least 4 characters.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        if confirm and new_password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        set_client_password(client_id, new_password)
        session[f"unlocked_{client_id}"] = True
        flash("🔒 Password set successfully!", "success")

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# LOCK CLIENT
# ============================================================
@app.route("/lock_client/<int:client_id>")
def lock_client(client_id):
    session.pop(f"unlocked_{client_id}", None)
    flash("🔒 Client locked.", "success")
    return redirect(url_for("dashboard"))


# ============================================================
# LOSS MANAGEMENT ROUTES
# ============================================================

@app.route("/add_loss/<int:client_id>", methods=["POST"])
def add_loss_route(client_id):
    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year  = request.form.get("fin_year", "2024-25")
    loss_year = request.form.get("loss_year", "").strip()
    stcl_str  = request.form.get("stcl", "0").strip()
    ltcl_str  = request.form.get("ltcl", "0").strip()
    notes     = request.form.get("notes", "").strip()

    if not loss_year:
        flash("Loss year is required.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    try:
        stcl = float(stcl_str) if stcl_str else 0
        ltcl = float(ltcl_str) if ltcl_str else 0
    except ValueError:
        flash("STCL and LTCL must be valid numbers.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if stcl < 0 or ltcl < 0:
        flash("Loss amounts cannot be negative.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if stcl == 0 and ltcl == 0:
        flash("At least one of STCL or LTCL must be greater than zero.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    loss_id, error = add_loss(
        client_id, loss_year,
        stcl=stcl, ltcl=ltcl,
        source="manual", notes=notes
    )

    if error:
        flash(f"Error saving loss: {error}", "error")
    else:
        flash(f"✅ Loss for FY {loss_year} saved successfully!", "success")

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


@app.route("/edit_loss/<int:loss_id>", methods=["GET", "POST"])
def edit_loss_route(loss_id):
    loss = get_loss_by_id(loss_id)
    if not loss:
        flash("Loss entry not found.", "error")
        return redirect(url_for("dashboard"))

    client_id = loss[1]
    fin_year  = request.args.get("fin_year",
                                 request.form.get("fin_year", "2024-25"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    if request.method == "POST":
        loss_year = request.form.get("loss_year", "").strip()
        stcl_str  = request.form.get("stcl", "0").strip()
        ltcl_str  = request.form.get("ltcl", "0").strip()
        notes     = request.form.get("notes", "").strip()

        try:
            stcl = float(stcl_str) if stcl_str else 0
            ltcl = float(ltcl_str) if ltcl_str else 0
        except ValueError:
            flash("STCL and LTCL must be valid numbers.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        if stcl < 0 or ltcl < 0:
            flash("Loss amounts cannot be negative.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        update_loss(loss_id, loss_year, stcl, ltcl, notes)
        flash("✅ Loss entry updated.", "success")

        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


@app.route("/delete_loss/<int:loss_id>/<int:client_id>/<fin_year>")
def delete_loss_route(loss_id, client_id, fin_year):
    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    delete_loss(loss_id)
    flash("Loss entry deleted.", "success")

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# ADD TRANSACTION
# ============================================================
@app.route("/add_transaction/<int:client_id>", methods=["POST"])
def add_transaction_route(client_id):

    try:
        fin_year      = request.form["fin_year"]
        date          = request.form["date"]
        type_         = request.form["type"].upper().strip()
        company       = request.form["company"]
        isin          = request.form.get("isin", "")
        quantity      = int(request.form["quantity"])
        amount        = float(request.form["amount"])
        buy_expenses  = float(request.form.get("buy_expenses",  0) or 0)
        sell_expenses = float(request.form.get("sell_expenses", 0) or 0)
        stt           = float(request.form.get("stt", 0) or 0)
        notes         = request.form.get("notes", "")

        if type_ not in VALID_TYPES:
            flash(
                f"Invalid type '{type_}'. Must be one of: "
                f"{', '.join(VALID_TYPES)}",
                "error"
            )
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        if quantity <= 0:
            flash("Quantity must be greater than zero.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        if stt < 0:
            flash("STT cannot be negative.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        if type_ == "BONUS":
            pass
        elif type_ == "SPLIT":
            if amount <= 0:
                flash("For SPLIT: Amount field = split ratio. Must be > 0.", "error")
                return redirect(url_for("client_page",
                                        client_id=client_id,
                                        fin_year=fin_year))
        else:
            if amount <= 0:
                flash("Amount must be greater than zero.", "error")
                return redirect(url_for("client_page",
                                        client_id=client_id,
                                        fin_year=fin_year))

        add_transaction(
            client_id, fin_year, date, type_, company, isin,
            quantity, amount, buy_expenses, sell_expenses,
            stt, notes
        )

        type_labels = {
            "BUY"    : f"Bought {quantity} shares of {company.upper()}",
            "SELL"   : f"Sold {quantity} shares of {company.upper()}",
            "BUYBACK": f"Buyback of {quantity} shares of {company.upper()}",
            "BONUS"  : f"Bonus — {quantity} shares of {company.upper()} (Cost ₹0)",
            "SPLIT"  : f"Stock Split recorded for {company.upper()} (ratio: {amount}:1)",
            "GIFT"   : f"Gift — {quantity} shares of {company.upper()}",
            "INHERIT": f"Inheritance — {quantity} shares of {company.upper()}",
        }
        flash(f"✅ {type_labels.get(type_, f'{type_} added')}", "success")

    except Exception as e:
        flash(f"Error adding transaction: {str(e)}", "error")

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=request.form.get("fin_year", "2024-25")))


# ============================================================
# EDIT TRANSACTION
# ============================================================
@app.route("/edit_transaction/<int:tx_id>", methods=["GET", "POST"])
def edit_transaction_route(tx_id):

    tx = get_transaction_by_id(tx_id)
    if not tx:
        flash("Transaction not found.", "error")
        return redirect(url_for("dashboard"))

    client_id = tx[1]
    fin_year  = tx[2]
    client    = get_client(client_id)

    if request.method == "POST":
        try:
            date          = request.form["date"]
            type_         = request.form["type"].upper().strip()
            company       = request.form["company"]
            isin          = request.form.get("isin", "")
            quantity      = int(request.form["quantity"])
            amount        = float(request.form["amount"])
            buy_expenses  = float(request.form.get("buy_expenses",  0) or 0)
            sell_expenses = float(request.form.get("sell_expenses", 0) or 0)
            stt           = float(request.form.get("stt", 0) or 0)
            notes         = request.form.get("notes", "")

            if type_ not in VALID_TYPES:
                flash(f"Invalid type. Must be one of: {', '.join(VALID_TYPES)}", "error")
                return redirect(url_for("edit_transaction_route", tx_id=tx_id))

            if quantity <= 0:
                flash("Quantity must be greater than zero.", "error")
                return redirect(url_for("edit_transaction_route", tx_id=tx_id))

            if stt < 0:
                flash("STT cannot be negative.", "error")
                return redirect(url_for("edit_transaction_route", tx_id=tx_id))

            if type_ == "BONUS":
                pass
            elif type_ == "SPLIT":
                if amount <= 0:
                    flash("For SPLIT: Amount = split ratio. Must be > 0.", "error")
                    return redirect(url_for("edit_transaction_route", tx_id=tx_id))
            else:
                if amount <= 0:
                    flash("Amount must be greater than zero.", "error")
                    return redirect(url_for("edit_transaction_route", tx_id=tx_id))

            update_transaction(
                tx_id, date, type_, company, isin,
                quantity, amount, buy_expenses, sell_expenses,
                stt, notes
            )

            flash("✅ Transaction updated successfully!", "success")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        except Exception as e:
            flash(f"Error updating: {str(e)}", "error")
            return redirect(url_for("edit_transaction_route", tx_id=tx_id))

    return render_template(
        "edit_transaction.html",
        tx       = tx,
        client   = client,
        fin_year = fin_year
    )


# ============================================================
# DELETE TRANSACTION
# ============================================================
@app.route("/delete_transaction/<int:tx_id>/<int:client_id>/<fin_year>")
def delete_transaction_route(tx_id, client_id, fin_year):
    delete_transaction(tx_id)
    flash("Transaction deleted.", "success")
    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# SAVE FMV
# ============================================================
@app.route("/save_fmv/<int:client_id>", methods=["POST"])
def save_fmv_route(client_id):
    try:
        company  = request.form["fmv_company"].strip().upper()
        fmv      = float(request.form["fmv_value"])
        fin_year = request.form.get("fin_year", "2024-25")

        if not company:
            flash("Company name is required for FMV.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        if fmv <= 0:
            flash("FMV must be greater than zero.", "error")
            return redirect(url_for("client_page",
                                    client_id=client_id,
                                    fin_year=fin_year))

        save_fmv(client_id, company, fmv)
        flash(f"FMV saved for {company}: ₹{fmv}", "success")

    except Exception as e:
        flash(f"Error saving FMV: {str(e)}", "error")

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=request.form.get("fin_year", "2024-25")))


# ============================================================
# DELETE FMV
# ============================================================
@app.route("/delete_fmv/<int:client_id>/<company>/<fin_year>")
def delete_fmv_route(client_id, company, fin_year):
    delete_fmv(client_id, company)
    flash(f"FMV entry for {company} deleted.", "success")
    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# DOWNLOAD FMV TEMPLATE
# ============================================================
@app.route("/download_fmv_template")
def download_fmv_template():
    from excel_handler import generate_fmv_template
    file_data = generate_fmv_template()
    return send_file(
        file_data,
        as_attachment=True,
        download_name="FMV_Template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ============================================================
# IMPORT FMV EXCEL FILE
# ============================================================
@app.route("/import_fmv/<int:client_id>", methods=["POST"])
def import_fmv(client_id):
    from excel_handler import read_fmv_file
    from database import save_fmv_bulk

    fin_year = request.form.get("fin_year", "2024-25")

    if "fmv_file" not in request.files:
        flash("No file uploaded.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    file = request.files["fmv_file"]

    if file.filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not file.filename.lower().endswith((".xlsx", ".xls")):
        flash("Only Excel files (.xlsx or .xls) are allowed.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    entries, errors = read_fmv_file(file)

    if errors and not entries:
        for err in errors[:5]:
            flash(err, "error")
        if len(errors) > 5:
            flash(f"... and {len(errors) - 5} more errors", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if entries:
        save_fmv_bulk(client_id, entries)
        flash(
            f"✅ Successfully imported FMV for {len(entries)} "
            f"compan{'y' if len(entries) == 1 else 'ies'}!",
            "success"
        )

    if errors and entries:
        flash(f"⚠️ {len(errors)} rows had errors and were skipped.", "error")
        for err in errors[:3]:
            flash(err, "error")

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# CALCULATE — with B/F loss handling
# ============================================================
@app.route("/calculate/<int:client_id>/<fin_year>")
def calculate(client_id, fin_year):

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    transactions = get_transactions(client_id, fin_year)

    if not transactions:
        flash("No transactions found for this client and year.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    fmv_data = get_fmv(client_id)

    sells = [t for t in transactions if t["type"] in ["SELL", "BUYBACK"]]
    if not sells:
        flash("No SELL or BUYBACK transactions found. Nothing to calculate.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    missing_fmv = detect_missing_fmv(transactions, fmv_data)
    force_calc  = request.args.get("force", "no")

    if missing_fmv and force_calc != "yes":
        return render_template(
            "fmv_warning.html",
            client      = client,
            fin_year    = fin_year,
            missing_fmv = missing_fmv
        )

    output_rows, errors = run_fifo(transactions, fmv_data)

    # ── DATE FILTER (by sell date) ──────────────────────
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str   = request.args.get("date_to",   "").strip()

    date_from = None
    date_to   = None
    date_filter_active = False

    if date_from_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            date_filter_active = True
        except ValueError:
            flash("⚠️ Invalid 'From Date' — ignored.", "warning")
            date_from_str = ""

    if date_to_str:
        try:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            date_filter_active = True
        except ValueError:
            flash("⚠️ Invalid 'To Date' — ignored.", "warning")
            date_to_str = ""

    if date_filter_active and output_rows:
        from datetime import date as date_cls

        def parse_sell_date(row):
            # sell_date is stored as "DD-MM-YYYY" string in output_rows
            try:
                return datetime.strptime(row["sell_date"], "%d-%m-%Y").date()
            except Exception:
                return None

        filtered = []
        for row in output_rows:
            sd = parse_sell_date(row)
            if sd is None:
                continue
            if date_from and sd < date_from:
                continue
            if date_to and sd > date_to:
                continue
            filtered.append(row)

        if not filtered:
            flash(
                f"⚠️ No transactions found with sell date between "
                f"{date_from_str or 'start'} and {date_to_str or 'end'}. "
                f"Showing all results instead.",
                "warning"
            )
            date_filter_active = False
            date_from_str = ""
            date_to_str   = ""
        else:
            output_rows = filtered
    # ── END DATE FILTER ─────────────────────────────────

    if not output_rows:
        flash("Calculation produced no results. Check your transactions.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    apply_bf = request.args.get("apply_bf", "no") == "yes"

    available_losses = get_available_losses(client_id, fin_year)

    if apply_bf:
        reset_loss_usage(client_id, fin_year)
        available_losses = get_available_losses(client_id, fin_year)

    tax_summary = calculate_tax_summary(
        output_rows,
        brought_forward_losses=available_losses if apply_bf else None,
        apply_bf=apply_bf
    )

    if apply_bf and tax_summary.get("bf_loss_updates"):
        for update in tax_summary["bf_loss_updates"]:
            update_loss_used(
                update["id"],
                update["stcl_used"],
                update["ltcl_used"]
            )

    stcl_cf = tax_summary.get("stcl_carryforward", 0)
    ltcl_cf = tax_summary.get("ltcl_carryforward", 0)

    if stcl_cf > 0 or ltcl_cf > 0:
        add_loss(
            client_id, fin_year,
            stcl=stcl_cf,
            ltcl=ltcl_cf,
            source="auto",
            notes=f"Auto-saved from FY {fin_year} calculation"
        )

    try:
        end_year = int(fin_year.split("-")[1])
        if end_year < 100:
            end_year = 2000 + end_year
        year_end_date = f"{end_year}-03-31"

        all_fin_years    = get_financial_years(client_id)
        all_transactions = []
        for yr in all_fin_years:
            all_transactions.extend(get_transactions(client_id, yr))

        closing_year_end, summary_year_end, cs_errors = get_closing_stock(
            all_transactions, fmv_data, year_end_date
        )

        from datetime import date as date_today
        today_str = date_today.today().strftime("%Y-%m-%d")
        closing_today, summary_today, _ = get_closing_stock(
            all_transactions, fmv_data, today_str
        )

    except Exception as e:
        closing_year_end = []
        summary_year_end = {}
        closing_today    = []
        summary_today    = {}
        cs_errors        = [f"Closing stock error: {str(e)}"]
        errors.extend(cs_errors)
        year_end_date    = ""
        today_str        = ""

    return render_template(
        "result.html",
        client             = client,
        fin_year           = fin_year,
        rows               = output_rows,
        tax                = tax_summary,
        errors             = errors,
        missing_fmv        = missing_fmv,
        closing_year_end   = closing_year_end,
        summary_year_end   = summary_year_end,
        closing_today      = closing_today,
        summary_today      = summary_today,
        year_end_date      = year_end_date,
        today_str          = today_str,
        available_losses   = available_losses,
        apply_bf           = apply_bf,
        date_from_str      = date_from_str,
        date_to_str        = date_to_str,
        date_filter_active = date_filter_active,
    )

# ============================================================
# CLOSING STOCK PAGE
# ============================================================
@app.route("/closing_stock/<int:client_id>/<fin_year>")
def closing_stock_page(client_id, fin_year):
    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    all_fin_years    = get_financial_years(client_id)
    all_transactions = []
    for yr in all_fin_years:
        all_transactions.extend(get_transactions(client_id, yr))

    if not all_transactions:
        flash("No transactions found for this client.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    fmv_data = get_fmv(client_id)

    try:
        end_year = int(fin_year.split("-")[1])
        if end_year < 100:
            end_year = 2000 + end_year
        year_end_date = f"{end_year}-03-31"
    except Exception:
        year_end_date = "2025-03-31"

    from datetime import date as date_obj
    today_str = date_obj.today().strftime("%Y-%m-%d")

    custom_date = request.args.get("custom_date", "").strip()
    active_view = request.args.get("view", "year_end")

    errors = []

    try:
        closing_year_end, summary_year_end, err1 = get_closing_stock(
            all_transactions, fmv_data, year_end_date
        )
        errors.extend(err1)

        closing_today, summary_today, err2 = get_closing_stock(
            all_transactions, fmv_data, today_str
        )
        errors.extend(err2)

        closing_custom      = []
        summary_custom      = {}
        custom_date_display = ""

        if custom_date:
            try:
                closing_custom, summary_custom, err3 = get_closing_stock(
                    all_transactions, fmv_data, custom_date
                )
                errors.extend(err3)
                custom_date_display = datetime.strptime(
                    custom_date, "%Y-%m-%d"
                ).strftime("%d-%m-%Y")
                active_view = "custom"
            except Exception as e:
                flash(f"Invalid custom date: {str(e)}", "error")
                custom_date = ""
                active_view = "year_end"

    except Exception as e:
        flash(f"Error calculating closing stock: {str(e)}", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    return render_template(
        "closing_stock.html",
        client              = client,
        fin_year            = fin_year,
        closing_year_end    = closing_year_end,
        summary_year_end    = summary_year_end,
        year_end_date       = year_end_date,
        closing_today       = closing_today,
        summary_today       = summary_today,
        today_str           = today_str,
        closing_custom      = closing_custom,
        summary_custom      = summary_custom,
        custom_date         = custom_date,
        custom_date_display = custom_date_display,
        active_view         = active_view,
        errors              = errors,
    )


# ============================================================
# CONSOLIDATED PORTFOLIO REPORT
# ============================================================

@app.route("/consolidated_report", methods=["GET", "POST"])
def consolidated_report():
    clients = get_all_clients()

    unlock_status = {}
    for c in clients:
        cid          = c[0]
        has_password = (c[7] == 1)
        if not has_password:
            unlock_status[cid] = True
        else:
            unlock_status[cid] = session.get(f"unlocked_{cid}", False)

    if request.method == "POST":
        selected_ids_raw = request.form.getlist("client_ids")
        layout           = request.form.get("layout", "combined")

        if not selected_ids_raw:
            flash("Please select at least one client.", "error")
            return render_template(
                "consolidated_report.html",
                clients=clients,
                unlock_status=unlock_status
            )

        try:
            selected_ids = [int(cid) for cid in selected_ids_raw]
        except ValueError:
            flash("Invalid client selection.", "error")
            return render_template(
                "consolidated_report.html",
                clients=clients,
                unlock_status=unlock_status
            )

        locked_clients = []
        for cid in selected_ids:
            if not unlock_status.get(cid, False):
                client = get_client(cid)
                if client:
                    locked_clients.append(client[1])

        if locked_clients:
            names_str = ", ".join(locked_clients)
            flash(
                f"🔒 Please unlock these clients first: {names_str}",
                "error"
            )
            return render_template(
                "consolidated_report.html",
                clients=clients,
                unlock_status=unlock_status
            )

        import database as db
        from datetime import date as date_cls

        today = date_cls.today().strftime("%d%b%Y")

        if layout == "combined":
            from excel_handler import generate_consolidated_excel

            try:
                client_names, lots = get_consolidated_holdings(
                    selected_ids, db
                )
            except Exception as e:
                flash(f"Error building report: {str(e)}", "error")
                return render_template(
                    "consolidated_report.html",
                    clients=clients,
                    unlock_status=unlock_status
                )

            if not lots:
                flash(
                    "No current holdings found for the selected clients.",
                    "error"
                )
                return render_template(
                    "consolidated_report.html",
                    clients=clients,
                    unlock_status=unlock_status
                )

            try:
                excel_file = generate_consolidated_excel(
                    client_names, lots, selected_ids
                )
            except Exception as e:
                flash(f"Error generating Excel: {str(e)}", "error")
                return render_template(
                    "consolidated_report.html",
                    clients=clients,
                    unlock_status=unlock_status
                )

            filename = f"Consolidated_Portfolio_CombinedDate_{today}.xlsx"

        else:
            from excel_handler import generate_consolidated_excel_perclient_dates
            from datetime import date as dc

            today_str = dc.today().strftime("%Y-%m-%d")

            client_names    = {}
            client_lots_map = {}

            for cid in selected_ids:
                client = db.get_client(cid)
                if not client:
                    continue
                client_names[cid] = client[1]

                all_tx = db.get_all_transactions_for_client(cid)
                if not all_tx:
                    client_lots_map[cid] = []
                    continue

                fmv_raw = db.get_fmv(cid)
                fmv_map = {k.upper(): v for k, v in fmv_raw.items()}

                holdings_list, _, _ = get_closing_stock(
                    all_tx, fmv_map, today_str
                )

                client_lots_map[cid] = holdings_list

            total_lots = sum(len(v) for v in client_lots_map.values())
            if total_lots == 0:
                flash(
                    "No current holdings found for the selected clients.",
                    "error"
                )
                return render_template(
                    "consolidated_report.html",
                    clients=clients,
                    unlock_status=unlock_status
                )

            try:
                excel_file = generate_consolidated_excel_perclient_dates(
                    client_names, selected_ids, client_lots_map
                )
            except Exception as e:
                flash(f"Error generating Excel: {str(e)}", "error")
                return render_template(
                    "consolidated_report.html",
                    clients=clients,
                    unlock_status=unlock_status
                )

            filename = f"Consolidated_Portfolio_PerClientDate_{today}.xlsx"

        return send_file(
            excel_file,
            as_attachment=True,
            download_name=filename,
            mimetype=(
                "application/vnd.openxmlformats-"
                "officedocument.spreadsheetml.sheet"
            )
        )

    return render_template(
        "consolidated_report.html",
        clients=clients,
        unlock_status=unlock_status
    )


# ============================================================
# DELETE CLIENT
# ============================================================
@app.route("/delete_client/<int:client_id>", methods=["POST"])
def delete_client_route(client_id):
    client = get_client(client_id)
    if client:
        delete_client(client_id)
        session.pop(f"unlocked_{client_id}", None)
        flash(
            f"Client '{client[1]}' and all their data has been deleted.",
            "success"
        )
    return redirect(url_for("dashboard"))


# ============================================================
# DOWNLOAD EXCEL TEMPLATE
# ============================================================
@app.route("/download_template")
def download_template():
    from excel_handler import generate_template
    file_data = generate_template()
    return send_file(
        file_data,
        as_attachment=True,
        download_name="Capital_Gains_Template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ============================================================
# IMPORT EXCEL FILE
# ============================================================
@app.route("/import_excel/<int:client_id>", methods=["POST"])
def import_excel(client_id):
    from excel_handler import read_excel_file

    fin_year = request.form.get("fin_year", "2024-25")

    if "excel_file" not in request.files:
        flash("No file uploaded.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    file = request.files["excel_file"]

    if file.filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not file.filename.lower().endswith((".xlsx", ".xls")):
        flash("Only Excel files (.xlsx or .xls) are allowed.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    transactions, errors, warnings = read_excel_file(file)

    if errors and not transactions:
        flash(
            f"❌ {len(errors)} row(s) had errors. None could be imported.",
            "error"
        )
        for err in errors[:3]:
            flash(err, "error")
        if len(errors) > 3:
            flash(
                f"... and {len(errors) - 3} more errors "
                f"(fix Excel and re-upload)",
                "error"
            )
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if errors and transactions:
        flash(
            f"❌ {len(errors)} row(s) had errors and were SKIPPED.",
            "error"
        )
        for err in errors[:3]:
            flash(err, "error")
        if len(errors) > 3:
            flash(
                f"... and {len(errors) - 3} more errors",
                "error"
            )

    if warnings:
        flash(
            f"ℹ️ {len(warnings)} row(s) imported with warnings. "
            f"See the warnings box on the review page below.",
            "warning"
        )

    if not transactions:
        flash("No valid transactions found in the Excel file.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    temp_path = save_transactions_to_temp(transactions)

    session["pdf_temp_file"]    = temp_path
    session["pdf_client_id"]    = client_id
    session["pdf_fin_year"]     = fin_year
    session["import_source"]    = "excel"
    session["pdf_verification"] = None
    session["import_warnings"]  = warnings

    return redirect(url_for("review_pdf", client_id=client_id))


# ============================================================
# AI CHATBOT ROUTES
# ============================================================
@app.route("/chat/<int:client_id>")
def chat_page(client_id):
    from ai_chatbot import get_suggestions

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    fin_year     = request.args.get("fin_year", "2024-25")
    transactions = get_transactions(client_id, fin_year)
    suggestions  = get_suggestions(has_data=len(transactions) > 0)

    return render_template(
        "chat.html",
        client      = client,
        fin_year    = fin_year,
        suggestions = suggestions
    )


@app.route("/ask_ai/<int:client_id>", methods=["POST"])
def ask_ai_route(client_id):
    from ai_chatbot import ask_ai

    question = request.json.get("question", "").strip()
    fin_year = request.json.get("fin_year", "2024-25")

    if not question:
        return jsonify({"answer": "Please type a question."})

    answer = ask_ai(question, client_id=client_id, fin_year=fin_year)
    return jsonify({"answer": answer})


@app.route("/chat_general")
def chat_general():
    from ai_chatbot import get_suggestions

    suggestions = get_suggestions(has_data=False)
    return render_template(
        "chat.html",
        client      = None,
        fin_year    = None,
        suggestions = suggestions
    )


@app.route("/ask_ai_general", methods=["POST"])
def ask_ai_general():
    from ai_chatbot import ask_ai

    question = request.json.get("question", "").strip()

    if not question:
        return jsonify({"answer": "Please type a question."})

    answer = ask_ai(question)
    return jsonify({"answer": answer})


# ============================================================
# PDF UPLOAD
# ============================================================
@app.route("/upload_pdf/<int:client_id>", methods=["POST"])
def upload_pdf(client_id):
    from pdf_extractor import extract_transactions_from_pdf

    fin_year = request.form.get("fin_year", "2024-25")

    if "pdf_file" not in request.files:
        flash("No file uploaded.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    file = request.files["pdf_file"]

    if file.filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are allowed.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    transactions, verification, error = extract_transactions_from_pdf(file)

    if error:
        flash(f"❌ {error}", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not transactions:
        flash("AI could not find any transactions in this PDF.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    temp_path = save_transactions_to_temp(transactions)

    session["pdf_temp_file"]    = temp_path
    session["pdf_client_id"]    = client_id
    session["pdf_fin_year"]     = fin_year
    session["import_source"]    = "pdf"
    session["pdf_verification"] = verification
    session["import_warnings"]  = []

    return redirect(url_for("review_pdf", client_id=client_id))


# ============================================================
# REVIEW PAGE
# ============================================================
@app.route("/review_pdf/<int:client_id>")
def review_pdf(client_id):
    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    temp_path       = session.get("pdf_temp_file", None)
    transactions    = load_transactions_from_temp(temp_path)
    fin_year        = session.get("pdf_fin_year", "2024-25")
    source          = session.get("import_source", "pdf")
    verification    = session.get("pdf_verification", None)
    import_warnings = session.get("import_warnings", [])

    if not transactions:
        flash("No transactions to review. Please upload a file.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    return render_template(
        "review_pdf.html",
        client          = client,
        transactions    = transactions,
        fin_year        = fin_year,
        source          = source,
        verification    = verification,
        import_warnings = import_warnings,
    )


# ============================================================
# CONFIRM IMPORT
# ============================================================
@app.route("/confirm_pdf_import/<int:client_id>", methods=["POST"])
def confirm_pdf_import(client_id):
    from database import add_transactions_bulk

    fin_year  = session.get("pdf_fin_year", "2024-25")
    source    = session.get("import_source", "pdf")
    temp_path = session.get("pdf_temp_file", None)

    edited_transactions = []

    row_indexes = set()
    for key in request.form.keys():
        if key.startswith("date_"):
            idx = key.replace("date_", "")
            row_indexes.add(idx)

    row_indexes = sorted(row_indexes, key=lambda x: int(x))

    for idx in row_indexes:
        try:
            date_val = request.form.get(f"date_{idx}", "").strip()
            type_val = request.form.get(f"type_{idx}", "").strip().upper()
            company  = request.form.get(f"company_{idx}", "").strip().upper()
            isin     = request.form.get(f"isin_{idx}", "").strip()
            quantity = int(request.form.get(f"quantity_{idx}", 0) or 0)
            amount   = float(request.form.get(f"amount_{idx}", 0) or 0)
            buy_exp  = float(request.form.get(f"buy_expenses_{idx}", 0) or 0)
            sell_exp = float(request.form.get(f"sell_expenses_{idx}", 0) or 0)
            stt      = float(request.form.get(f"stt_{idx}", 0) or 0)
            notes    = request.form.get(f"notes_{idx}", "").strip()

            if not date_val or not company or quantity <= 0:
                continue

            if stt < 0:
                stt = 0

            if type_val == "BONUS":
                amount = 0.0

            elif type_val == "SPLIT":
                if amount <= 0:
                    flash(
                        f"Row {int(idx)+1}: SPLIT ratio must be > 0. "
                        f"Row skipped.",
                        "error"
                    )
                    continue

            elif type_val in ["GIFT", "INHERIT"]:
                if amount < 0:
                    flash(
                        f"Row {int(idx)+1}: Amount cannot be negative. "
                        f"Row skipped.",
                        "error"
                    )
                    continue

            elif type_val == "BUY":
                if amount < 0:
                    flash(
                        f"Row {int(idx)+1}: BUY Amount cannot be negative. "
                        f"Row skipped.",
                        "error"
                    )
                    continue

            else:
                if amount <= 0:
                    flash(
                        f"Row {int(idx)+1}: {type_val} must have "
                        f"Amount > 0. Row skipped.",
                        "error"
                    )
                    continue

            # Day 16: Apply Master DB choice if user selected one
            mdb_apply = request.form.get(f"mdb_apply_{idx}", "").strip()
            mdb_radio = request.form.get(f"mdb_choice_{idx}", "").strip()
            mdb_value = mdb_apply or mdb_radio

            if mdb_value and "|||" in mdb_value:
                parts = mdb_value.split("|||")
                if len(parts) == 2:
                    mdb_isin = parts[0].strip().upper()
                    mdb_name = parts[1].strip().upper()
                    if mdb_name:
                        company = mdb_name
                    if mdb_isin:
                        isin = mdb_isin

            edited_transactions.append({
                "date"         : date_val,
                "type"         : type_val,
                "company"      : company,
                "isin"         : isin,
                "quantity"     : quantity,
                "amount"       : amount,
                "buy_expenses" : buy_exp,
                "sell_expenses": sell_exp,
                "stt"          : stt,
                "notes"        : notes
            })

        except Exception as e:
            flash(f"Error in row {int(idx)+1}: {str(e)}", "error")
            continue

    if not edited_transactions:
        flash("No valid transactions to save.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    add_transactions_bulk(client_id, fin_year, edited_transactions)

    delete_temp_file(temp_path)
    session.pop("pdf_temp_file",    None)
    session.pop("pdf_client_id",    None)
    session.pop("pdf_fin_year",     None)
    session.pop("import_source",    None)
    session.pop("pdf_verification", None)
    session.pop("import_warnings",  None)

    source_label = "Excel" if source == "excel" else "PDF"
    flash(
        f"✅ Successfully imported {len(edited_transactions)} transactions "
        f"from {source_label} (after your edits)!",
        "success"
    )

    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# CANCEL IMPORT
# ============================================================
@app.route("/cancel_pdf_import/<int:client_id>")
def cancel_pdf_import(client_id):
    fin_year  = session.get("pdf_fin_year", "2024-25")
    temp_path = session.get("pdf_temp_file", None)

    delete_temp_file(temp_path)
    session.pop("pdf_temp_file",    None)
    session.pop("pdf_client_id",    None)
    session.pop("pdf_fin_year",     None)
    session.pop("import_source",    None)
    session.pop("pdf_verification", None)
    session.pop("import_warnings",  None)

    flash("Import cancelled.", "success")
    return redirect(url_for("client_page",
                            client_id=client_id,
                            fin_year=fin_year))


# ============================================================
# AUTOCOMPLETE
# ============================================================
@app.route("/get_companies/<int:client_id>")
def get_companies(client_id):
    fin_years = get_financial_years(client_id)

    companies = set()
    isins     = {}

    for year in fin_years:
        txns = get_transactions(client_id, year)
        for t in txns:
            name = str(t["company"]).upper().strip()
            isin = str(t.get("isin", "") or "").strip().upper()
            companies.add(name)
            if isin and name not in isins:
                isins[name] = isin

    result = []
    for name in sorted(companies):
        result.append({
            "name": name,
            "isin": isins.get(name, "")
        })

    return jsonify(result)


# ============================================================
# FMV REFERENCE FILES
# ============================================================
@app.route("/download_fmv_reference/<exchange>")
def download_fmv_reference(exchange):
    file_map = {
        "nse": "NSE_FMV_31Jan2018.xlsx",
        "bse": "BSE_FMV_31Jan2018.xlsx",
    }

    exchange = exchange.lower()
    if exchange not in file_map:
        flash("Invalid exchange. Use 'nse' or 'bse'.", "error")
        return redirect(url_for("dashboard"))

    filename  = file_map[exchange]
    file_path = os.path.join("static", "fmv_files", filename)

    if not os.path.exists(file_path):
        flash(
            f"❌ {filename} not found. Please ask admin to upload "
            f"the file to static/fmv_files/ folder.",
            "error"
        )
        return redirect(url_for("dashboard"))

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ============================================================
# DAY 13 — RECONCILIATION ROUTES
# ============================================================

@app.route("/upload_reconciliation/<int:client_id>", methods=["POST"])
def upload_reconciliation(client_id):
    """Upload one or more DP statement PDFs and run reconciliation."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year = request.form.get("fin_year", "2024-25")

    if "dp_pdfs" not in request.files:
        flash("❌ No files uploaded.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    pdf_files = request.files.getlist("dp_pdfs")
    pdf_files = [f for f in pdf_files if f.filename != ""]

    if not pdf_files:
        flash("❌ No PDF files selected.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    all_holdings_lists = []
    all_sources        = []
    bill_dates_found   = []
    extraction_sources = []

    for pdf_file in pdf_files:
        filename = pdf_file.filename
        bill_date, holdings, source = extract_holdings_from_pdf(pdf_file)

        if not holdings:
            flash(f"⚠️ Could not extract holdings from '{filename}'. Skipping.", "warning")
            continue

        all_holdings_lists.append(holdings)
        all_sources.append(filename)
        extraction_sources.append(source)
        if bill_date:
            bill_dates_found.append(bill_date)

    if not all_holdings_lists:
        flash("❌ Could not extract holdings from any uploaded PDF.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    combined_dp_holdings = combine_holdings(all_holdings_lists, all_sources)

    if bill_dates_found:
        bill_date = max(bill_dates_found)
    else:
        from datetime import date
        bill_date = date.today().strftime("%Y-%m-%d")
        flash("⚠️ Could not detect Bill Date from PDFs. Using today's date.", "warning")

    all_txns = get_all_transactions_for_client(client_id)

    if not all_txns:
        flash("⚠️ No transactions in app for this client. Add transactions first.", "warning")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    fmv_raw = get_fmv(client_id)
    fmv_map = {k.upper(): v for k, v in fmv_raw.items()}

    app_holdings, _, _ = get_closing_stock(all_txns, fmv_map, bill_date)

    result = reconcile(combined_dp_holdings, app_holdings)

    temp_dir  = tempfile.gettempdir()
    temp_file = os.path.join(
        temp_dir,
        f"recon_{client_id}_{int(datetime.now().timestamp())}.json"
    )

    with open(temp_file, 'w') as f:
        json.dump({
            'dp_holdings': combined_dp_holdings,
            'app_holdings': [
                {
                    'isin'    : h.get('isin', ''),
                    'company' : h.get('company', ''),
                    'quantity': h.get('quantity', 0),
                }
                for h in app_holdings
            ],
            'result'            : result,
            'bill_date'         : bill_date,
            'sources'           : all_sources,
            'extraction_sources': extraction_sources,
            'num_pdfs'          : len(all_holdings_lists),
        }, f, default=str)

    session["recon_temp_file"] = temp_file
    session["recon_client_id"] = client_id

    return redirect(url_for("reconciliation_result", client_id=client_id))


@app.route("/reconciliation_result/<int:client_id>")
def reconciliation_result(client_id):
    """Show reconciliation comparison report."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    temp_file = session.get("recon_temp_file")
    if not temp_file or not os.path.exists(temp_file):
        flash("No reconciliation data found. Please upload DP statements.", "error")
        return redirect(url_for("client_page", client_id=client_id))

    with open(temp_file, 'r') as f:
        data = json.load(f)

    return render_template(
        "reconciliation_result.html",
                client             = client,
        client_id          = client_id,
        client_name        = client[1],
        fin_year           = request.args.get("fin_year", "2024-25"),
        result             = data['result'],
        ambiguous          = data['result'].get('ambiguous', []),
        bill_date          = data['bill_date'],
        sources            = data['sources'],
        extraction_sources = data.get('extraction_sources', []),
        num_pdfs           = data.get('num_pdfs', 1)
    )


@app.route("/resolve_ambiguous/<int:client_id>", methods=["POST"])
def resolve_ambiguous(client_id):
    """User has chosen which app company matches each ambiguous DP company."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    temp_file = session.get("recon_temp_file")
    if not temp_file or not os.path.exists(temp_file):
        flash("Reconciliation data lost. Please re-upload.", "error")
        return redirect(url_for("client_page", client_id=client_id))

    with open(temp_file, 'r') as f:
        data = json.load(f)

    user_choices = {}
    for key in request.form:
        if key.startswith("choice_"):
            dp_key = key.replace("choice_", "")
            user_choices[dp_key] = request.form[key]

    result = reconcile(data['dp_holdings'], data['app_holdings'], user_choices)

    data['result'] = result
    with open(temp_file, 'w') as f:
        json.dump(data, f, default=str)

    flash(f"✅ Resolved {len(user_choices)} ambiguous match(es).", "success")
    return redirect(url_for("reconciliation_result", client_id=client_id))


@app.route("/download_reconciliation_excel/<int:client_id>")
def download_reconciliation_excel(client_id):
    """Download reconciliation report as Excel."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    temp_file = session.get("recon_temp_file")
    if not temp_file or not os.path.exists(temp_file):
        flash("No reconciliation data found.", "error")
        return redirect(url_for("client_page", client_id=client_id))

    with open(temp_file, 'r') as f:
        data = json.load(f)

    wb = export_reconciliation_to_excel(
        data['result'],
        client[1],
        data['bill_date']
    )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    safe_name = client[1].replace(' ', '_').replace('/', '_')
    bill_date_safe = data['bill_date'].replace('-', '')
    filename = f"Reconciliation_{safe_name}_{bill_date_safe}.xlsx"

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

# ============================================================
# DAY 13 (OPTION B) — MANUAL HOLDINGS EXCEL FALLBACK
# ============================================================

@app.route("/download_manual_holdings_template")
def download_manual_holdings_template():
    """Download blank Excel template for manual DP holdings entry."""

    wb = generate_manual_holdings_template()

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="DP_Holdings_Template.xlsx"
    )


@app.route("/upload_reconciliation_excel/<int:client_id>", methods=["POST"])
def upload_reconciliation_excel(client_id):
    """
    Manual Excel fallback for reconciliation.
    Used when AI/Rules parser fails — user fills a template
    with DP holdings → uploads → same reconciliation flow as PDF.
    """

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year = request.form.get("fin_year", "2024-25")

    if "holdings_excel" not in request.files:
        flash("❌ No Excel file uploaded.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    excel_file = request.files["holdings_excel"]

    if not excel_file or excel_file.filename == "":
        flash("❌ No Excel file selected.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not excel_file.filename.lower().endswith((".xlsx", ".xls")):
        flash("Only Excel files (.xlsx or .xls) are allowed.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    # Extract holdings from Excel
    bill_date, dp_holdings, source = extract_holdings_from_excel(excel_file)

    if not dp_holdings:
        flash("❌ Could not read holdings from Excel. "
              "Please check the format (ISIN in column A, "
              "Company in B, Free Qty in C, Pledged Qty in D, "
              "starting from row 4).", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not bill_date:
        flash("⚠️ Bill Date missing or invalid in cell B1 of the Excel. "
              "Please add the date (format YYYY-MM-DD) and re-upload.",
              "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    # Get app's closing stock as on bill_date
    all_txns = get_all_transactions_for_client(client_id)

    if not all_txns:
        flash("⚠️ No transactions in app for this client. "
              "Add transactions first.", "warning")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    fmv_raw = get_fmv(client_id)
    fmv_map = {k.upper(): v for k, v in fmv_raw.items()}

    app_holdings, _, _ = get_closing_stock(all_txns, fmv_map, bill_date)

    # Run reconciliation
    result = reconcile(dp_holdings, app_holdings)

    # Save to temp file (same pattern as PDF flow)
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(
        temp_dir,
        f"recon_{client_id}_{int(datetime.now().timestamp())}.json"
    )

    with open(temp_file, "w") as f:
        json.dump({
            "dp_holdings"       : dp_holdings,
            "app_holdings"      : [
                {
                    "isin"    : h.get("isin", ""),
                    "company" : h.get("company", ""),
                    "quantity": h.get("quantity", 0),
                }
                for h in app_holdings
            ],
            "result"            : result,
            "bill_date"         : bill_date,
            "sources"           : [excel_file.filename],
            "extraction_sources": ["manual"],
            "num_pdfs"          : 1,
        }, f, default=str)

    session["recon_temp_file"] = temp_file
    session["recon_client_id"] = client_id

    flash(f"✅ Loaded {len(dp_holdings)} holdings from Excel. "
          "Reconciliation complete.", "success")

    return redirect(url_for("reconciliation_result", client_id=client_id))

# ============================================================
# DAY 15 — STANDARDIZE COMPANY NAMES (from AIS/DP reconciliation)
# ============================================================

@app.route("/apply_renames/<int:client_id>", methods=["POST"])
def apply_renames(client_id):
    """
    Apply bulk renames to client transactions based on
    user-selected matches from AIS or DP reconciliation.

    Day 16 — Phase 5: Also optionally save selected names
                       as aliases in the Master DB.
    """

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    source = request.form.get("source", "ais").strip().lower()

    # Collect all selected rename entries
    renames_to_apply = []
    master_db_savs   = []   # NEW Day 16 Phase 5

    for key in request.form:
        if key.startswith("rename_"):
            value = request.form.get(key, "").strip()
            if not value:
                continue
            parts = value.split("|||")
            if len(parts) < 2:
                continue
            old_name = parts[0].strip()
            new_name = parts[1].strip()
            isin = parts[2].strip() if len(parts) > 2 else ""
            if old_name and new_name and old_name.upper() != new_name.upper():
                renames_to_apply.append((old_name, new_name, isin))

        # NEW Day 16 Phase 5: check "also save to master DB" boxes
        if key.startswith("mdb_save_"):
            value = request.form.get(key, "").strip()
            if not value:
                continue
            parts = value.split("|||")
            if len(parts) < 3:
                continue
            alias_to_save = parts[0].strip().upper()    # original (e.g. ABFRL)
            official_name = parts[1].strip().upper()    # full name from AIS/DP
            isin          = parts[2].strip().upper()
            if alias_to_save and official_name and isin:
                master_db_savs.append((alias_to_save, official_name, isin))

    if not renames_to_apply and not master_db_savs:
        flash("⚠️ No renames or master DB additions selected.", "warning")
        if source == "dp":
            return redirect(url_for("reconciliation_result",
                                    client_id=client_id))
        return redirect(url_for("ais_reconciliation_result",
                                client_id=client_id))

    # ─── Apply renames (existing logic) ───
    total_renamed = 0
    total_failed = 0
    errors = []

    for old_name, new_name, isin in renames_to_apply:
        rows, error = bulk_rename_company(
            client_id, old_name, new_name,
            isin=isin if isin else None
        )
        if error:
            total_failed += 1
            errors.append(f"{old_name}: {error}")
        else:
            total_renamed += rows

    if total_renamed > 0:
        flash(
            f"✅ Successfully renamed {total_renamed} transaction(s) "
            f"across {len(renames_to_apply) - total_failed} company(s).",
            "success"
        )
    if total_failed > 0:
        flash(f"⚠️ {total_failed} rename(s) failed.", "error")
        for err in errors[:3]:
            flash(err, "error")

    # ─── NEW Day 16 Phase 5: Save aliases to master DB ───
    from database import (
        get_security_by_isin, add_security,
        add_alias_to_security, check_alias_conflict
    )

    mdb_added     = 0
    mdb_conflicts = []
    mdb_created   = 0

    for alias, official, isin in master_db_savs:
        # Step 1: Check if security exists in master DB
        existing = get_security_by_isin(isin)

        if not existing:
            # Security doesn't exist → create it with the alias
            add_security(
                isin=isin,
                official_name=official,
                aliases=[alias],
                exchange=None
            )
            mdb_created += 1
            mdb_added   += 1
            continue

        # Step 2: Security exists → check alias conflict
        conflict_isin = check_alias_conflict(alias)
        if conflict_isin and conflict_isin != isin:
            conflict_sec = get_security_by_isin(conflict_isin)
            conf_name = conflict_sec['official_name'] if conflict_sec else "?"
            mdb_conflicts.append(
                f"'{alias}' already used by {conf_name} ({conflict_isin}) "
                f"— skipped for {official}"
            )
            continue

        # Step 3: Safely add alias to existing security
        success, _ = add_alias_to_security(isin, alias)
        if success:
            mdb_added += 1

    if mdb_added > 0:
        msg = f"📚 Master DB: Added {mdb_added} alias(es)"
        if mdb_created > 0:
            msg += f" (and created {mdb_created} new security record(s))"
        msg += "."
        flash(msg, "success")

    if mdb_conflicts:
        flash(
            f"⚠️ {len(mdb_conflicts)} alias(es) skipped due to conflicts:",
            "error"
        )
        for c in mdb_conflicts[:3]:
            flash(c, "error")

    # Redirect back to source page
    if source == "dp":
        return redirect(url_for("reconciliation_result",
                                client_id=client_id))
    return redirect(url_for("ais_reconciliation_result",
                            client_id=client_id))

# ============================================================
# DAY 15 — AIS RECONCILIATION ROUTES
# ============================================================

@app.route("/upload_ais_reconciliation/<int:client_id>", methods=["POST"])
def upload_ais_reconciliation(client_id):
    """Upload AIS PDF and run reconciliation against app transactions."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year = request.form.get("fin_year", "2024-25")

    if "ais_pdf" not in request.files:
        flash("❌ No file uploaded.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    pdf_file = request.files["ais_pdf"]

    if not pdf_file or pdf_file.filename == "":
        flash("❌ No PDF selected.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    if not pdf_file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are allowed.", "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    # Extract AIS data
    ais_data = extract_ais_from_pdf(pdf_file)

    if not ais_data:
        flash("❌ Could not extract any transactions from AIS PDF. "
              "Please ensure it is a valid decrypted AIS document.",
              "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    # Get app transactions for the selected FY
    app_transactions = get_transactions(client_id, fin_year)

    if not app_transactions:
        flash(f"⚠️ No transactions in app for FY {fin_year}. "
              "Add transactions first.", "warning")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    # Run reconciliation
    result = reconcile_ais(ais_data, app_transactions)

    # ────────────────────────────────────────────────────
    # NEW (Day 15.1) — Auto-fill missing ISINs from AIS
    # For every matched/mismatched row, if app's company
    # has no ISIN but AIS provides one, silently fill it.
    # ────────────────────────────────────────────────────
    isin_fills_count = 0
    fill_log = []

    matched_and_mismatched = (
        result.get("matched", []) + result.get("mismatched", [])
    )

    for row in matched_and_mismatched:
        ais_isin = (row.get("isin") or "").strip()
        app_company_name = (row.get("app_company") or "").strip()

        # Skip if no AIS ISIN to fill
        if not ais_isin:
            continue
        # Skip if no app company name to match against
        if not app_company_name:
            continue

        rows_filled, err = fill_missing_isin(
            client_id, app_company_name, ais_isin
        )

        if err:
            print(f"⚠️ ISIN fill error for {app_company_name}: {err}")
            continue

        if rows_filled > 0:
            isin_fills_count += rows_filled
            fill_log.append(
                f"{app_company_name} → {ais_isin} ({rows_filled} row(s))"
            )

    if isin_fills_count > 0:
        flash(
            f"✅ Auto-filled {isin_fills_count} missing ISIN(s) from AIS data.",
            "success"
        )
        print(f"🔧 AIS ISIN Auto-Fill: {isin_fills_count} rows updated")
        for entry in fill_log[:10]:
            print(f"   • {entry}")

    # Save to temp file
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(
        temp_dir,
        f"ais_recon_{client_id}_{int(datetime.now().timestamp())}.json"
    )

    # Convert app transactions to JSON-safe format
    app_tx_safe = []
    for tx in app_transactions:
        app_tx_safe.append({
            'date': tx['date'],
            'type': tx['type'],
            'company': tx['company'],
            'isin': tx['isin'] if 'isin' in tx.keys() else '',
            'quantity': tx['quantity'],
            'amount': tx['amount']
        })

    with open(temp_file, "w") as f:
        json.dump({
            "ais_data": ais_data,
            "app_transactions": app_tx_safe,
            "result": result,
            "filename": pdf_file.filename,
            "fin_year": fin_year,
        }, f, default=str)

    session["ais_recon_temp_file"] = temp_file
    session["ais_recon_client_id"] = client_id
    session["ais_recon_fin_year"] = fin_year

    total_ais = len(ais_data.get('buys', [])) + len(ais_data.get('sells', []))
    flash(f"✅ Extracted {total_ais} AIS transactions. "
          "Reconciliation complete.", "success")

    return redirect(url_for("ais_reconciliation_result",
                            client_id=client_id))


@app.route("/ais_reconciliation_result/<int:client_id>")
def ais_reconciliation_result(client_id):
    """Show AIS reconciliation comparison report."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    temp_file = session.get("ais_recon_temp_file")
    if not temp_file or not os.path.exists(temp_file):
        flash("No AIS reconciliation data found. Please upload AIS PDF.",
              "error")
        return redirect(url_for("client_page", client_id=client_id))

    with open(temp_file, "r") as f:
        data = json.load(f)

    fin_year = data.get("fin_year", "2024-25")

    return render_template(
        "ais_reconciliation_result.html",
        client=client,
        client_id=client_id,
        client_name=client[1],
        fin_year=fin_year,
        result=data["result"],
        ambiguous=data["result"].get("ambiguous", []),
        filename=data.get("filename", ""),
    )


@app.route("/resolve_ais_ambiguous/<int:client_id>", methods=["POST"])
def resolve_ais_ambiguous(client_id):
    """User has chosen which app company matches each ambiguous AIS company."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    temp_file = session.get("ais_recon_temp_file")
    if not temp_file or not os.path.exists(temp_file):
        flash("Reconciliation data lost. Please re-upload AIS PDF.", "error")
        return redirect(url_for("client_page", client_id=client_id))

    with open(temp_file, "r") as f:
        data = json.load(f)

    user_choices = {}
    for key in request.form:
        if key.startswith("choice_"):
            ais_key = key.replace("choice_", "")
            user_choices[ais_key] = request.form[key]

    # Re-run reconciliation with user choices
    result = reconcile_ais(
        data["ais_data"],
        data["app_transactions"],
        user_choices
    )

    data["result"] = result
    with open(temp_file, "w") as f:
        json.dump(data, f, default=str)

    flash(f"✅ Resolved {len(user_choices)} ambiguous match(es).", "success")
    return redirect(url_for("ais_reconciliation_result",
                            client_id=client_id))


@app.route("/download_ais_reconciliation_excel/<int:client_id>")
def download_ais_reconciliation_excel(client_id):
    """Download AIS reconciliation report as Excel."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    temp_file = session.get("ais_recon_temp_file")
    if not temp_file or not os.path.exists(temp_file):
        flash("No AIS reconciliation data found.", "error")
        return redirect(url_for("client_page", client_id=client_id))

    with open(temp_file, "r") as f:
        data = json.load(f)

    wb = export_ais_reconciliation_to_excel(
        data["result"],
        client[1],
        data.get("fin_year", "2024-25")
    )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    safe_name = client[1].replace(' ', '_').replace('/', '_')
    fy_safe = data.get("fin_year", "2024-25").replace("-", "_")
    filename = f"AIS_Reconciliation_{safe_name}_FY{fy_safe}.xlsx"

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

# ============================================================
# DAY 14 — ITR SCHEDULE CG ROUTES
# ============================================================

@app.route("/itr_schedule_cg/<int:client_id>")
def itr_schedule_cg(client_id):
    """Show ITR Schedule CG page for a client."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year = request.args.get("fin_year", "2024-25")

    # Get available FY list
    from database import get_financial_years
    available_years = get_financial_years(client_id)

    if not available_years:
        flash("No transactions found. Please add transactions first.", "warning")
        return redirect(url_for("client_page", client_id=client_id))

    if fin_year not in available_years:
        fin_year = available_years[0]

    # Generate schedule
    schedule = generate_schedule_cg(client_id, fin_year)

    if "error" in schedule:
        flash(schedule["error"], "error")
        return redirect(url_for("client_page",
                                client_id=client_id,
                                fin_year=fin_year))

    return render_template(
        "itr_schedule.html",
        client          = client,
        client_id       = client[0],
        client_name     = client[1],
        schedule        = schedule,
        fin_year        = fin_year,
        available_years = available_years,
    )


@app.route("/download_itr_excel/<int:client_id>")
def download_itr_excel(client_id):
    """Download ITR Schedule CG as Excel."""

    client = get_client(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    if client_has_password(client_id):
        if not session.get(f"unlocked_{client_id}"):
            flash("🔒 Please unlock the client first.", "error")
            return redirect(url_for("dashboard"))

    fin_year = request.args.get("fin_year", "2024-25")
    mode     = request.args.get("mode", "detailed").strip().lower()

    if mode not in ["detailed", "adjusted"]:
        mode = "detailed"

    schedule = generate_schedule_cg(client_id, fin_year)

    if "error" in schedule:
        flash(schedule["error"], "error")
        return redirect(url_for(
            "client_page",
            client_id=client_id,
            fin_year=fin_year
        ))

    excel_file = export_schedule_cg_to_excel(schedule, mode=mode)

    safe_name  = client[1].replace(" ", "_").replace("/", "_")
    fy_safe    = fin_year.replace("-", "_")
    mode_label = "Detailed" if mode == "detailed" else "Adjusted"
    filename   = f"ITR_ScheduleCG_{safe_name}_FY{fy_safe}_{mode_label}.xlsx"

    return send_file(
        excel_file,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ============================================================
# RUN APP
# ============================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
