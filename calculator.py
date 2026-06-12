# calculator.py
# ============================================================
# FIFO Capital Gains Engine
# UPDATED: Day 11 — Added brought-forward loss application
# UPDATED: Day 12 — Added get_consolidated_holdings
# UPDATED: Day 12.5 — get_consolidated_holdings now lot-wise
# UPDATED: Day 12.5 — Added rate_per_share (with buy expenses adjustment)
# ============================================================

from datetime import date, datetime
from rules import get_rule


def parse_date(date_input):
    if isinstance(date_input, date):
        return date_input

    if isinstance(date_input, str):
        for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"]:
            try:
                return datetime.strptime(date_input, fmt).date()
            except ValueError:
                continue

    raise ValueError(f"Cannot parse date: {date_input}")


def get_holding_days(buy_date, sell_date):
    return (sell_date - buy_date).days


def get_gain_type(buy_date, sell_date):
    days  = get_holding_days(buy_date, sell_date)
    limit = get_rule("ltcg_holding_days")
    return "LTCG" if days >= limit else "STCG"


def apply_grandfathering(buy_unit_price, fmv_price, sell_unit_price, quantity):
    deemed_cost_per_share = max(
        buy_unit_price,
        min(fmv_price, sell_unit_price)
    )
    return quantity * deemed_cost_per_share


def apply_stock_split(buy_queue, split_ratio, split_date):
    for lot in buy_queue:
        if lot["date"] < split_date:
            lot["quantity"] = int(lot["quantity"] * split_ratio)
    return buy_queue


def get_company_key(isin, company):
    isin_clean = str(isin).strip().upper() if isin else ""
    if isin_clean and len(isin_clean) == 12 and isin_clean[:2].isalpha():
        return isin_clean
    return str(company).upper().strip()


def get_display_name(transactions_for_key):
    names = [t["company"] for t in transactions_for_key]
    if not names:
        return "UNKNOWN"
    return max(set(names), key=names.count)


