from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    salon_staff_max_discount = fields.Float(
        string="Staff/Receptionist Max Discount (%)", default=10.0,
        help="Highest discount percentage a Salon Staff or Receptionist may give on a service line.")
    salon_manager_max_discount = fields.Float(
        string="Manager Max Discount (%)", default=30.0,
        help="Highest discount percentage a Salon Manager may give. Salon Administrators are unrestricted.")
