from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError

DONE_STATES = ("completed", "paid", "closed")


class SalonDashboard(models.AbstractModel):
    _name = "salon.dashboard"
    _description = "Salon Dashboard Data"

    @api.model
    def get_dashboard_data(self, date_from=False, date_to=False, employee_id=False, product_id=False):
        """KPIs and chart data. All figures come from live transactional records.

        Definitions: Revenue = net amount (after discount, before tax) of Done lines.
        No-show rate = no-shows / bookings that were confirmed or later (excl. draft, cancelled).
        Estimated profit = revenue - commission - approved expenses (not accounting profit).
        """
        today = fields.Date.context_today(self)
        date_from = fields.Date.to_date(date_from) or today
        date_to = fields.Date.to_date(date_to) or date_from
        Booking, Line = self.env["salon.booking"], self.env["salon.booking.line"]
        b_dom = [("booking_date", ">=", date_from), ("booking_date", "<=", date_to)]
        l_dom = [("booking_date", ">=", date_from), ("booking_date", "<=", date_to)]
        if employee_id:
            b_dom.append(("line_ids.employee_id", "=", int(employee_id)))
            l_dom.append(("employee_id", "=", int(employee_id)))
        if product_id:
            b_dom.append(("line_ids.product_id", "=", int(product_id)))
            l_dom.append(("product_id", "=", int(product_id)))
        done_dom = l_dom + [("state", "=", "done")]

        by_state = {s: c for s, c in Booking._read_group(b_dom, ["state"], ["__count"])}
        completed = sum(by_state.get(s, 0) for s in DONE_STATES)
        cancelled, no_show = by_state.get("cancelled", 0), by_state.get("no_show", 0)
        active_total = sum(c for s, c in by_state.items() if s not in ("draft", "cancelled"))

        agg = Line._read_group(done_dom, [], ["price_subtotal:sum", "commission_amount:sum", "discount_amount:sum"])
        revenue, commission, discount = agg[0] if agg else (0.0, 0.0, 0.0)
        revenue, commission, discount = revenue or 0.0, commission or 0.0, discount or 0.0

        exp_dom = [("date", ">=", date_from), ("date", "<=", date_to), ("state", "=", "approved")]
        expenses = sum(a for _c, a in self.env["salon.expense"]._read_group(exp_dom, ["category"], ["amount:sum"]))

        # customers
        first_visit = dict(Booking._read_group([("state", "in", DONE_STATES)], ["partner_id"], ["booking_date:min"]))
        in_range = {p for p, in Booking._read_group(b_dom + [("state", "in", DONE_STATES)], ["partner_id"])}
        new = sum(1 for p in in_range if first_visit.get(p) and first_visit[p] >= date_from)
        returning = len(in_range) - new

        # staff working (attendance)
        tz = pytz.timezone(self.env.user.tz or "UTC")
        start = tz.localize(datetime.combine(date_from, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        end = tz.localize(datetime.combine(date_to + timedelta(days=1), time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        att_dom = [("check_in", ">=", start), ("check_in", "<", end), ("employee_id.salon_is_staff", "=", True)]
        if employee_id:
            att_dom.append(("employee_id", "=", int(employee_id)))
        staff_working = len(self.env["hr.attendance"].sudo()._read_group(att_dom, ["employee_id"]))

        def named(rows, label_fn=lambda k: k.display_name if k else "-"):
            return [{"label": label_fn(k), "value": v or 0.0} for k, v in rows]

        revenue_by_day = [{"label": str(d), "value": v or 0.0} for d, v in Line._read_group(
            done_dom, ["booking_date:day"], ["price_subtotal:sum"], order="booking_date:day")]
        revenue_by_staff = named(Line._read_group(done_dom, ["employee_id"], ["price_subtotal:sum"], order="price_subtotal:sum desc"))
        revenue_by_service = named(Line._read_group(done_dom, ["product_id"], ["price_subtotal:sum"], order="price_subtotal:sum desc"))
        commission_by_staff = named(Line._read_group(done_dom, ["employee_id"], ["commission_amount:sum"], order="commission_amount:sum desc"))
        labels = dict(self.env["salon.booking"]._fields["state"].selection)
        booking_status = [{"label": labels[s], "value": c} for s, c in by_state.items()]
        cat_labels = dict(self.env["salon.expense"]._fields["category"].selection)
        expenses_by_category = [{"label": cat_labels[c], "value": a or 0.0} for c, a in
                                self.env["salon.expense"]._read_group(exp_dom, ["category"], ["amount:sum"])]
        return {
            "currency": self.env.company.currency_id.symbol,
            "date_from": str(date_from), "date_to": str(date_to),
            "kpis": {
                "revenue": revenue, "discount": discount, "bookings": sum(by_state.values()),
                "completed": completed, "cancelled": cancelled, "no_show": no_show,
                "customers": len(in_range), "new_customers": new, "returning_customers": returning,
                "staff_working": staff_working, "commission": commission, "expenses": expenses,
                "est_profit": revenue - commission - expenses,
                "avg_bill": revenue / completed if completed else 0.0,
                "no_show_rate": (no_show / active_total * 100.0) if active_total else 0.0,
                "repeat_rate": (returning / len(in_range) * 100.0) if in_range else 0.0,
            },
            "charts": {
                "revenue_by_day": revenue_by_day, "revenue_by_staff": revenue_by_staff,
                "revenue_by_service": revenue_by_service, "booking_status": booking_status,
                "commission_by_staff": commission_by_staff, "expenses_by_category": expenses_by_category,
            },
        }

    @api.model
    def get_trends(self, months=6):
        """Monthly bookings / revenue / commission / expenses for the last N months (data the user may see)."""
        months = max(1, min(int(months or 6), 24))
        first = fields.Date.context_today(self).replace(day=1)
        starts, y, m = [], first.year, first.month
        for _i in range(months):
            starts.append(first.replace(year=y, month=m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        starts.reverse()
        idx = {d: i for i, d in enumerate(starts)}

        def bucket(model, date_field, domain, measure):
            out = [0.0] * months
            try:
                self.env[model].check_access("read")
            except AccessError:
                return out
            for month_start, value in self.env[model]._read_group(
                    domain + [(date_field, ">=", starts[0])], [f"{date_field}:month"], [measure]):
                d = fields.Date.to_date(month_start)
                if d in idx:
                    out[idx[d]] = value or 0
            return out

        done = [("state", "=", "done")]
        series = [
            {"key": "revenue", "name": self.env._("Revenue"), "kind": "sum",
             "values": bucket("salon.booking.line", "booking_date", done, "price_subtotal:sum")},
            {"key": "bookings", "name": self.env._("Bookings"), "kind": "count",
             "values": bucket("salon.booking", "booking_date", [("state", "in", list(DONE_STATES))], "__count")},
            {"key": "commission", "name": self.env._("Commission"), "kind": "sum",
             "values": bucket("salon.booking.line", "booking_date", done, "commission_amount:sum")},
            {"key": "expenses", "name": self.env._("Expenses"), "kind": "sum",
             "values": bucket("salon.expense", "date", [("state", "=", "approved")], "amount:sum")},
        ]
        return {"labels": [d.strftime("%b %y") for d in starts],
                "currency": self.env.company.currency_id.symbol, "series": series}

    @api.model
    def get_staff_performance(self, date_from=False, date_to=False):
        """Leaderboard rows per staff member for the period (visible data only)."""
        today = fields.Date.context_today(self)
        date_from = fields.Date.to_date(date_from) or today.replace(day=1)
        date_to = fields.Date.to_date(date_to) or today
        rows = self.env["salon.booking.line"]._read_group(
            [("booking_date", ">=", date_from), ("booking_date", "<=", date_to), ("state", "=", "done"),
             ("employee_id", "!=", False)],
            ["employee_id"], ["__count", "price_subtotal:sum", "commission_amount:sum"])
        return [{"name": emp.name, "services": cnt, "revenue": rev or 0.0, "commission": com or 0.0}
                for emp, cnt, rev, com in rows]

    @api.model
    def get_history(self, model, res_id):
        """Field-change history (chatter tracking) of a salon record for the external frontend.
        Plain users cannot read mail.tracking.value, so it is read with sudo after a normal read-access check."""
        if model not in ("salon.booking", "salon.staff.payout", "salon.expense"):
            raise AccessError(self.env._("History is not available for this model."))
        record = self.env[model].browse(res_id)
        record.check_access("read")
        values = self.env["mail.tracking.value"].sudo().search(
            [("mail_message_id.model", "=", model), ("mail_message_id.res_id", "=", res_id)], order="id desc", limit=100)
        return [{
            "date": fields.Datetime.to_string(v.mail_message_id.date),
            "user": v.mail_message_id.author_id.name or "",
            "field": v.field_id.field_description,
            "old": v.old_value_char or "", "new": v.new_value_char or "",
        } for v in values]

    @api.model
    def get_app_context(self):
        """Session info for the external (Next.js) frontend: who is logged in and what they may do."""
        user = self.env.user
        roles = {r: user.has_group("salon_management.group_salon_" + r)
                 for r in ("staff", "receptionist", "manager", "accountant", "admin")}
        company = self.env.company
        limit = (None if roles["admin"] else company.salon_manager_max_discount if roles["manager"]
                 else company.salon_staff_max_discount)
        return {"uid": user.id, "name": user.name, "company": company.name,
                "currency": company.currency_id.symbol, "roles": roles, "discount_limit": limit,
                "employee_id": user.employee_id.id or False}