def run_fifo(transactions, fmv_data):
    grandfather_date = get_rule("grandfather_date")

    cleaned = []
    for t in transactions:
        isin    = str(t.get("isin", "") or "").strip().upper()
        company = str(t["company"]).upper().strip()
        key     = get_company_key(isin, company)

        cleaned.append({
            "date"         : parse_date(t["date"]),
            "type"         : str(t["type"]).upper().strip(),
            "company"      : company,
            "isin"         : isin,
            "key"          : key,
            "quantity"     : int(t["quantity"]),
            "amount"       : float(t["amount"]),
            "buy_expenses" : float(t.get("buy_expenses",  0) or 0),
            "sell_expenses": float(t.get("sell_expenses", 0) or 0),
            "stt"          : float(t.get("stt", 0) or 0),
            "notes"        : t.get("notes", "") or "",
        })

    keys = sorted(set(t["key"] for t in cleaned))

    output_rows = []
    errors      = []

    for key in keys:

        key_txns     = [t for t in cleaned if t["key"] == key]
        display_name = get_display_name(key_txns)

        fmv = fmv_data.get(key, None)
        if fmv is None:
            fmv = fmv_data.get(display_name, None)

        key_txns.sort(key=lambda x: x["date"])

        buy_queue = []

        for t in key_txns:

            if t["type"] == "BUY":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : t["amount"],
                    "buy_expenses" : t["buy_expenses"],
                    "stt"          : t["stt"],
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : False,
                    "is_gift"      : False,
                    "is_inherited" : False,
                    "label"        : "BUY",
                })

            elif t["type"] == "BONUS":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : 0.0,
                    "buy_expenses" : 0.0,
                    "stt"          : 0.0,
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : True,
                    "is_gift"      : False,
                    "is_inherited" : False,
                    "label"        : "BONUS",
                })

            elif t["type"] == "GIFT":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : t["amount"],
                    "buy_expenses" : t["buy_expenses"],
                    "stt"          : t["stt"],
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : False,
                    "is_gift"      : True,
                    "is_inherited" : False,
                    "label"        : "GIFT",
                    "user_notes"   : t.get("notes", ""),
                })

            elif t["type"] == "INHERIT":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : t["amount"],
                    "buy_expenses" : t["buy_expenses"],
                    "stt"          : t["stt"],
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : False,
                    "is_gift"      : False,
                    "is_inherited" : True,
                    "label"        : "INHERIT",
                    "user_notes"   : t.get("notes", ""),
                })

            elif t["type"] == "SPLIT":
                try:
                    split_ratio = float(t["amount"])
                    if split_ratio <= 0:
                        errors.append(
                            f"SPLIT ERROR: {display_name} on {t['date']} — "
                            f"ratio must be > 0 (found: {split_ratio})"
                        )
                        continue
                    buy_queue = apply_stock_split(
                        buy_queue, split_ratio, t["date"]
                    )
                except Exception as e:
                    errors.append(
                        f"SPLIT ERROR: {display_name} on {t['date']} — {str(e)}"
                    )

        buy_queue.sort(key=lambda x: x["date"])

        sell_list = [
            t for t in key_txns
            if t["type"] in ["SELL", "BUYBACK"]
        ]
        sell_list.sort(key=lambda x: x["date"])

        for sell in sell_list:

            remaining_sell_qty = sell["quantity"]
            sell_unit_price    = sell["amount"] / sell["quantity"]
            is_buyback         = (sell["type"] == "BUYBACK")
            sell_stt_total     = sell.get("stt", 0)

            total_available = sum(
                b["quantity"] for b in buy_queue if b["quantity"] > 0
            )
            if remaining_sell_qty > total_available:
                errors.append(
                    f"OVERSOLD: {display_name} — "
                    f"Tried to sell {remaining_sell_qty} "
                    f"but only {total_available} available"
                )

            for buy in buy_queue:

                if remaining_sell_qty <= 0:
                    break
                if buy["quantity"] <= 0:
                    continue

                take = min(remaining_sell_qty, buy["quantity"])

                if buy["is_bonus"]:
                    buy_unit_price = 0.0
                else:
                    buy_unit_price = (
                        buy["amount"] / buy["quantity"]
                        if buy["quantity"] > 0 else 0.0
                    )

                is_pre_2018 = buy["date"] < grandfather_date
                raw_cost    = take * buy_unit_price

                buy_exp_portion = (
                    (take / buy["quantity"]) * buy["buy_expenses"]
                    if buy["quantity"] > 0 else 0
                )

                buy_stt_portion = (
                    (take / buy["quantity"]) * buy.get("stt", 0)
                    if buy["quantity"] > 0 else 0
                )

                adjusted_cost     = raw_cost + buy_exp_portion
                gross_proceeds    = take * sell_unit_price

                sell_exp_portion = (
                    (take / sell["quantity"]) * sell["sell_expenses"]
                    if sell["quantity"] > 0 else 0
                )

                sell_stt_portion = (
                    (take / sell["quantity"]) * sell_stt_total
                    if sell["quantity"] > 0 else 0
                )

                adjusted_proceeds = gross_proceeds - sell_exp_portion

                gain_type   = get_gain_type(buy["date"], sell["date"])
                fair_cost   = None
                grandfather = False
                final_cost  = adjusted_cost

                if (is_pre_2018
                        and fmv is not None
                        and gain_type == "LTCG"
                        and not buy["is_bonus"]):

                    grandfathered_total = apply_grandfathering(
                        buy_unit_price,
                        fmv,
                        sell_unit_price,
                        take
                    )
                    fair_cost   = take * fmv
                    final_cost  = grandfathered_total
                    grandfather = True

                final_pl = adjusted_proceeds - final_cost

                total_stt = round(buy_stt_portion + sell_stt_portion, 2)

                notes = []

                if buy["is_bonus"]:
                    notes.append(
                        f"🎁 BONUS shares (allotted {buy['date'].strftime('%d-%m-%Y')}) "
                        f"— Cost ₹0 as per Sec 55"
                    )

                if buy["is_gift"]:
                    user_note = buy.get("user_notes", "")
                    notes.append(
                        f"🎁 GIFTED shares (donor's purchase date: "
                        f"{buy['date'].strftime('%d-%m-%Y')}) "
                        f"— Sec 49(1): donor's cost adopted"
                        + (f" | {user_note}" if user_note else "")
                    )

                if buy["is_inherited"]:
                    user_note = buy.get("user_notes", "")
                    notes.append(
                        f"🏛️ INHERITED shares (original purchase date: "
                        f"{buy['date'].strftime('%d-%m-%Y')}) "
                        f"— Sec 49(1): predecessor's cost adopted"
                        + (f" | {user_note}" if user_note else "")
                    )

                if is_buyback:
                    notes.append(
                        "Buyback — taxed as Capital Gains (post 1-Apr-2026)"
                    )

                if grandfather:
                    notes.append(
                        f"Grandfathering applied (Sec 112A) | "
                        f"FMV on 31-Jan-2018: ₹{fmv}"
                    )

                if is_pre_2018 and fmv is None and not buy["is_bonus"]:
                    notes.append(
                        "⚠️ Warning: Pre-2018 purchase but FMV not provided"
                    )

                if take > 0:
                    output_rows.append({
                        "company"              : display_name,
                        "isin"                 : key if len(key) == 12 else buy.get("isin", ""),
                        "buy_date"             : buy["date"].strftime("%d-%m-%Y"),
                        "sell_date"            : sell["date"].strftime("%d-%m-%Y"),
                        "holding_days"         : get_holding_days(buy["date"], sell["date"]),
                        "shares"               : take,
                        "buy_price_per_share"  : round(buy_unit_price, 4),
                        "buy_expenses_portion" : round(buy_exp_portion, 2),
                        "adjusted_cost"        : round(adjusted_cost, 2),
                        "sell_price_per_share" : round(sell_unit_price, 4),
                        "sell_expenses_portion": round(sell_exp_portion, 2),
                        "adjusted_proceeds"    : round(adjusted_proceeds, 2),
                        "stt_paid"             : total_stt,
                        "fmv_31_jan_2018"      : fmv,
                        "fair_cost"            : round(fair_cost, 2) if fair_cost else None,
                        "grandfathering"       : grandfather,
                        "final_cost_used"      : round(final_cost, 2),
                        "profit_loss"          : round(final_pl, 2),
                        "gain_type"            : gain_type,
                        "tx_type"              : sell["type"],
                        "buy_lot_label"        : buy["label"],
                        "is_bonus"             : buy["is_bonus"],
                        "is_gift"              : buy["is_gift"],
                        "is_inherited"         : buy["is_inherited"],
                        "notes"                : " | ".join(notes) if notes else ""
                    })

                buy["quantity"] -= take
                buy["amount"]   -= raw_cost
                buy["buy_expenses"] = max(0, buy["buy_expenses"] - buy_exp_portion)
                buy["stt"]          = max(0, buy.get("stt", 0) - buy_stt_portion)
                remaining_sell_qty -= take

    return output_rows, errors


