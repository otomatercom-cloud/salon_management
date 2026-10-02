from odoo import api, fields, models

DONE_STATES = ("completed", "paid", "closed")


class ResPartner(models.Model):
    _inherit = "res.partner"

    salon_gender = fields.Selection(
        [("female", "Female"), ("male", "Male"), ("other", "Other")], string="Gender")
    salon_birthdate = fields.Date(string="Date of Birth")
    salon_notes = fields.Text(string="Salon Notes")
    salon_preferred_employee_id = fields.Many2one(
        "hr.employee", string="Preferred Staff", domain=[("salon_is_staff", "=", True)])
    salon_preferred_service_ids = fields.Many2many(
        "product.template", "salon_partner_service_rel", "partner_id", "product_tmpl_id",
        string="Preferred Services", domain=[("is_salon_service", "=", True)])
    salon_booking_ids = fields.One2many("salon.booking", "partner_id", string="Salon Bookings")
    salon_visit_count = fields.Integer(
        string="Visits", compute="_compute_salon_stats",
        help="Bookings that reached Completed, Paid or Closed.")
    salon_cancel_count = fields.Integer(string="Cancellations", compute="_compute_salon_stats")
    salon_noshow_count = fields.Integer(string="No-shows", compute="_compute_salon_stats")
    salon_last_visit = fields.Date(string="Last Visit", compute="_compute_salon_stats")
    salon_total_spent = fields.Float(
        string="Total Spending", compute="_compute_salon_stats",
        help="Net amount of completed service lines (before tax).")
    salon_customer_status = fields.Selection(
        [("prospect", "Prospect"), ("new", "New"), ("returning", "Returning"), ("loyal", "Loyal")],
        string="Customer Status", compute="_compute_salon_stats",
        help="Prospect: no visit. New: 1 visit. Returning: 2-9 visits. Loyal: 10+ visits.")

    @api.depends("salon_booking_ids.state")
    def _compute_salon_stats(self):
        ids = [r.id for r in self if isinstance(r.id, int)]
        visits, cancels, noshows, last, spent = {}, {}, {}, {}, {}
        if ids:
            Booking = self.env["salon.booking"]
            for partner, state, count, maxdate in Booking._read_group(
                    [("partner_id", "in", ids)], ["partner_id", "state"],
                    ["__count", "booking_date:max"]):
                pid = partner.id
                if state in DONE_STATES:
                    visits[pid] = visits.get(pid, 0) + count
                    if maxdate and (pid not in last or maxdate > last[pid]):
                        last[pid] = maxdate
                elif state == "cancelled":
                    cancels[pid] = count
                elif state == "no_show":
                    noshows[pid] = count
            for partner, total in self.env["salon.booking.line"]._read_group(
                    [("partner_id", "in", ids), ("state", "=", "done")],
                    ["partner_id"], ["price_subtotal:sum"]):
                spent[partner.id] = total
        for rec in self:
            pid = rec.id if isinstance(rec.id, int) else 0
            v = visits.get(pid, 0)
            rec.salon_visit_count = v
            rec.salon_cancel_count = cancels.get(pid, 0)
            rec.salon_noshow_count = noshows.get(pid, 0)
            rec.salon_last_visit = last.get(pid, False)
            rec.salon_total_spent = spent.get(pid, 0.0)
            rec.salon_customer_status = (
                "prospect" if not v else "new" if v == 1 else "returning" if v < 10 else "loyal")
