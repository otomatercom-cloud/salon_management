from odoo import fields, models
from odoo.exceptions import UserError


class SalonBookingReasonWizard(models.TransientModel):
    _name = "salon.booking.reason.wizard"
    _description = "Booking Cancellation / No-show Reason"

    booking_id = fields.Many2one("salon.booking", required=True)
    mode = fields.Selection([("cancel", "Cancel"), ("no_show", "No Show")], required=True)
    reason = fields.Char(string="Reason")

    def action_confirm(self):
        self.ensure_one()
        booking = self.booking_id
        if self.mode == "cancel":
            if not self.reason:
                raise UserError(self.env._("A cancellation reason is required."))
            manager = self.env.su or self.env.user.has_group("salon_management.group_salon_manager")
            if booking.state == "confirmed" and not manager:
                booking._request_cancellation(self.reason)
                return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                    "title": self.env._("Approval required"),
                    "message": self.env._("Cancelling a confirmed booking needs manager approval. Managers were notified."),
                    "type": "warning", "next": {"type": "ir.actions.act_window_close"}}}
            booking._do_cancel(self.reason)
        else:
            booking._do_no_show(self.reason or "")
        return {"type": "ir.actions.act_window_close"}