# ============================================================
# BROUGHT FORWARD LOSS APPLICATION
# ============================================================

def apply_brought_forward_losses(net_stcg, net_ltcg, available_losses):
    remaining_stcg = float(net_stcg)
    remaining_ltcg = float(net_ltcg)

    application_log = []
    updated_losses  = []

    for loss in available_losses:

        stcl_avail = float(loss.get("stcl_remaining", 0))
        ltcl_avail = float(loss.get("ltcl_remaining", 0))

        stcl_used_now = 0.0
        ltcl_used_now = 0.0

        if stcl_avail > 0 and remaining_stcg > 0:
            apply = min(stcl_avail, remaining_stcg)
            remaining_stcg -= apply
            stcl_avail     -= apply
            stcl_used_now  += apply
            application_log.append({
                "loss_year" : loss["loss_year"],
                "loss_type" : "STCL",
                "applied_to": "STCG",
                "amount"    : round(apply, 2),
            })

        if stcl_avail > 0 and remaining_ltcg > 0:
            apply = min(stcl_avail, remaining_ltcg)
            remaining_ltcg -= apply
            stcl_avail     -= apply
            stcl_used_now  += apply
            application_log.append({
                "loss_year" : loss["loss_year"],
                "loss_type" : "STCL",
                "applied_to": "LTCG",
                "amount"    : round(apply, 2),
            })

        if ltcl_avail > 0 and remaining_ltcg > 0:
            apply = min(ltcl_avail, remaining_ltcg)
            remaining_ltcg -= apply
            ltcl_avail     -= apply
            ltcl_used_now  += apply
            application_log.append({
                "loss_year" : loss["loss_year"],
                "loss_type" : "LTCL",
                "applied_to": "LTCG",
                "amount"    : round(apply, 2),
            })

        if stcl_used_now > 0 or ltcl_used_now > 0:
            new_stcl_used = float(loss.get("stcl_used", 0)) + stcl_used_now
            new_ltcl_used = float(loss.get("ltcl_used", 0)) + ltcl_used_now
            updated_losses.append({
                "id"       : loss["id"],
                "stcl_used": round(new_stcl_used, 2),
                "ltcl_used": round(new_ltcl_used, 2),
            })

        if remaining_stcg <= 0 and remaining_ltcg <= 0:
            break

    return (
        round(remaining_stcg, 2),
        round(remaining_ltcg, 2),
        application_log,
        updated_losses
    )


