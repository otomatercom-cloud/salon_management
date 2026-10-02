from odoo import fields, models, tools


class SalonStaffDailyReport(models.Model):
    _name = "salon.staff.daily.report"
    _description = "Salon Staff Daily Work"
    _auto = False
    _order = "date desc, employee_id"

    employee_id = fields.Many2one("hr.employee", readonly=True)
    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    bookings = fields.Integer(readonly=True)
    services_done = fields.Integer(string="Services Completed", readonly=True)
    services_cancelled = fields.Integer(string="Services Cancelled", readonly=True)
    no_shows = fields.Integer(string="No-shows", readonly=True)
    revenue = fields.Float(readonly=True)
    commission = fields.Float(readonly=True)
    worked_hours = fields.Float(string="Working Hours", readonly=True,
                                help="Hours from Odoo Attendance (day boundaries in UTC).")
    revenue_per_hour = fields.Float(string="Revenue / Hour", readonly=True, aggregator=False)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW salon_staff_daily_report AS (
            WITH l AS (
                SELECT bl.employee_id, b.booking_date AS date, b.company_id,
                       COUNT(DISTINCT b.id) AS bookings,
                       COUNT(*) FILTER (WHERE bl.state = 'done') AS services_done,
                       COUNT(*) FILTER (WHERE bl.state = 'cancelled' AND b.state = 'cancelled') AS services_cancelled,
                       COUNT(DISTINCT b.id) FILTER (WHERE b.state = 'no_show') AS no_shows,
                       COALESCE(SUM(bl.price_subtotal) FILTER (WHERE bl.state = 'done'), 0) AS revenue,
                       COALESCE(SUM(bl.commission_amount) FILTER (WHERE bl.state = 'done'), 0) AS commission
                  FROM salon_booking_line bl
                  JOIN salon_booking b ON b.id = bl.booking_id
                 WHERE bl.employee_id IS NOT NULL
              GROUP BY bl.employee_id, b.booking_date, b.company_id
            ), a AS (
                SELECT at.employee_id, (at.check_in AT TIME ZONE 'UTC')::date AS date,
                       e.company_id, SUM(at.worked_hours) AS worked_hours
                  FROM hr_attendance at
                  JOIN hr_employee e ON e.id = at.employee_id
                 WHERE e.salon_is_staff
              GROUP BY at.employee_id, (at.check_in AT TIME ZONE 'UTC')::date, e.company_id
            )
            SELECT row_number() OVER () AS id,
                   COALESCE(l.employee_id, a.employee_id) AS employee_id,
                   COALESCE(l.date, a.date) AS date,
                   COALESCE(l.company_id, a.company_id) AS company_id,
                   COALESCE(l.bookings, 0) AS bookings,
                   COALESCE(l.services_done, 0) AS services_done,
                   COALESCE(l.services_cancelled, 0) AS services_cancelled,
                   COALESCE(l.no_shows, 0) AS no_shows,
                   COALESCE(l.revenue, 0) AS revenue,
                   COALESCE(l.commission, 0) AS commission,
                   COALESCE(a.worked_hours, 0) AS worked_hours,
                   CASE WHEN COALESCE(a.worked_hours, 0) > 0
                        THEN COALESCE(l.revenue, 0) / a.worked_hours END AS revenue_per_hour
              FROM l
              FULL JOIN a ON a.employee_id = l.employee_id AND a.date = l.date
            )""")
