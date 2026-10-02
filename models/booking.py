from collections import defaultdict
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

TRANSITIONS = {
    "draft": ("confirmed", "cancelled"),
    "confirmed": ("checked_in", "cancelled", "no_show"),
    "checked_in": ("in_service",),
    "in_service": ("completed",),
    "completed": ("paid",),
    "paid": ("closed",),
}
LOCKED_STATES = ("completed", "paid", "closed", "cancelled", "no_show")
BUSINESS_FIELDS = {"partner_id", "start_datetime", "line_ids", "company_id"}
STATES = [
    ("draft", "Draft"), ("confirmed", "Confirmed"), ("checked_in", "Checked-In"),
    ("in_service", "In Service"), ("completed", "Completed"), ("paid", "Paid"),
    ("closed", "Closed"), ("cancelled", "Cancelled"), ("no_show", "No Show"),
]
G_STAFF = "salon_management.group_salon_staff"
G_RECEPTION = "salon_management.group_salon_receptionist"
G_MANAGER = "salon_management.group_salon_manager"


class SalonBooking(models.Model):
    _name = "salon.booking"
    _description = "Salon Booking"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "start_datetime desc, id desc"

    name = fields.Char(string="Booking Number", default="New", copy=False, readonly=True, index=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, index=True, tracking=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    start_datetime = fields.Datetime(string="Start", required=True, tracking=True, index=True,
                                     default=fields.Datetime.now)
    end_datetime = fields.Datetime(string="End", compute="_compute_end", store=True)
    booking_date = fields.Date(string="Booking Date", compute="_compute_booking_date", store=True, index=True)
    duration = fields.Float(
        string="Duration (h)", compute="_compute_duration", store=True,
        help="Longest sequence of services for a single staff member (staff work in parallel).")
    line_ids = fields.One2many("salon.booking.line", "booking_id", string="Services", copy=True)
    employee_ids = fields.Many2many("hr.employee", string="Staff", compute="_compute_employee_ids")
    source = fields.Selection(
        [("walkin", "Walk-in"), ("phone", "Phone"), ("whatsapp", "WhatsApp"),
         ("online", "Online"), ("referral", "Referral")], default="walkin")
    note = fields.Html(string="Notes")
    state = fields.Selection(STATES, default="draft", required=True, tracking=True, copy=False, index=True)
    # audit
    checkin_datetime = fields.Datetime(string="Checked-in At", copy=False, readonly=True)
    completed_datetime = fields.Datetime(copy=False, readonly=True)
    completed_user_id = fields.Many2one("res.users", copy=False, readonly=True)
    paid_datetime = fields.Datetime(copy=False, readonly=True)
    paid_user_id = fields.Many2one("res.users", copy=False, readonly=True)
    cancel_date = fields.Datetime(string="Cancelled / No-show At", copy=False, readonly=True)
    cancel_user_id = fields.Many2one("res.users", string="Cancelled / No-show By", copy=False, readonly=True)
    cancel_reason = fields.Char(string="Cancellation / No-show Reason", copy=False, readonly=True)
    reminder_sent = fields.Boolean(copy=False)
    # money
    amount_gross = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    amount_discount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    amount_untaxed = fields.Monetary(string="Total (before tax)", compute="_compute_amounts", store=True,
                                     currency_field="currency_id")
    invoice_id = fields.Many2one("account.move", string="Invoice", copy=False, readonly=True, index="btree_not_null")
    amount_due = fields.Monetary(related="invoice_id.amount_residual", currency_field="currency_id")
    payment_status = fields.Selection(
        [("unpaid", "Unpaid"), ("partial", "Partial"), ("paid", "Paid")], string="Payment Status",
        compute="_compute_payment_status", store=True, index=True)

    # ------------------------------------------------------------------ computes
    @api.depends("start_datetime")
    def _compute_booking_date(self):
        for rec in self:
            rec.booking_date = (fields.Datetime.context_timestamp(rec, rec.start_datetime).date()
                                if rec.start_datetime else False)

    @api.depends("line_ids.duration", "line_ids.employee_id")
    def _compute_duration(self):
        for rec in self:
            per_staff = defaultdict(float)
            for line in rec.line_ids:
                per_staff[line.employee_id.id] += line.duration
            rec.duration = max(per_staff.values()) if per_staff else 0.0

    @api.depends("start_datetime", "duration")
    def _compute_end(self):
        for rec in self:
            rec.end_datetime = (rec.start_datetime + timedelta(hours=rec.duration)
                                if rec.start_datetime else False)

    @api.depends("line_ids.employee_id")
    def _compute_employee_ids(self):
        for rec in self:
            rec.employee_ids = rec.line_ids.employee_id

    @api.depends("line_ids.price_subtotal", "line_ids.discount_amount", "line_ids.state")
    def _compute_amounts(self):
        for rec in self:
            lines = rec.line_ids.filtered(lambda l: l.state != "cancelled")
            rec.amount_discount = sum(lines.mapped("discount_amount"))
            rec.amount_untaxed = sum(lines.mapped("price_subtotal"))
            rec.amount_gross = rec.amount_untaxed + rec.amount_discount

    @api.depends("invoice_id.payment_state", "invoice_id.state")
    def _compute_payment_status(self):
        for rec in self:
            inv = rec.invoice_id
            if not inv or inv.state != "posted":
                rec.payment_status = "unpaid"
            elif inv.payment_state in ("paid", "in_payment"):
                rec.payment_status = "paid"
            elif inv.payment_state == "partial":
                rec.payment_status = "partial"
            else:
                rec.payment_status = "unpaid"

    @api.constrains("state", "line_ids")
    def _check_has_lines(self):
        for rec in self:
            if rec.state not in ("draft", "cancelled", "no_show") and not rec.line_ids:
                raise UserError(self.env._("A booking must have at least one service."))

    # ------------------------------------------------------------------ ORM
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("salon.booking") or "New"
            if not self.env.context.get("salon_transition"):
                vals["state"] = "draft"
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals:
            if not self.env.context.get("salon_transition"):
                raise UserError(self.env._("Use the workflow buttons to change the booking state."))
            for rec in self:
                if vals["state"] != rec.state:
                    rec._check_transition(vals["state"])
        if BUSINESS_FIELDS & set(vals):
            for rec in self:
                if rec.state in LOCKED_STATES:
                    raise UserError(self.env._(
                        "Booking %(name)s is %(state)s and can no longer be edited.",
                        name=rec.name, state=dict(STATES)[rec.state]))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(self.env._(
                    "Only draft bookings can be deleted. Cancel booking %s instead.", rec.name))

    # ------------------------------------------------------------------ workflow helpers
    def _check_transition(self, new_state):
        self.ensure_one()
        if new_state not in TRANSITIONS.get(self.state, ()):
            labels = dict(STATES)
            raise UserError(self.env._(
                "Invalid transition for %(name)s: %(old)s → %(new)s is not allowed.",
                name=self.name, old=labels[self.state], new=labels[new_state]))

    def _require(self, xmlid):
        if not self.env.su and not self.env.user.has_group(xmlid):
            raise AccessError(self.env._("You are not allowed to perform this action."))

    def _transition(self, new_state, **extra):
        for rec in self:
            rec._check_transition(new_state)
        self.with_context(salon_transition=True).write({"state": new_state, **extra})

    def _managers(self):
        group = self.env.ref(G_MANAGER)
        return group.all_user_ids.filtered(lambda u: u.active and not u.share)

    def _notify_managers(self, summary):
        for rec in self:
            for user in rec._managers()[:5]:
                rec.activity_schedule("mail.mail_activity_data_todo", user_id=user.id, summary=summary)

    # ------------------------------------------------------------------ actions
    def action_confirm(self):
        self._require(G_STAFF)
        for rec in self:
            rec._check_transition("confirmed")
            if not rec.line_ids:
                raise UserError(self.env._("Add at least one service before confirming."))
            if rec.line_ids.filtered(lambda l: not l.employee_id):
                raise UserError(self.env._("Assign a staff member to every service before confirming."))
        self.line_ids._check_overlap()
        self._transition("confirmed")

    def action_check_in(self):
        self._require(G_STAFF)
        self._transition("checked_in", checkin_datetime=fields.Datetime.now())

    def action_start_service(self):
        self._require(G_STAFF)
        self._transition("in_service")

    def action_complete(self):
        self._require(G_RECEPTION)
        for rec in self:
            rec._check_transition("completed")
        pending = self.line_ids.filtered(lambda l: l.state == "pending")
        pending.with_context(salon_system=True).write({"state": "done"})
        self._transition("completed", completed_datetime=fields.Datetime.now(),
                         completed_user_id=self.env.user.id)
        for rec in self:
            rec.activity_schedule("mail.mail_activity_data_todo", user_id=self.env.uid,
                                  summary=self.env._("Create invoice and collect payment"))

    def action_create_invoice(self):
        self._require(G_RECEPTION)
        for rec in self:
            if rec.state != "completed":
                raise UserError(self.env._("Only completed bookings can be invoiced."))
            if rec.invoice_id and rec.invoice_id.state != "cancel":
                raise UserError(self.env._("Booking %s already has an invoice.", rec.name))
            lines = []
            for line in rec.line_ids.filtered(lambda l: l.state == "done"):
                vals = {"product_id": line.product_id.id, "name": line.name, "quantity": line.quantity}
                if line.discount_type == "fixed":
                    net = line.price_subtotal / line.quantity
                    vals.update(price_unit=net, discount=0.0)
                    if line.discount_amount:
                        vals["name"] = "%s (discount %s)" % (line.name, line.discount_amount)
                else:
                    vals.update(price_unit=line.price_unit, discount=line.discount_percent)
                lines.append(Command.create(vals))
            move = self.env["account.move"].with_company(rec.company_id).create({
                "move_type": "out_invoice",
                "partner_id": rec.partner_id.id,
                "invoice_date": fields.Date.context_today(rec),
                "invoice_origin": rec.name,
                "company_id": rec.company_id.id,
                "salon_booking_id": rec.id,
                "invoice_line_ids": lines,
            })
            move.action_post()
            rec.invoice_id = move

    def action_register_payment(self):
        self.ensure_one()
        self._require(G_RECEPTION)
        if not self.invoice_id or self.invoice_id.state != "posted":
            raise UserError(self.env._("Create the invoice first."))
        return self.invoice_id.action_register_payment()

    @api.model
    def get_booking_meta(self):
        """Lookup data for booking/payment forms in the external frontend."""
        staff = self.env["hr.employee"].search_read([("salon_is_staff", "=", True)], ["name"])
        services = self.env["product.product"].search_read(
            [("is_salon_service", "=", True)], ["display_name", "lst_price", "salon_duration"])
        journals = self.env["account.journal"].search_read(
            [("type", "in", ("bank", "cash")), ("company_id", "=", self.env.company.id)], ["name", "type"])
        return {"staff": staff, "services": services, "journals": journals}

    def register_payment(self, journal_id, amount):
        """Register one (partial) payment on the booking invoice; call repeatedly for split payments."""
        self.ensure_one()
        self._require(G_RECEPTION)
        if not self.invoice_id or self.invoice_id.state != "posted":
            raise UserError(self.env._("Create the invoice first."))
        if amount <= 0 or amount > self.invoice_id.amount_residual + 0.005:
            raise UserError(self.env._("Payment must be between 0 and the amount still due."))
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=self.invoice_id.ids).create(
            {"amount": amount, "journal_id": journal_id}).action_create_payments()
        return self.payment_status

    def action_mark_paid(self):
        self._require(G_RECEPTION)
        for rec in self:
            if rec.payment_status != "paid":
                raise UserError(self.env._(
                    "Booking %s is not fully paid yet. Register the payment(s) first.", rec.name))
        self._transition("paid", paid_datetime=fields.Datetime.now(), paid_user_id=self.env.user.id)

    def action_close(self):
        self._require(G_RECEPTION)
        self._transition("closed")

    def action_cancel_wizard(self):
        return self._reason_wizard("cancel")

    def action_no_show_wizard(self):
        return self._reason_wizard("no_show")

    def _reason_wizard(self, mode):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "salon.booking.reason.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_booking_id": self.id, "default_mode": mode},
        }

    def _do_cancel(self, reason):
        for rec in self:
            rec._check_transition("cancelled")
            if rec.state == "confirmed":
                rec._require(G_MANAGER)
        self.line_ids.with_context(salon_system=True).write({"state": "cancelled"})
        self._transition("cancelled", cancel_date=fields.Datetime.now(),
                         cancel_user_id=self.env.user.id, cancel_reason=reason)
        self._notify_managers(self.env._("Booking cancelled"))

    def _do_no_show(self, reason):
        self._require(G_RECEPTION)
        for rec in self:
            rec._check_transition("no_show")
        self.line_ids.with_context(salon_system=True).write({"state": "cancelled"})
        self._transition("no_show", cancel_date=fields.Datetime.now(),
                         cancel_user_id=self.env.user.id, cancel_reason=reason)
        self._notify_managers(self.env._("Customer no-show"))

    def _request_cancellation(self, reason):
        for rec in self:
            rec.message_post(body=self.env._("Cancellation requested by %(user)s: %(reason)s",
                                             user=self.env.user.name, reason=reason))
        self._notify_managers(self.env._("Approve cancellation request"))

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_upcoming_bookings(self):
        now = fields.Datetime.now()
        bookings = self.search([
            ("state", "=", "confirmed"), ("reminder_sent", "=", False),
            ("start_datetime", ">=", now), ("start_datetime", "<=", now + timedelta(hours=2)),
        ])
        for rec in bookings:
            for user in rec.line_ids.employee_id.user_id:
                rec.activity_schedule("mail.mail_activity_data_todo", user_id=user.id,
                                      summary=self.env._("Upcoming booking %s", rec.name))
        bookings.reminder_sent = True
