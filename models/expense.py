from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

CATEGORIES = [
    ("rent", "Rent"), ("electricity", "Electricity"), ("water", "Water"), ("salary", "Salary"),
    ("payout", "Staff Payout"), ("purchase", "Product Purchase"), ("equipment", "Equipment"),
    ("maintenance", "Maintenance"), ("marketing", "Marketing"), ("other", "Other"),
]


class SalonExpense(models.Model):
    _name = "salon.expense"
    _description = "Salon Expense"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(string="Description", required=True)
    date = fields.Date(required=True, default=fields.Date.context_today, index=True, tracking=True)
    category = fields.Selection(CATEGORIES, required=True, default="other", tracking=True, index=True)
    amount = fields.Monetary(required=True, tracking=True, currency_field="currency_id")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one("res.partner", string="Vendor")
    bill_id = fields.Many2one("account.move", string="Vendor Bill", domain=[("move_type", "=", "in_invoice")],
                              help="Optional link to the accounting vendor bill.")
    payout_id = fields.Many2one("salon.staff.payout", readonly=True, copy=False, index="btree_not_null")
    state = fields.Selection([("draft", "Draft"), ("approved", "Approved"), ("cancelled", "Cancelled")],
                             default="draft", tracking=True, required=True, index=True, copy=False)
    approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)

    @api.constrains("amount")
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(self.env._("Expense amount must be positive."))

    def write(self, vals):
        if {"amount", "date", "category", "company_id"} & set(vals) and not self.env.context.get("salon_system"):
            if any(r.state != "draft" for r in self):
                raise UserError(self.env._("Only draft expenses can be edited. Cancel and re-enter instead."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_draft_only(self):
        if any(r.state != "draft" for r in self):
            raise UserError(self.env._("Only draft expenses can be deleted; cancel approved ones instead."))

    def _require_manager(self):
        if not self.env.su and not self.env.user.has_group("salon_management.group_salon_manager"):
            raise AccessError(self.env._("Only a Salon Manager can approve or cancel expenses."))

    def action_approve(self):
        self._require_manager()
        if any(r.state != "draft" for r in self):
            raise UserError(self.env._("Only draft expenses can be approved."))
        self.write({"state": "approved", "approved_by": self.env.uid, "approved_date": fields.Datetime.now()})

    def action_cancel(self):
        self._require_manager()
        self.with_context(salon_system=True).write({"state": "cancelled"})
