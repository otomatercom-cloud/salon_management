from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

BASE_FIELDS = {"employee_id", "date_from", "date_to", "advance", "other_deduction", "base_pay", "company_id"}
G_MANAGER = "salon_management.group_salon_manager"
G_ADMIN = "salon_management.group_salon_admin"


def _month_start(self):
    return fields.Date.context_today(self).replace(day=1)


class SalonStaffPayout(models.Model):
    _name = "salon.staff.payout"
    _description = "Salon Staff Payout"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_to desc, id desc"

    name = fields.Char(default="New", copy=False, readonly=True)
    employee_id = fields.Many2one("hr.employee", required=True, tracking=True, index=True,
                                  domain=[("salon_is_staff", "=", True)])
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True, default=_month_start, tracking=True)
    date_to = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection(
        [("draft", "Draft"), ("calculated", "Calculated"), ("approved", "Approved"),
         ("paid", "Paid"), ("closed", "Closed")], default="draft", tracking=True, copy=False, index=True)
    line_ids = fields.One2many("salon.booking.line", "payout_id", string="Commission Lines", readonly=True)
    commission_total = fields.Monetary(string="Gross Commission", compute="_compute_totals", store=True,
                                       currency_field="currency_id")
    base_pay = fields.Monetary(string="Base Pay (fixed / daily)", currency_field="currency_id", tracking=True)
    attendance_days = fields.Integer(readonly=True)
    advance = fields.Monetary(currency_field="currency_id", tracking=True)
    other_deduction = fields.Monetary(currency_field="currency_id", tracking=True)
    net_payable = fields.Monetary(compute="_compute_totals", store=True, currency_field="currency_id")
    notes = fields.Text()
    approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    paid_by = fields.Many2one("res.users", readonly=True, copy=False)
    paid_date = fields.Datetime(readonly=True, copy=False)
    expense_id = fields.Many2one("salon.expense", readonly=True, copy=False)

    @api.depends("line_ids.commission_amount", "base_pay", "advance", "other_deduction")
    def _compute_totals(self):
        for rec in self:
            rec.commission_total = sum(rec.line_ids.mapped("commission_amount"))
            rec.net_payable = rec.base_pay + rec.commission_total - rec.advance - rec.other_deduction

    @api.constrains("date_from", "date_to", "advance", "other_deduction", "base_pay")
    def _check_values(self):
        for rec in self:
            if rec.date_from > rec.date_to:
                raise ValidationError(self.env._("The period start must be before its end."))
            if rec.advance < 0 or rec.other_deduction < 0 or rec.base_pay < 0:
                raise ValidationError(self.env._("Advance, deductions and base pay cannot be negative."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("salon.staff.payout") or "New"
        return super().create(vals_list)

    def write(self, vals):
        if BASE_FIELDS & set(vals) and not self.env.context.get("salon_system"):
            for rec in self:
                if rec.state in ("paid", "closed"):
                    raise UserError(self.env._(
                        "Payout %s is already paid. Ask an administrator to reverse it before correcting.", rec.name))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_draft_only(self):
        if any(r.state != "draft" for r in self):
            raise UserError(self.env._("Only draft payouts can be deleted."))

    def _require(self, xmlid):
        if not self.env.su and not self.env.user.has_group(xmlid):
            raise AccessError(self.env._("You are not allowed to perform this action."))

    def _set(self, vals):
        self.with_context(salon_system=True).write(vals)

    # ------------------------------------------------------------------ workflow
    def action_calculate(self):
        self._require(G_MANAGER)
        Line = self.env["salon.booking.line"]
        for rec in self:
            if rec.state != "draft":
                raise UserError(self.env._("Only draft payouts can be calculated."))
            lines = Line.search([
                ("employee_id", "=", rec.employee_id.id), ("state", "=", "done"),
                ("payout_id", "=", False), ("booking_state", "in", ("completed", "paid", "closed")),
                ("booking_date", ">=", rec.date_from), ("booking_date", "<=", rec.date_to),
                ("company_id", "=", rec.company_id.id),
            ])
            lines.with_context(salon_system=True).write({"payout_id": rec.id})
            emp = rec.employee_id
            days = 0
            base = 0.0
            if emp.salon_salary_type in ("fixed", "fixed_commission"):
                base = emp.salon_fixed_salary
            elif emp.salon_salary_type in ("daily", "daily_commission"):
                att = self.env["hr.attendance"].sudo()._read_group([
                    ("employee_id", "=", emp.id), ("check_in", ">=", rec.date_from),
                    ("check_in", "<", fields.Date.add(rec.date_to, days=1)),
                ], ["check_in:day"], ["__count"])
                days = len(att)
                base = emp.salon_daily_rate * days
            rec._set({"state": "calculated", "base_pay": base, "attendance_days": days})
            rec.activity_schedule("mail.mail_activity_data_todo", summary=self.env._("Approve staff payout"),
                                  user_id=self.env.uid)

    def action_reset_draft(self):
        self._require(G_MANAGER)
        for rec in self:
            if rec.state not in ("calculated", "approved"):
                raise UserError(self.env._("Only calculated or approved payouts can be reset."))
            rec.line_ids.with_context(salon_system=True).write({"payout_id": False})
            rec._set({"state": "draft", "approved_by": False, "approved_date": False})

    def action_approve(self):
        self._require(G_MANAGER)
        for rec in self:
            if rec.state != "calculated":
                raise UserError(self.env._("Only calculated payouts can be approved."))
            if rec.net_payable < 0:
                raise UserError(self.env._("Net payable is negative; reduce the advance/deductions first."))
            rec._set({"state": "approved", "approved_by": self.env.uid, "approved_date": fields.Datetime.now()})

    def action_pay(self):
        self._require(G_MANAGER)
        for rec in self:
            if rec.state != "approved":
                raise UserError(self.env._("Only approved payouts can be paid (a payout cannot be paid twice)."))
            vals = {"state": "paid", "paid_by": self.env.uid, "paid_date": fields.Datetime.now()}
            if rec.base_pay > 0:
                vals["expense_id"] = self.env["salon.expense"].with_context(salon_system=True).create({
                    "name": self.env._("Base pay %(payout)s - %(staff)s", payout=rec.name, staff=rec.employee_id.name),
                    "category": "salary", "amount": rec.base_pay, "date": fields.Date.context_today(rec),
                    "company_id": rec.company_id.id, "payout_id": rec.id, "state": "approved",
                    "approved_by": self.env.uid, "approved_date": fields.Datetime.now(),
                }).id
            rec._set(vals)

    def action_close(self):
        self._require(G_MANAGER)
        for rec in self:
            if rec.state != "paid":
                raise UserError(self.env._("Only paid payouts can be closed."))
        self._set({"state": "closed"})

    def action_reverse(self):
        """Controlled correction: administrator reopens a paid payout (never silently edited)."""
        self._require(G_ADMIN)
        for rec in self:
            if rec.state != "paid":
                raise UserError(self.env._("Only a paid (not closed) payout can be reversed."))
            if rec.expense_id:
                rec.expense_id.with_context(salon_system=True).write({"state": "cancelled"})
            rec._set({"state": "approved", "paid_by": False, "paid_date": False, "expense_id": False})
            rec.message_post(body=self.env._("Payment reversed by %s for correction.", self.env.user.name))
