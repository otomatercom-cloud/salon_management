from datetime import datetime, timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.service.model import call_kw
from odoo.tests import TransactionCase, tagged

START = datetime(2026, 10, 5, 10, 0, 0)


@tagged("post_install", "-at_install")
class TestSalon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)

        def mk(login, xmlid):
            return Users.create({"name": login, "login": login, "email": login + "@x.com",
                                 "group_ids": [Command.set([cls.env.ref(xmlid).id])]})
        cls.u_staff = mk("s_staff", "salon_management.group_salon_staff")
        cls.u_reception = mk("s_recep", "salon_management.group_salon_receptionist")
        cls.u_manager = mk("s_mgr", "salon_management.group_salon_manager")
        cls.u_acc = mk("s_acc", "salon_management.group_salon_accountant")
        cls.u_admin = mk("s_adm", "salon_management.group_salon_admin")
        Emp = cls.env["hr.employee"]
        cls.emp_a = Emp.create({"name": "Staff A", "salon_is_staff": True, "user_id": cls.u_staff.id,
                                "salon_salary_type": "fixed_commission", "salon_fixed_salary": 10000,
                                "salon_commission_type": "percent", "salon_commission_value": 10})
        cls.emp_b = Emp.create({"name": "Staff B", "salon_is_staff": True, "salon_salary_type": "commission",
                                "salon_commission_type": "percent", "salon_commission_value": 10})
        cls.emp_fixed = Emp.create({"name": "Staff Fixed", "salon_is_staff": True, "salon_salary_type": "fixed",
                                    "salon_fixed_salary": 5000})
        Prod = cls.env["product.product"]
        base = {"type": "service", "is_salon_service": True, "salon_duration": 1.0}
        cls.haircut = Prod.create({**base, "name": "Haircut", "list_price": 500})
        cls.facial = Prod.create({**base, "name": "Facial", "list_price": 1500})
        cls.color = Prod.create({**base, "name": "Hair Color", "list_price": 2000})
        Rule = cls.env["salon.commission.rule"]
        Rule.create({"name": "Haircut 20%", "product_tmpl_id": cls.haircut.product_tmpl_id.id,
                     "method": "percent", "value": 20})
        Rule.create({"name": "Facial fixed 100", "product_tmpl_id": cls.facial.product_tmpl_id.id,
                     "method": "fixed", "value": 100})
        cls.partner = cls.env["res.partner"].create({"name": "Anjali"})

    # helpers
    def _booking(self, specs, start=START, user=None, **kw):
        env = self.env["salon.booking"].with_user(user) if user else self.env["salon.booking"]
        lines = [Command.create({"product_id": p.id, "employee_id": e.id, **extra})
                 for p, e, extra in specs]
        return env.create({
            "partner_id": self.partner.id, "start_datetime": start, "line_ids": lines, **kw})

    def _run_to_completed(self, booking):
        booking.action_confirm()
        booking.action_check_in()
        booking.action_start_service()
        booking.action_complete()

    # ------------------------------------------------------------ workflow
    def test_full_flow_with_split_payment(self):
        b = self._booking([(self.haircut, self.emp_a, {}), (self.facial, self.emp_b, {})])
        self.assertTrue(b.name.startswith("SAL/"))
        self.assertEqual(b.amount_untaxed, 2000)
        self._run_to_completed(b)
        self.assertEqual(b.state, "completed")
        self.assertEqual(b.payment_status, "unpaid")
        b.action_create_invoice()
        inv = b.invoice_id
        self.assertEqual(inv.state, "posted")
        self.assertEqual(inv.amount_untaxed, 2000)
        bank = self.env["account.journal"].search([("type", "=", "bank"), ("company_id", "=", b.company_id.id)], limit=1)
        reg = lambda amount: self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=inv.ids).create(
            {"amount": amount, "journal_id": bank.id}).action_create_payments()
        bank_meta = self.env["salon.booking"].with_user(self.u_reception).get_booking_meta()
        self.assertTrue(bank_meta["journals"] and bank_meta["staff"] and bank_meta["services"])
        with self.assertRaises(AccessError):
            b.with_user(self.u_staff).register_payment(bank.id, 800)
        with self.assertRaises(UserError):
            b.with_user(self.u_reception).register_payment(bank.id, 999999)
        self.assertEqual(b.with_user(self.u_reception).register_payment(bank.id, 800), "partial")
        self.assertEqual(b.payment_status, "partial")
        with self.assertRaises(UserError):
            b.action_mark_paid()
        reg(inv.amount_residual)
        self.assertEqual(b.payment_status, "paid")
        b.action_mark_paid()
        self.assertEqual(b.state, "paid")
        with self.assertRaises(UserError):
            b.write({"partner_id": self.env["res.partner"].create({"name": "Other"}).id})
        b.action_close()
        self.assertEqual(b.state, "closed")

    def test_invalid_transitions(self):
        b = self._booking([(self.haircut, self.emp_a, {})])
        with self.assertRaises(UserError):
            b.action_complete()
        with self.assertRaises(UserError):
            b.action_check_in()
        with self.assertRaises(UserError):
            b.write({"state": "confirmed"})
        b.action_confirm()
        with self.assertRaises(UserError):
            b.action_start_service()
        with self.assertRaises(UserError):
            b.with_context(salon_transition=True).write({"state": "paid"})

    def test_confirm_requires_lines_and_staff(self):
        empty = self.env["salon.booking"].create({"partner_id": self.partner.id, "start_datetime": START})
        with self.assertRaises(UserError):
            empty.action_confirm()
        nostaff = self.env["salon.booking"].create({
            "partner_id": self.partner.id, "start_datetime": START,
            "line_ids": [Command.create({"product_id": self.haircut.id})]})
        with self.assertRaises(UserError):
            nostaff.action_confirm()

    def test_cancellation_and_noshow(self):
        b = self._booking([(self.haircut, self.emp_a, {})])
        b.action_confirm()
        # receptionist cannot cancel a confirmed booking directly
        with self.assertRaises(AccessError):
            b.with_user(self.u_reception)._do_cancel("x")
        b.with_user(self.u_manager)._do_cancel("Customer asked")
        self.assertEqual(b.state, "cancelled")
        self.assertEqual(b.cancel_reason, "Customer asked")
        self.assertEqual(b.cancel_user_id, self.u_manager)
        self.assertTrue(b.cancel_date)
        self.assertEqual(set(b.line_ids.mapped("state")), {"cancelled"})
        with self.assertRaises(UserError):
            b.unlink()
        ns = self._booking([(self.haircut, self.emp_b, {})], start=START + timedelta(days=1))
        ns.action_confirm()
        ns.action_no_show_wizard()
        ns._do_no_show("Did not come")
        self.assertEqual(ns.state, "no_show")
        self.assertEqual(ns.cancel_reason, "Did not come")
        done = self._booking([(self.haircut, self.emp_a, {})], start=START + timedelta(days=2))
        self._run_to_completed(done)
        with self.assertRaises(UserError):
            done._do_cancel("late")
        with self.assertRaises(UserError):
            done.unlink()

    def test_wizard_requests_approval_for_receptionist(self):
        b = self._booking([(self.haircut, self.emp_a, {})])
        b.action_confirm()
        wiz = self.env["salon.booking.reason.wizard"].with_user(self.u_reception).create(
            {"booking_id": b.id, "mode": "cancel", "reason": "change of plans"})
        wiz.action_confirm()
        self.assertEqual(b.state, "confirmed")
        self.assertTrue(b.activity_ids)

    # ------------------------------------------------------------ commission
    def test_commission_multi_staff(self):
        b = self._booking([
            (self.haircut, self.emp_a, {}),                       # rule 20% of 500 = 100
            (self.facial, self.emp_b, {}),                        # fixed 100
            (self.color, self.emp_a, {}),                         # default 10% of 2000 = 200
        ])
        self._run_to_completed(b)
        by_emp = {}
        for line in b.line_ids:
            by_emp.setdefault(line.employee_id, [0, 0])
            by_emp[line.employee_id][0] += line.price_subtotal
            by_emp[line.employee_id][1] += line.commission_amount
        self.assertEqual(by_emp[self.emp_a], [2500, 300])
        self.assertEqual(by_emp[self.emp_b], [1500, 100])

    def test_commission_with_discount_and_no_commission_staff(self):
        b = self._booking([(self.haircut, self.emp_a, {"discount_type": "percent", "discount_value": 10}),
                           (self.haircut, self.emp_fixed, {})], start=START + timedelta(days=3))
        self._run_to_completed(b)
        a_line, f_line = b.line_ids
        self.assertEqual(a_line.price_subtotal, 450)
        self.assertAlmostEqual(a_line.commission_amount, 90)
        self.assertEqual(f_line.commission_amount, 0)

    def test_negative_commission_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["salon.commission.rule"].create({"name": "bad", "method": "fixed", "value": -5})

    # ------------------------------------------------------------ scheduling
    def test_overlap_rejected_and_adjacent_allowed(self):
        self._booking([(self.haircut, self.emp_a, {})])
        with self.assertRaises(ValidationError):
            self._booking([(self.facial, self.emp_a, {})], start=START + timedelta(minutes=30))
        self._booking([(self.facial, self.emp_a, {})], start=START + timedelta(hours=1))
        self._booking([(self.facial, self.emp_b, {})], start=START + timedelta(minutes=30))

    def test_cancelled_booking_frees_staff(self):
        b = self._booking([(self.haircut, self.emp_a, {})])
        b._do_cancel("x")
        self._booking([(self.facial, self.emp_a, {})])

    def test_sequential_lines_and_parallel_staff(self):
        b = self._booking([(self.haircut, self.emp_a, {}), (self.color, self.emp_a, {}),
                           (self.facial, self.emp_b, {"duration": 1.5})], start=START + timedelta(days=7))
        l1, l2, l3 = b.line_ids
        self.assertEqual(l2.line_start, l1.line_end)
        self.assertEqual(l3.line_start, b.start_datetime)
        self.assertEqual(b.duration, 2.0)
        self.assertEqual(b.end_datetime, b.start_datetime + timedelta(hours=2))

    def test_skill_restriction(self):
        self.emp_b.salon_service_ids = [Command.set([self.haircut.product_tmpl_id.id])]
        with self.assertRaises(ValidationError):
            self._booking([(self.facial, self.emp_b, {})], start=START + timedelta(days=9))
        self._booking([(self.haircut, self.emp_b, {})], start=START + timedelta(days=9))
        self.emp_b.salon_service_ids = [Command.clear()]

    # ------------------------------------------------------------ discounts / prices
    def test_discount_limits(self):
        start = START + timedelta(days=10)
        with self.assertRaises(ValidationError):
            self._booking([(self.haircut, self.emp_b, {"discount_value": 50})], user=self.u_reception, start=start)
        self._booking([(self.haircut, self.emp_b, {"discount_value": 10})], user=self.u_reception, start=start)
        start = START + timedelta(days=11)
        self._booking([(self.haircut, self.emp_b, {"discount_value": 25})], user=self.u_manager, start=start)
        with self.assertRaises(ValidationError):
            self._booking([(self.haircut, self.emp_a, {"discount_value": 40})], user=self.u_manager, start=start)
        self._booking([(self.haircut, self.emp_a, {"discount_value": 80})], user=self.u_admin, start=start)

    def test_fixed_discount_and_invoice_amount(self):
        b = self._booking([(self.color, self.emp_a, {"discount_type": "fixed", "discount_value": 200})],
                          start=START + timedelta(days=12))
        self.assertEqual(b.amount_untaxed, 1800)
        self._run_to_completed(b)
        b.action_create_invoice()
        self.assertEqual(b.invoice_id.amount_untaxed, 1800)

    def test_staff_cannot_change_price(self):
        start = START + timedelta(days=13)
        with self.assertRaises(ValidationError):
            self._booking([(self.haircut, self.emp_a, {"price_unit": 100})], user=self.u_reception, start=start)
        self._booking([(self.haircut, self.emp_a, {"price_unit": 450})], user=self.u_manager, start=start)

    # ------------------------------------------------------------ security
    def test_staff_sees_only_own_bookings_and_cannot_invoice(self):
        mine = self._booking([(self.haircut, self.emp_a, {})], start=START + timedelta(days=14))
        other = self._booking([(self.haircut, self.emp_b, {})], start=START + timedelta(days=14))
        visible = self.env["salon.booking"].with_user(self.u_staff).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(other, visible)
        mine.with_user(self.u_staff).action_confirm()
        mine.with_user(self.u_staff).action_check_in()
        mine.with_user(self.u_staff).action_start_service()
        with self.assertRaises(AccessError):
            mine.with_user(self.u_staff).action_complete()
        mine.line_ids.with_user(self.u_staff).action_done()
        self.assertEqual(mine.line_ids.state, "done")
        mine.action_complete()
        with self.assertRaises(AccessError):
            mine.with_user(self.u_staff).action_create_invoice()
        with self.assertRaises(AccessError):
            self.env["salon.commission.rule"].with_user(self.u_staff).create({"name": "x", "value": 1})

    def test_staff_cannot_approve_payout_or_expense(self):
        payout = self.env["salon.staff.payout"].create({"employee_id": self.emp_a.id})
        with self.assertRaises(AccessError):
            payout.with_user(self.u_staff).read(["name"])
        with self.assertRaises(AccessError):
            payout.with_user(self.u_reception).action_approve()
        exp = self.env["salon.expense"].with_user(self.u_reception).create({"name": "Rent", "amount": 100})
        with self.assertRaises(AccessError):
            exp.with_user(self.u_reception).action_approve()
        exp.with_user(self.u_manager).action_approve()
        self.assertEqual(exp.state, "approved")
        with self.assertRaises(UserError):
            exp.write({"amount": 5})

    # ------------------------------------------------------------ payouts
    def test_payout_lifecycle(self):
        day = START + timedelta(days=20)
        b = self._booking([(self.haircut, self.emp_a, {}), (self.color, self.emp_a, {})], start=day)
        self._run_to_completed(b)
        po = self.env["salon.staff.payout"].create({
            "employee_id": self.emp_a.id, "date_from": day.date(), "date_to": day.date(),
            "advance": 100, "other_deduction": 50})
        po.action_calculate()
        self.assertEqual(po.state, "calculated")
        self.assertEqual(po.commission_total, 300)
        self.assertEqual(po.base_pay, 10000)
        self.assertEqual(po.net_payable, 10000 + 300 - 150)
        # lines are not settled twice
        po2 = self.env["salon.staff.payout"].create({
            "employee_id": self.emp_a.id, "date_from": day.date(), "date_to": day.date()})
        po2.action_calculate()
        self.assertEqual(po2.commission_total, 0)
        with self.assertRaises(UserError):
            po.action_pay()
        po.with_user(self.u_manager).action_approve()
        po.with_user(self.u_manager).action_pay()
        self.assertEqual(po.state, "paid")
        self.assertTrue(po.expense_id)
        with self.assertRaises(UserError):
            po.action_pay()
        with self.assertRaises(UserError):
            po.write({"advance": 1})
        with self.assertRaises(AccessError):
            po.with_user(self.u_manager).action_reverse()
        po.with_user(self.u_admin).action_reverse()
        self.assertEqual(po.state, "approved")
        self.assertEqual(po.expense_id.id, False)
        po.with_user(self.u_manager).action_pay()
        po.with_user(self.u_manager).action_close()
        self.assertEqual(po.state, "closed")

    # ------------------------------------------------------------ customers / reports
    def test_customer_stats(self):
        p = self.env["res.partner"].create({"name": "Stats Customer"})
        mk = lambda days, emp: self.env["salon.booking"].create({
            "partner_id": p.id, "start_datetime": START + timedelta(days=days),
            "line_ids": [Command.create({"product_id": self.haircut.id, "employee_id": emp.id})]})
        b1, b2, b3 = mk(30, self.emp_a), mk(31, self.emp_a), mk(32, self.emp_a)
        self._run_to_completed(b1)
        b2._do_cancel("x")
        b3.action_confirm()
        b3._do_no_show("")
        p.invalidate_recordset()
        self.assertEqual(p.salon_visit_count, 1)
        self.assertEqual(p.salon_cancel_count, 1)
        self.assertEqual(p.salon_noshow_count, 1)
        self.assertEqual(p.salon_total_spent, 500)
        self.assertEqual(p.salon_customer_status, "new")

    def test_reports_and_dashboard(self):
        b = self._booking([(self.haircut, self.emp_a, {})], start=START + timedelta(days=40))
        self._run_to_completed(b)
        self.env["salon.expense"].create({"name": "Water", "category": "water", "amount": 50,
                                          "date": b.booking_date}).action_approve()
        self.env.flush_all()
        daily = self.env["salon.staff.daily.report"].search([("employee_id", "=", self.emp_a.id),
                                                             ("date", "=", b.booking_date)])
        self.assertEqual(daily.services_done, 1)
        self.assertEqual(daily.revenue, 500)
        self.assertEqual(daily.commission, 100)
        profit = self.env["salon.profit.report"]._read_group(
            [("date", "=", b.booking_date)], [], ["amount:sum"])
        self.assertEqual(profit[0][0], 500 - 100 - 50)
        data = call_kw(self.env["salon.dashboard"], "get_dashboard_data", [],
                       {"date_from": str(b.booking_date), "date_to": str(b.booking_date)})
        k = data["kpis"]
        self.assertEqual(k["revenue"], 500)
        self.assertEqual(k["completed"], 1)
        self.assertEqual(k["commission"], 100)
        self.assertEqual(k["expenses"], 50)
        self.assertEqual(k["est_profit"], 350)
        self.assertEqual(k["avg_bill"], 500)
        self.assertEqual(data["charts"]["revenue_by_staff"][0]["value"], 500)

    def test_app_context(self):
        ctx = self.env["salon.dashboard"].with_user(self.u_manager).get_app_context()
        self.assertTrue(ctx["roles"]["manager"] and ctx["roles"]["staff"])
        self.assertFalse(ctx["roles"]["admin"])
        self.assertEqual(ctx["discount_limit"], 30)

    def test_history_readable_by_plain_user(self):
        b = self._booking([(self.haircut, self.emp_a, {})])
        b.with_context(tracking_disable=False, mail_notrack=False).action_confirm()
        hist = self.env["salon.dashboard"].with_user(self.u_reception).get_history("salon.booking", b.id)
        self.assertIsInstance(hist, list)  # tracking is disabled in tests; shape + access are what matter
        with self.assertRaises(AccessError):
            self.env["salon.dashboard"].with_user(self.u_reception).get_history("res.partner", 1)

    def test_manager_manages_services_staff_cannot(self):
        vals = {"name": "Spa", "type": "service", "is_salon_service": True, "list_price": 900}
        svc = self.env["product.template"].with_user(self.u_manager).create(vals)
        self.assertTrue(svc.is_salon_service)
        with self.assertRaises(AccessError):
            self.env["product.template"].with_user(self.u_staff).create(vals)
