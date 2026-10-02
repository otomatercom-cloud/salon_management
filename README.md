# Salon Management (Odoo 19)

Customer → Booking → Staff → Check-in → Service → Completion → Invoice → Payment → Commission → Payout → Expenses → Profitability.

**Depends:** base, mail, hr, hr_attendance, product, account (no Payroll/Expenses/Inventory dependency).

## Reused standard models
res.partner (customers), hr.employee (staff), product.template/product.product (services), account.move / account.payment (invoices, split payments through several payments on different journals — create Cash / UPI / Card / Bank journals in Accounting), hr.attendance (working hours).

## New models
salon.booking, salon.booking.line, salon.commission.rule, salon.staff.payout, salon.expense (light, because hr_expense is not a dependency), plus SQL report views salon.staff.daily.report and salon.profit.report.

## Workflow
Draft → Confirmed → Checked-In → In Service → Completed → (Create Invoice, Register Payment) → Paid → Closed; Cancelled / No Show are terminal.
Transitions are enforced server-side (`TRANSITIONS` in models/booking.py). "Mark Paid" requires the invoice to be fully paid (Odoo 19 deprecated the invoice-paid hook).

## Groups
Staff < Receptionist < Manager; Accountant (read-only finance); Administrator = Manager + Accountant + payout reversal.
Salon Manager is given read/write on hr.employee (to configure staff pay) — staff/accountant get read only.
Discount limits (%): Configuration > Discount Limits (staff/receptionist default 10, manager default 30, admin unlimited).

## Commission precedence
staff+service rule > service rule > staff rule > global rule > staff default. Pay types without commission earn 0.

## KPI definitions
- Revenue: net (after discount, before tax) amount of Done service lines.
- Average bill value: revenue ÷ bookings Completed/Paid/Closed in the period.
- No-show rate: no-show bookings ÷ bookings that reached Confirmed or later (excluding Draft/Cancelled).
- Repeat rate: returning customers ÷ customers with a completed visit in the period.
- Staff revenue / commission: sum of Done line net amount / commission per staff.
- Estimated profit: revenue − commission − approved expenses. An estimate, not accounting profit (no product cost, no tax).

## Known limits (v1)
- Inventory consumption and Payroll are not integrated (no hard dependencies).
- Staff daily working hours use UTC day boundaries from hr.attendance.
- Double-booking is validated in the ORM (no DB lock); two simultaneous saves could race.
- Single currency per report (company currency).
