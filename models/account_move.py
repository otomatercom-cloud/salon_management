from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    salon_booking_id = fields.Many2one(
        "salon.booking", string="Salon Booking", index="btree_not_null", copy=False, readonly=True)