# ============================================================
# TAX SUMMARY
# ============================================================

def calculate_tax_summary(output_rows, brought_forward_losses=None,
                          apply_bf=False):
    ltcg_gains  = 0.0
    ltcg_losses = 0.0
    stcg_gains  = 0.0
    stcg_losses = 0.0
    total_stt   = 0.0

    for row in output_rows:
        pl        = row["profit_loss"]
        gain_type = row["gain_type"]
        total_stt += row.get("stt_paid", 0)

        if gain_type == "LTCG":
            if pl >= 0:
                ltcg_gains  += pl
            else:
                ltcg_losses += abs(pl)
        else:
            if pl >= 0:
                stcg_gains  += pl
            else:
                stcg_losses += abs(pl)

    net_stcg_before_setoff = stcg_gains - stcg_losses
    net_ltcg_before_setoff = ltcg_gains - ltcg_losses

    if net_stcg_before_setoff >= 0:
        net_stcg       = net_stcg_before_setoff
        stcl_remaining = 0.0
    else:
        net_stcg       = 0.0
        stcl_remaining = abs(net_stcg_before_setoff)

    if net_ltcg_before_setoff >= 0:
        net_ltcg       = net_ltcg_before_setoff
        ltcl_remaining = 0.0
    else:
        net_ltcg       = 0.0
        ltcl_remaining = abs(net_ltcg_before_setoff)

    if stcl_remaining > 0 and net_ltcg > 0:
        reduction      = min(stcl_remaining, net_ltcg)
        net_ltcg      -= reduction
        stcl_remaining -= reduction

    bf_log          = []
    bf_updates      = []
    bf_stcg_applied = 0.0
    bf_ltcg_applied = 0.0

    if apply_bf and brought_forward_losses:
        stcg_before_bf = net_stcg
        ltcg_before_bf = net_ltcg

        net_stcg, net_ltcg, bf_log, bf_updates = apply_brought_forward_losses(
            net_stcg, net_ltcg, brought_forward_losses
        )

        bf_stcg_applied = round(stcg_before_bf - net_stcg, 2)
        bf_ltcg_applied = round(ltcg_before_bf - net_ltcg, 2)

    exemption    = get_rule("ltcg_exemption")
    taxable_ltcg = max(0, net_ltcg - exemption)

    ltcg_tax  = taxable_ltcg * get_rule("ltcg_rate")
    stcg_tax  = net_stcg     * get_rule("stcg_rate")
    total_tax = ltcg_tax + stcg_tax

    cess        = total_tax * get_rule("cess_rate")
    grand_total = total_tax + cess

    return {
        "gross_ltcg"        : round(ltcg_gains,  2),
        "gross_ltcl"        : round(ltcg_losses, 2),
        "gross_stcg"        : round(stcg_gains,  2),
        "gross_stcl"        : round(stcg_losses, 2),
        "net_ltcg"          : round(net_ltcg,    2),
        "net_stcg"          : round(net_stcg,    2),
        "ltcg_exemption"    : exemption,
        "taxable_ltcg"      : round(taxable_ltcg, 2),
        "taxable_stcg"      : round(net_stcg,     2),
        "ltcg_tax"          : round(ltcg_tax,    2),
        "stcg_tax"          : round(stcg_tax,    2),
        "total_tax"         : round(total_tax,   2),
        "cess"              : round(cess,         2),
        "grand_total"       : round(grand_total,  2),
        "ltcl_carryforward" : round(ltcl_remaining, 2),
        "stcl_carryforward" : round(stcl_remaining,  2),
        "total_stt_paid"    : round(total_stt,    2),
        "bf_applied"        : apply_bf,
        "bf_stcg_applied"   : bf_stcg_applied,
        "bf_ltcg_applied"   : bf_ltcg_applied,
        "bf_application_log": bf_log,
        "bf_loss_updates"   : bf_updates,
    }


