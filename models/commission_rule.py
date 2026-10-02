from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SalonCommissionRule(models.Model):
    _name = "salon.commission.rule"
    _description = "Salon Commission Rule"
    _order = "sequence, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    employee_id = fields.Many2one("hr.employee", string="Staff", domain=[("salon_is_staff", "=", True)],
                                  help="Leave empty to apply to any staff.")
    product_tmpl_id = fields.Many2one("product.template", string="Service",
                                      domain=[("is_salon_service", "=", True)],
                                      help="Leave empty to apply to any service.")
    method = fields.Selection(
        [("percent", "Percentage of line amount"), ("fixed", "Fixed amount per service unit")],
        required=True, default="percent")
    value = fields.Float(required=True)

    @api.constrains("value", "method")
    def _check_value(self):
        for rec in self:
            if rec.value < 0:
                raise ValidationError(self.env._("Commission cannot be negative."))
            if rec.method == "percent" and rec.value > 100:
                raise ValidationError(self.env._("A percentage commission cannot exceed 100."))

    @api.model
    def _compute_commission(self, employee, product, quantity, amount, rules=None):
        """Commission for one service line.

        Precedence: staff+service rule > service rule > staff rule > global rule >
        staff default. Staff whose pay type has no commission earn 0.
        """
        from .hr_employee import COMMISSION_TYPES
        if not employee or employee.salon_salary_type not in COMMISSION_TYPES:
            return 0.0
        if rules is None:
            rules = self.sudo().search([])
        tmpl = product.product_tmpl_id
        best, best_score = None, -1
        for rule in rules:
            if rule.employee_id and rule.employee_id != employee:
                continue
            if rule.product_tmpl_id and rule.product_tmpl_id != tmpl:
                continue
            if rule.company_id and employee.company_id and rule.company_id != employee.company_id:
                continue
            score = (2 if rule.product_tmpl_id else 0) + (1 if rule.employee_id else 0)
            if score > best_score:
                best, best_score = rule, score
        if best:
            method, value = best.method, best.value
        else:
            method, value = employee.salon_commission_type, employee.salon_commission_value
        if method == "fixed":
            return value * quantity
        return amount * value / 100.0
