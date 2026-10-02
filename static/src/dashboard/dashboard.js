/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const KPI_DEFS = [
    ["revenue", "Revenue", true], ["bookings", "Bookings"], ["completed", "Completed"],
    ["cancelled", "Cancelled"], ["no_show", "No-show"], ["customers", "Customers"],
    ["new_customers", "New customers"], ["returning_customers", "Returning customers"],
    ["staff_working", "Staff working"], ["commission", "Staff commission", true],
    ["expenses", "Expenses", true], ["est_profit", "Estimated profit", true],
    ["avg_bill", "Average bill value", true], ["no_show_rate", "No-show rate %"],
    ["repeat_rate", "Customer repeat rate %"],
];
const CHART_DEFS = [
    ["revenue_by_day", "Revenue by day"], ["revenue_by_staff", "Revenue by staff"],
    ["revenue_by_service", "Revenue by service"], ["booking_status", "Booking status"],
    ["commission_by_staff", "Commission by staff"], ["expenses_by_category", "Expenses by category"],
];

function fmtDate(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export class SalonDashboard extends Component {
    static template = "salon_management.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            period: "today", dateFrom: "", dateTo: "", employeeId: "", productId: "",
            data: null, staff: [], services: [], loading: true,
        });
        onWillStart(async () => {
            this.state.staff = await this.orm.searchRead(
                "hr.employee", [["salon_is_staff", "=", true]], ["name"]);
            this.state.services = await this.orm.searchRead(
                "product.product", [["is_salon_service", "=", true]], ["display_name"]);
            await this.load();
        });
    }

    get kpiDefs() { return KPI_DEFS; }
    get chartDefs() { return CHART_DEFS; }

    range() {
        const now = new Date();
        const s = this.state;
        if (s.period === "yesterday") {
            const y = new Date(now); y.setDate(y.getDate() - 1);
            return [fmtDate(y), fmtDate(y)];
        }
        if (s.period === "week") {
            const start = new Date(now);
            start.setDate(now.getDate() - ((now.getDay() + 6) % 7));
            return [fmtDate(start), fmtDate(now)];
        }
        if (s.period === "month") {
            return [fmtDate(new Date(now.getFullYear(), now.getMonth(), 1)), fmtDate(now)];
        }
        if (s.period === "custom") {
            return [s.dateFrom || fmtDate(now), s.dateTo || s.dateFrom || fmtDate(now)];
        }
        return [fmtDate(now), fmtDate(now)];
    }

    async load() {
        this.state.loading = true;
        const [from, to] = this.range();
        this.state.data = await this.orm.call("salon.dashboard", "get_dashboard_data", [], {
            date_from: from, date_to: to,
            employee_id: this.state.employeeId ? parseInt(this.state.employeeId) : false,
            product_id: this.state.productId ? parseInt(this.state.productId) : false,
        });
        this.state.loading = false;
    }

    onFilterChange(field, ev) {
        this.state[field] = ev.target.value;
        this.load();
    }

    formatKpi(key, isMoney) {
        const v = this.state.data.kpis[key] || 0;
        const txt = (Math.round(v * 100) / 100).toLocaleString(undefined, { maximumFractionDigits: 2 });
        return isMoney ? `${this.state.data.currency || ""} ${txt}` : txt;
    }

    rows(key) {
        const rows = this.state.data.charts[key] || [];
        const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1);
        return rows.map((r) => ({ ...r, pct: Math.round((Math.abs(r.value) / max) * 100) }));
    }
}

registry.category("actions").add("salon_management.dashboard", SalonDashboard);