def detect_missing_fmv(transactions, fmv_data):
    grandfather_date = get_rule("grandfather_date")

    missing      = []
    keys_checked = set()

    cleaned = []
    for t in transactions:
        isin    = str(t.get("isin", "") or "").strip().upper()
        company = str(t["company"]).upper().strip()
        key     = get_company_key(isin, company)

        cleaned.append({
            "date"    : parse_date(t["date"]),
            "type"    : str(t["type"]).upper().strip(),
            "company" : company,
            "key"     : key,
            "quantity": int(t["quantity"]),
        })

    keys = sorted(set(t["key"] for t in cleaned))

    for key in keys:
        if key in keys_checked:
            continue
        keys_checked.add(key)

        key_txns     = [t for t in cleaned if t["key"] == key]
        display_name = get_display_name(key_txns)

        pre_2018_buys = [
            t for t in key_txns
            if t["type"] in ["BUY", "GIFT", "INHERIT"]
            and t["date"] < grandfather_date
        ]

        sells = [
            t for t in key_txns
            if t["type"] in ["SELL", "BUYBACK"]
        ]

        fmv_exists = (key in fmv_data) or (display_name in fmv_data)

        if pre_2018_buys and sells and not fmv_exists:
            missing.append({
                "company"           : display_name,
                "pre_2018_buys"     : len(pre_2018_buys),
                "total_pre_2018_qty": sum(b["quantity"] for b in pre_2018_buys),
                "earliest_buy_date" : min(b["date"] for b in pre_2018_buys).strftime("%d-%m-%Y"),
                "has_sells"         : True,
                "sells_count"       : len(sells)
            })

    return missing


# ============================================================
# CLOSING STOCK
# ============================================================

