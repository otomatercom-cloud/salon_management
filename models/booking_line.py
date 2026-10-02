from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

LOCK_ALL = ("completed", "paid", "closed", "cancelled", "no_show")
G_RECEPTION = "salon_management.group_salon_receptionist"
G_MANAGER = "salon_management.group_salon_manager"
G_ADMIN = "salon_management.group_salon_admin"


class SalonBookingLine(models.Model):
    _name = "salon.booking.line"
    _description = "Salon Booking Service Line"
    _order = "booking_id, sequence, id"

    booking_id = fields.Many2one("salon.booking", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related="booking_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one(related="booking_id.partner_id", store=True, index=True)
    booking_date = fields.Date(related="booking_id.booking_date", store=True, index=True)
    booking_state = fields.Selection(related="booking_id.state", store=True, index=True, string="Booking State")
    product_id = fields.Many2one("product.product", string="Service", required=True,
                                 domain=[("is_salon_service", "=", True)])
    name = fields.Char(string="Description")
    employee_id = fields.Many2one("hr.employee", string="Staff", index=True,
                                  domain=[("salon_is_staff", "=", True)])
    quantity = fields.Float(default=1.0)
    price_unit = fields.Float(string="Unit Price", digits="Product Price")
    discount_type = fields.Selection([("percent", "%"), ("fixed", "Fixed")], default="percent", required=True)
    discount_value = fields.Float(string="Discount")
    discount_percent = fields.Float(compute="_compute_amounts", store=True, digits=(16, 4))
    discount_amount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    price_subtotal = fields.Monetary(string="Net Amount", compute="_compute_amounts", store=True,
                                     currency_field="currency_id")
    duration = fields.Float(string="Duration (h)")
    line_start = fields.Datetime(compute="_compute_times", store=True, index=True)
    line_end = fields.Datetime(compute="_compute_times", store=True, index=True)
    state = fields.Selection([("pending", "Pending"), ("done", "Done"), ("cancelled", "Cancelled")],
                             default="pending", required=True, index=True)
    commission_amount = fields.Monetary(
        compute="_compute_commission", store=True, currency_field="currency_id",
        help="Commission earned by the staff member on this line (an estimate until the line is Done).")
    payout_id = fields.Many2one("salon.staff.payout", copy=False, readonly=True, index="btree_not_null")

    # ------------------------------------------------------------------ computes
    @api.depends("quantity", "price_unit", "discount_type", "discount_value")
    def _compute_amounts(self):
        for line in self:
            gross = line.quantity * line.price_unit
            if line.discount_type == "fixed":
                disc = min(line.discount_value, gross)
            else:
                disc = gross * min(line.discount_value, 100.0) / 100.0
            if line.currency_id:
                disc = line.currency_id.round(disc)
            line.discount_amount = disc
            line.discount_percent = (disc / gross * 100.0) if gross else 0.0
            line.price_subtotal = gross - disc

    @api.depends("booking_id.start_datetime", "booking_id.line_ids.sequence",
                 "booking_id.line_ids.duration", "booking_id.line_ids.employee_id")
    def _compute_times(self):
        from datetime import timedelta
        for line in self:
            booking = line.booking_id
            if not booking.start_datetime:
                line.line_start = line.line_end = False
                continue
            offset = 0.0
            ordered = booking.line_ids.sorted(key=lambda l: (l.sequence, l._origin.id or 0))
            for other in ordered:
                if other.employee_id != line.employee_id:
                    continue
                if other == line:
                    break
                offset += other.duration
            line.line_start = booking.start_datetime + timedelta(hours=offset)
            line.line_end = line.line_start + timedelta(hours=line.duration)

    @api.depends("state", "price_subtotal", "quantity", "employee_id", "product_id")
    def _compute_commission(self):
        rules = self.env["salon.commission.rule"].sudo().search([])
        for line in self:
            if line.state == "cancelled" or not line.employee_id:
                line.commission_amount = 0.0
                continue
            line.commission_amount = self.env["salon.commission.rule"]._compute_commission(
                line.employee_id.sudo(), line.product_id, line.quantity, line.price_subtotal, rules=rules)

    # ------------------------------------------------------------------ constraints
    @api.constrains("quantity", "price_unit", "discount_value")
    def _check_values(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(self.env._("Quantity must be positive."))
            if line.price_unit < 0:
                raise ValidationError(self.env._("Service price cannot be negative."))
            if line.discount_value < 0:
                raise ValidationError(self.env._("Discount cannot be negative."))
            if line.discount_type == "percent" and line.discount_value > 100:
                raise ValidationError(self.env._("A percentage discount cannot exceed 100."))
            if line.discount_type == "fixed" and line.discount_value > line.quantity * line.price_unit:
                raise ValidationError(self.env._("A fixed discount cannot exceed the line amount."))

    @api.constrains("commission_amount")
    def _check_commission(self):
        for line in self:
            if line.commission_amount < 0:
                raise ValidationError(self.env._("Commission cannot be negative."))

    @api.constrains("discount_value", "discount_type", "price_unit", "quantity")
    def _check_discount_limit(self):
        # constraints run sudo()'d (uid unchanged), so test the real user, not env.su
        if self.env.uid == SUPERUSER_ID:
            return
        user = self.env.user
        if user.has_group(G_ADMIN):
            return
        for line in self:
            company = line.company_id
            limit = (company.salon_manager_max_discount if user.has_group(G_MANAGER)
                     else company.salon_staff_max_discount)
            if line.discount_percent > limit + 1e-6:
                raise ValidationError(self.env._(
                    "Discount of %(pct).2f%% exceeds your limit of %(limit).2f%%.",
                    pct=line.discount_percent, limit=limit))

    @api.constrains("price_unit", "product_id")
    def _check_price_manipulation(self):
        if self.env.uid == SUPERUSER_ID or self.env.user.has_group(G_MANAGER):
            return
        for line in self:
            if abs(line.price_unit - line.product_id.lst_price) > 0.005:
                raise ValidationError(self.env._(
                    "Only a Salon Manager can change the price of %s.", line.product_id.display_name))

    @api.constrains("employee_id", "product_id")
    def _check_skill(self):
        for line in self:
            skills = line.employee_id.salon_service_ids
            if skills and line.product_id.product_tmpl_id not in skills:
                raise ValidationError(self.env._(
                    "%(staff)s is not qualified to perform %(service)s.",
                    staff=line.employee_id.name, service=line.product_id.display_name))

    @api.constrains("employee_id", "line_start", "line_end", "state")
    def _check_overlap(self):
        for line in self:
            if (not line.employee_id or line.state == "cancelled" or line.duration <= 0
                    or line.booking_id.state in ("cancelled", "no_show")
                    or not line.line_start):
                continue
            clash = self.search([
                ("employee_id", "=", line.employee_id.id),
                ("booking_id", "!=", line.booking_id.id),
                ("booking_id.state", "not in", ("cancelled", "no_show")),
                ("state", "!=", "cancelled"),
                ("line_start", "<", line.line_end),
                ("line_end", ">", line.line_start),
            ], limit=1)
            if clash:
                raise ValidationError(self.env._(
                    "%(staff)s is already booked in %(booking)s during this time.",
                    staff=line.employee_id.name, booking=clash.booking_id.name))

    # ------------------------------------------------------------------ ORM
    @api.model_create_multi
    def create(self, vals_list):
        Product = self.env["product.product"]
        for vals in vals_list:
            product = Product.browse(vals.get("product_id"))
            if product:
                vals.setdefault("price_unit", product.lst_price)
                vals.setdefault("duration", product.salon_duration)
                vals.setdefault("name", product.display_name)
            if not self.env.context.get("salon_system"):
                vals.pop("state", None)
                vals.pop("payout_id", None)
        bookings = self.env["salon.booking"].browse([v["booking_id"] for v in vals_list if v.get("booking_id")])
        if not self.env.context.get("salon_system"):
            for booking in bookings:
                if booking.state not in ("draft", "confirmed", "checked_in", "in_service"):
                    raise UserError(self.env._("Services cannot be added to booking %s in its current state.",
                                               booking.name))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("salon_system"):
            for line in self:
                st = line.booking_id.state
                if st in LOCK_ALL:
                    raise UserError(self.env._("Booking %s can no longer be edited.", line.booking_id.name))
                if st == "in_service":
                    raise UserError(self.env._("Services cannot be edited once the service has started."))
            if "state" in vals or "payout_id" in vals:
                raise UserError(self.env._("Use the action buttons to change the service status."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_check(self):
        if self.env.context.get("salon_system"):
            return
        for line in self:
            if line.booking_id.state not in ("draft", "confirmed", "checked_in"):
                raise UserError(self.env._("Services cannot be removed from booking %s in its current state.",
                                           line.booking_id.name))

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.price_unit = line.product_id.lst_price
                line.duration = line.product_id.salon_duration
                line.name = line.product_id.display_name

    # ------------------------------------------------------------------ actions
    def action_done(self):
        for line in self:
            if line.booking_id.state != "in_service":
                raise UserError(self.env._("Services can only be completed while the booking is In Service."))
            if (not self.env.user.has_group(G_RECEPTION) and not self.env.su
                    and line.employee_id.user_id != self.env.user):
                raise AccessError(self.env._("You can only complete services assigned to you."))
            if line.state != "pending":
                raise UserError(self.env._("This service is not pending."))
        self.with_context(salon_system=True).write({"state": "done"})
