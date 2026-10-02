from odoo import api, fields, models
from odoo.exceptions import ValidationError

COMMISSION_TYPES = ("commission", "fixed_commission", "daily_commission")


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    salon_is_staff = fields.Boolean(string="Salon Staff")
    salon_service_ids = fields.Many2many(
        "product.template", "salon_employee_service_rel", "employee_id", "product_tmpl_id",
        string="Skills / Services", domain=[("is_salon_service", "=", True)],
        help="Services this person can perform. Empty means any service.")
    salon_salary_type = fields.Selection(
        [("fixed", "Fixed Salary"), ("daily", "Daily Salary"), ("commission", "Commission"),
         ("fixed_commission", "Fixed + Commission"), ("daily_commission", "Daily + Commission")],
        string="Salon Pay Type", default="commission")
    salon_fixed_salary = fields.Float(string="Fixed Salary (per payout period)")
    salon_daily_rate = fields.Float(string="Daily Rate (per attendance day)")
    salon_commission_type = fields.Selection(
        [("percent", "Percentage"), ("fixed", "Fixed Amount per Service")],
        string="Default Commission Type", default="percent")
    salon_commission_value = fields.Float(string="Default Commission Value")

    @api.constrains("salon_fixed_salary", "salon_daily_rate", "salon_commission_value", "salon_commission_type")
    def _check_salon_amounts(self):
        for rec in self:
            if rec.salon_fixed_salary < 0 or rec.salon_daily_rate < 0 or rec.salon_commission_value < 0:
                raise ValidationError(self.env._("Salary and commission values cannot be negative."))
            if rec.salon_commission_type == "percent" and rec.salon_commission_value > 100:
                raise ValidationError(self.env._("A percentage commission cannot exceed 100."))