def get_closing_stock(transactions, fmv_data, as_on_date):
    grandfather_date = get_rule("grandfather_date")
    ltcg_days        = get_rule("ltcg_holding_days")

    as_on_date = parse_date(as_on_date)

    cleaned = []
    for t in transactions:
        isin    = str(t.get("isin", "") or "").strip().upper()
        company = str(t["company"]).upper().strip()
        key     = get_company_key(isin, company)
        t_date  = parse_date(t["date"])

        if t_date > as_on_date:
            continue

        cleaned.append({
            "date"         : t_date,
            "type"         : str(t["type"]).upper().strip(),
            "company"      : company,
            "isin"         : isin,
            "key"          : key,
            "quantity"     : int(t["quantity"]),
            "amount"       : float(t["amount"]),
            "buy_expenses" : float(t.get("buy_expenses",  0) or 0),
            "sell_expenses": float(t.get("sell_expenses", 0) or 0),
            "stt"          : float(t.get("stt", 0) or 0),
            "notes"        : t.get("notes", "") or "",
        })

    keys = sorted(set(t["key"] for t in cleaned))

    holdings = []
    errors   = []

    for key in keys:

        key_txns     = [t for t in cleaned if t["key"] == key]
        display_name = get_display_name(key_txns)
        isin_display = key if (len(key) == 12 and key[:2].isalpha()) else ""

        fmv = fmv_data.get(key, None)
        if fmv is None:
            fmv = fmv_data.get(display_name, None)

        key_txns.sort(key=lambda x: x["date"])

        buy_queue = []

        for t in key_txns:

            if t["type"] == "BUY":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : t["amount"],
                    "buy_expenses" : t["buy_expenses"],
                    "stt"          : t["stt"],
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : False,
                    "is_gift"      : False,
                    "is_inherited" : False,
                    "label"        : "BUY",
                    "notes"        : t.get("notes", ""),
                })

            elif t["type"] == "BONUS":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : 0.0,
                    "buy_expenses" : 0.0,
                    "stt"          : 0.0,
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : True,
                    "is_gift"      : False,
                    "is_inherited" : False,
                    "label"        : "BONUS",
                    "notes"        : t.get("notes", ""),
                })

            elif t["type"] == "GIFT":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : t["amount"],
                    "buy_expenses" : t["buy_expenses"],
                    "stt"          : t["stt"],
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : False,
                    "is_gift"      : True,
                    "is_inherited" : False,
                    "label"        : "GIFT",
                    "notes"        : t.get("notes", ""),
                })

            elif t["type"] == "INHERIT":
                buy_queue.append({
                    "date"         : t["date"],
                    "quantity"     : t["quantity"],
                    "amount"       : t["amount"],
                    "buy_expenses" : t["buy_expenses"],
                    "stt"          : t["stt"],
                    "isin"         : t["isin"],
                    "company"      : t["company"],
                    "is_bonus"     : False,
                    "is_gift"      : False,
                    "is_inherited" : True,
                    "label"        : "INHERIT",
                    "notes"        : t.get("notes", ""),
                })

            elif t["type"] == "SPLIT":
                try:
                    split_ratio = float(t["amount"])
                    if split_ratio > 0:
                        buy_queue = apply_stock_split(
                            buy_queue, split_ratio, t["date"]
                        )
                    else:
                        errors.append(
                            f"SPLIT ERROR (closing stock): {display_name} "
                            f"on {t['date']} — ratio must be > 0"
                        )
                except Exception as e:
                    errors.append(
                        f"SPLIT ERROR (closing stock): {display_name} "
                        f"on {t['date']} — {str(e)}"
                    )

        buy_queue.sort(key=lambda x: x["date"])

        sell_list = [
            t for t in key_txns
            if t["type"] in ["SELL", "BUYBACK"]
        ]
        sell_list.sort(key=lambda x: x["date"])

        for sell in sell_list:
            remaining = sell["quantity"]
            for buy in buy_queue:
                if remaining <= 0:
                    break
                if buy["quantity"] <= 0:
                    continue
                take             = min(remaining, buy["quantity"])
                raw_cost         = (buy["amount"] / buy["quantity"]) * take if buy["quantity"] > 0 else 0
                # Reduce buy_expenses proportionally too
                buy_exp_portion  = (take / buy["quantity"]) * buy["buy_expenses"] if buy["quantity"] > 0 else 0
                buy["quantity"] -= take
                buy["amount"]   -= raw_cost
                buy["buy_expenses"] = max(0, buy["buy_expenses"] - buy_exp_portion)
                remaining       -= take

        total_bonus_qty = sum(
            b["quantity"] for b in buy_queue
            if b["is_bonus"] and b["quantity"] > 0
        )

        for lot in buy_queue:
            if lot["quantity"] <= 0:
                continue

            qty          = lot["quantity"]
            is_bonus     = lot["is_bonus"]
            is_gift      = lot["is_gift"]
            is_inherited = lot["is_inherited"]
            buy_date     = lot["date"]
            label        = lot["label"]

            if is_bonus:
                cost_per_share = 0.0
            else:
                cost_per_share = (
                    lot["amount"] / lot["quantity"]
                    if lot["quantity"] > 0 else 0.0
                )

            total_cost = cost_per_share * qty
            buy_exp    = lot.get("buy_expenses", 0)

            # NEW Day 12.5 — Rate per share (cost adjusted with buy expenses)
            # If buy_expenses > 0 → (amount + buy_expenses) / qty
            # If buy_expenses = 0 → amount / qty (assume amount already includes)
            if is_bonus:
                rate_per_share = 0.0
            elif qty > 0:
                rate_per_share = (total_cost + buy_exp) / qty
            else:
                rate_per_share = 0.0

            is_pre_2018         = buy_date < grandfather_date
            grandfathered_cost  = None
            grandfathered_total = None

            if is_pre_2018 and fmv is not None and not is_bonus:
                grandfathered_cost  = round(fmv, 2)
                grandfathered_total = round(fmv * qty, 2)

            holding_days = (as_on_date - buy_date).days
            status = "LTCG" if holding_days >= ltcg_days else "STCG"

            lot_notes = []

            if is_gift:
                user_note = lot.get("notes", "")
                lot_notes.append(
                    "🎁 Gifted shares — donor's cost & date adopted (Sec 49(1))"
                    + (f" | {user_note}" if user_note else "")
                )

            if is_inherited:
                user_note = lot.get("notes", "")
                lot_notes.append(
                    "🏛️ Inherited shares — predecessor's cost & date adopted (Sec 49(1))"
                    + (f" | {user_note}" if user_note else "")
                )

            if is_pre_2018 and fmv is None and not is_bonus:
                lot_notes.append(
                    "⚠️ Pre-2018 purchase — FMV not entered, "
                    "grandfathered cost not available"
                )

            holdings.append({
                "company"                      : display_name,
                "isin"                         : isin_display,
                "label"                        : label,
                "buy_date"                     : buy_date.strftime("%d-%m-%Y"),
                "buy_date_raw"                 : buy_date,
                "quantity"                     : qty,
                "cost_per_share"               : round(cost_per_share, 4),
                "rate_per_share"               : round(rate_per_share, 4),   # NEW
                "total_cost"                   : round(total_cost, 2),
                "buy_expenses"                 : round(buy_exp, 2),
                "is_pre_2018"                  : is_pre_2018,
                "fmv_31_jan_2018"              : fmv,
                "grandfathered_cost_per_share" : grandfathered_cost,
                "grandfathered_total"          : grandfathered_total,
                "holding_days"                 : holding_days,
                "holding_years"                : round(holding_days / 365, 1),
                "status"                       : status,
                "is_bonus"                     : is_bonus,
                "is_gift"                      : is_gift,
                "is_inherited"                 : is_inherited,
                "company_bonus_qty"            : total_bonus_qty,
                "notes"                        : " | ".join(lot_notes) if lot_notes else "",
            })

    holdings.sort(key=lambda x: (x["company"], x["buy_date_raw"]))

    summary = _build_closing_stock_summary(holdings, as_on_date)

    return holdings, summary, errors


