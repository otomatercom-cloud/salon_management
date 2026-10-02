from odoo import fields, models, api
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_salon_service = fields.Boolean(string="Salon Service", index=True)
    salon_duration = fields.Float(string="Duration (hours)", default=1.0)
    salon_commission_rule_ids = fields.One2many(
        "salon.commission.rule", "product_tmpl_id", string="Commission Rules")

    @api.constrains("salon_duration")
    def _check_salon_duration(self):
        for rec in self:
            if rec.salon_duration < 0:
                raise ValidationError(self.env._("Service duration cannot be negative."))
