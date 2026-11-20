from datetime import date, datetime, timedelta


def is_close(first: float, second: float) -> bool:
    tolerance = max(0.05, abs(second) * 0.005)
    return abs(first - second) <= tolerance


def validate_invoice(data: dict, injection_risk: str) -> dict:
    checks = []

    def add(name: str, status: str, detail: str):
        checks.append({"name": name, "status": status, "detail": detail})

    total = data.get("total")
    if total is not None:
        add("Total found", "passed", f"Total is {total:.2f}")
    else:
        add("Total found", "failed", "No total amount was found")

    if data.get("vendor"):
        add("Vendor found", "passed", data["vendor"])
    else:
        add("Vendor found", "failed", "No vendor name was found")

    if data.get("invoice_number"):
        add("Invoice number found", "passed", data["invoice_number"])
    else:
        add("Invoice number found", "failed", "No invoice number was found")

    invoice_date = data.get("invoice_date")
    if invoice_date is None:
        add("Invoice date is valid", "failed", "No valid date in YYYY-MM-DD format")
    else:
        parsed = datetime.strptime(invoice_date, "%Y-%m-%d").date()
        too_old = parsed < date(1990, 1, 1)
        too_far = parsed > date.today() + timedelta(days=400)
        if too_old or too_far:
            add("Invoice date is valid", "failed", f"{invoice_date} looks unrealistic")
        else:
            add("Invoice date is valid", "passed", invoice_date)

    items = data.get("line_items") or []
    if not items:
        add("Line items found", "skipped", "No line items were found")
    else:
        add("Line items found", "passed", f"{len(items)} item(s)")

    problems = []
    testable = 0
    for number, item in enumerate(items, start=1):
        quantity = item.get("quantity")
        unit_price = item.get("unit_price")
        amount = item.get("amount")
        if quantity is None or unit_price is None or amount is None:
            continue
        testable += 1
        expected = quantity * unit_price
        if not is_close(expected, amount):
            problems.append(
                f"Line {number}: {quantity:g} x {unit_price:.2f} is {expected:.2f}, but the amount says {amount:.2f}"
            )

    if testable == 0:
        add("Line amounts match quantity x price", "skipped", "Not enough data to check")
    elif problems:
        add("Line amounts match quantity x price", "failed", "; ".join(problems[:3]))
    else:
        add("Line amounts match quantity x price", "passed", f"{testable} line(s) checked")

    subtotal = data.get("subtotal")
    amounts = [item.get("amount") for item in items]
    can_sum = bool(items) and subtotal is not None and None not in amounts
    if not can_sum:
        add("Line items add up to subtotal", "skipped", "Not enough data to check")
    else:
        items_sum = sum(amounts)
        if is_close(items_sum, subtotal):
            add("Line items add up to subtotal", "passed", f"{items_sum:.2f} matches")
        else:
            add(
                "Line items add up to subtotal",
                "failed",
                f"Items add up to {items_sum:.2f}, but the subtotal says {subtotal:.2f}",
            )

    tax = data.get("tax")
    if total is None or subtotal is None:
        add("Subtotal plus tax equals total", "skipped", "Not enough data to check")
    else:
        expected_total = subtotal + (tax or 0)
        if is_close(expected_total, total):
            add("Subtotal plus tax equals total", "passed", f"{expected_total:.2f} matches")
        else:
            add(
                "Subtotal plus tax equals total",
                "failed",
                f"Subtotal plus tax is {expected_total:.2f}, but the total says {total:.2f}",
            )

    if injection_risk == "none":
        add("Document safety scan", "passed", "No instruction-like text found")
    else:
        add(
            "Document safety scan",
            "failed",
            f"Risk level {injection_risk}: instruction-like text was found, so a person should review this",
        )

    passed = sum(1 for check in checks if check["status"] == "passed")
    failed = sum(1 for check in checks if check["status"] == "failed")
    skipped = sum(1 for check in checks if check["status"] == "skipped")

    return {
        "status": "needs_review" if failed else "ok",
        "reasons": [check["name"] for check in checks if check["status"] == "failed"],
        "summary": f"{passed} passed, {failed} failed, {skipped} skipped",
        "checks": checks,
    }