def _build_closing_stock_summary(holdings, as_on_date):
    if not holdings:
        return {
            "total_companies"    : 0,
            "total_shares"       : 0,
            "total_cost"         : 0.0,
            "total_grandfathered": 0.0,
            "ltcg_shares"        : 0,
            "stcg_shares"        : 0,
            "ltcg_companies"     : 0,
            "stcg_companies"     : 0,
            "as_on_date"         : as_on_date.strftime("%d-%m-%Y"),
        }

    companies      = set(h["company"] for h in holdings)
    ltcg_companies = set(h["company"] for h in holdings if h["status"] == "LTCG")
    stcg_companies = set(h["company"] for h in holdings if h["status"] == "STCG")

    total_shares = sum(h["quantity"] for h in holdings)
    total_cost   = sum(h["total_cost"] for h in holdings)

    total_grandfathered = 0.0
    for h in holdings:
        if h["grandfathered_total"] is not None:
            total_grandfathered += h["grandfathered_total"]
        else:
            total_grandfathered += h["total_cost"]

    ltcg_shares = sum(h["quantity"] for h in holdings if h["status"] == "LTCG")
    stcg_shares = sum(h["quantity"] for h in holdings if h["status"] == "STCG")

    return {
        "total_companies"    : len(companies),
        "total_shares"       : total_shares,
        "total_cost"         : round(total_cost, 2),
        "total_grandfathered": round(total_grandfathered, 2),
        "ltcg_shares"        : ltcg_shares,
        "stcg_shares"        : stcg_shares,
        "ltcg_companies"     : len(ltcg_companies),
        "stcg_companies"     : len(stcg_companies),
        "as_on_date"         : as_on_date.strftime("%d-%m-%Y"),
    }


