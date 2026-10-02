from odoo import fields, models, tools


class SalonProfitReport(models.Model):
    _name = "salon.profit.report"
    _description = "Salon Profitability (Estimate)"
    _auto = False
    _order = "date desc"

    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    kind = fields.Selection(
        [("revenue", "Net Revenue"), ("commission", "Staff Commission"), ("expense", "Operating Expense")],
        readonly=True)
    category = fields.Char(readonly=True)
    employee_id = fields.Many2one("hr.employee", readonly=True)
    product_id = fields.Many2one("product.product", string="Service", readonly=True)
    amount = fields.Float(string="Estimated Profit Impact", readonly=True,
                          help="Revenue is positive; commission and expenses are negative. "
                               "The total is an ESTIMATE, not accounting profit.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW salon_profit_report AS (
            SELECT row_number() OVER () AS id, x.* FROM (
                SELECT bl.booking_date AS date, bl.company_id, 'revenue' AS kind,
                       'Services' AS category, bl.employee_id, bl.product_id, bl.price_subtotal AS amount
                  FROM salon_booking_line bl WHERE bl.state = 'done'
                UNION ALL
                SELECT bl.booking_date, bl.company_id, 'commission', 'Commission',
                       bl.employee_id, bl.product_id, -bl.commission_amount
                  FROM salon_booking_line bl WHERE bl.state = 'done' AND bl.commission_amount <> 0
                UNION ALL
                SELECT e.date, e.company_id, 'expense', e.category, NULL, NULL, -e.amount
                  FROM salon_expense e WHERE e.state = 'approved'
            ) x)""")