# ============================================================
# CONSOLIDATED PORTFOLIO — UPDATED Day 12.5
# Now includes rate_per_share for each lot
# ============================================================

def get_consolidated_holdings(client_ids, db):
    """
    For multiple clients, get their current holdings as of today.
    Returns LOT-WISE data — each buy lot is a separate row.

    Args:
        client_ids : list of client IDs selected by user
        db         : the database module

    Returns:
        client_names : dict { client_id -> client_name }
        lots         : list of dicts, each with:
                       company, isin, buy_date, buy_date_raw,
                       cost_per_share, client_lots (each has qty, amt, rate)
    """
    from datetime import date as date_cls
    today = date_cls.today().strftime("%Y-%m-%d")

    client_names = {}

    # Step 1: Collect every individual lot from every client
    raw_lots = {}

    for client_id in client_ids:

        client = db.get_client(client_id)
        if not client:
            continue
        client_names[client_id] = client[1]

        all_tx = db.get_all_transactions_for_client(client_id)
        if not all_tx:
            continue

        fmv_raw = db.get_fmv(client_id)
        fmv_map = {k.upper(): v for k, v in fmv_raw.items()}

        holdings_list, _, _ = get_closing_stock(all_tx, fmv_map, today)

        for lot in holdings_list:
            company        = lot["company"]
            isin           = lot["isin"]
            buy_date       = lot["buy_date"]
            buy_date_raw   = lot["buy_date_raw"]
            qty            = lot["quantity"]
            total_cost     = lot["total_cost"]
            cost_per_share = lot["cost_per_share"]
            rate_per_share = lot["rate_per_share"]   # NEW

            if qty <= 0:
                continue

            key = (company, isin, buy_date, buy_date_raw, client_id,
                   round(cost_per_share, 4))

            if key not in raw_lots:
                raw_lots[key] = {"qty": 0, "amt": 0.0, "rate": rate_per_share}

            raw_lots[key]["qty"]  += qty
            raw_lots[key]["amt"]  += total_cost
            raw_lots[key]["rate"] = rate_per_share

    # Step 2: Group into rows by (company, isin, buy_date, cost)
    row_groups = {}

    for key, data in raw_lots.items():
        company, isin, buy_date, buy_date_raw, client_id, cost = key

        row_key = (company, isin, buy_date, cost)

        if row_key not in row_groups:
            row_groups[row_key] = {
                "company"        : company,
                "isin"           : isin,
                "buy_date"       : buy_date,
                "buy_date_raw"   : buy_date_raw,
                "cost_per_share" : cost,
                "client_lots"    : {}
            }

        row_groups[row_key]["client_lots"][client_id] = {
            "qty" : data["qty"],
            "amt" : round(data["amt"], 2),
            "rate": round(data["rate"], 4)
        }

    lots = list(row_groups.values())
    lots.sort(key=lambda x: (
        x["company"],
        x["buy_date_raw"],
        x["cost_per_share"]
    ))

    return client_names, lots