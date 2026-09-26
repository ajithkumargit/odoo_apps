from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class RetailPosBridge(models.Model):
    _name = "retail.pos.bridge"
    _description = "Retail POS Bridge"

    name = fields.Char(default="Retail POS Bridge")

    @api.model
    def retail_pos_products(self, search=None, limit=1000):
        domain = [("sale_ok", "=", True)]
        if search:
            search_term = str(search)
            domain += [
                "|",
                "|",
                ("name", "ilike", search_term),
                ("barcode", "ilike", search_term),
                ("default_code", "ilike", search_term),
            ]

        products = self.env["product.product"].sudo().search(domain, limit=int(limit or 1000))
        return [self._product_payload(product) for product in products]

    @api.model
    def retail_pos_create_order(self, sale):
        if not isinstance(sale, dict):
            raise ValidationError(_("Sale payload must be an object."))

        lines = sale.get("lines") or []
        if not lines:
            raise ValidationError(_("Sale must contain at least one line."))

        partner = self._get_sale_partner(sale)
        order_lines = []

        for line in lines:
            product = self.env["product.product"].sudo().browse(int(line.get("productOdooId") or 0))
            if not product.exists():
                raise ValidationError(_("Product not found for sale line."))

            quantity = float(line.get("quantity") or 0)
            if quantity <= 0:
                raise ValidationError(_("Sale line quantity must be greater than zero."))

            unit_price = float(line.get("unitPrice") or product.lst_price)
            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": product.id,
                        "name": product.display_name,
                        "product_uom_qty": quantity,
                        "price_unit": unit_price,
                    },
                )
            )

        order = (
            self.env["sale.order"]
            .sudo()
            .create(
                {
                    "partner_id": partner.id,
                    "origin": sale.get("externalReference") or _("Retail POS"),
                    "note": sale.get("note") or _("Created from custom Retail POS."),
                    "order_line": order_lines,
                }
            )
        )

        if sale.get("confirmOrder", True):
            order.action_confirm()

        return {
            "model": "sale.order",
            "id": order.id,
            "name": order.name,
            "state": order.state,
            "amount_total": order.amount_total,
        }

    @api.model
    def retail_pos_adjust_stock(self, adjustment):
        if not isinstance(adjustment, dict):
            raise ValidationError(_("Stock adjustment payload must be an object."))

        product = self.env["product.product"].sudo().browse(
            int(adjustment.get("productOdooId") or 0)
        )
        if not product.exists():
            raise ValidationError(_("Product not found for stock adjustment."))

        quantity_delta = float(adjustment.get("quantityDelta") or 0)
        if not quantity_delta:
            raise ValidationError(_("Stock adjustment quantity cannot be zero."))

        location = self._get_stock_location(adjustment)
        self.env["stock.quant"].sudo()._update_available_quantity(product, location, quantity_delta)

        return {
            "product_id": product.id,
            "product_name": product.display_name,
            "location_id": location.id,
            "location_name": location.display_name,
            "quantity_delta": quantity_delta,
            "qty_available": product.qty_available,
        }

    def _product_payload(self, product):
        template = product.product_tmpl_id
        calculated_price = template.retail_calculated_selling_price
        return {
            "id": product.id,
            "templateId": template.id,
            "name": product.display_name,
            "sku": product.default_code or "",
            "barcode": product.barcode or "",
            "category": product.categ_id.display_name if product.categ_id else "",
            "stock": product.qty_available,
            "forecastStock": product.virtual_available,
            "retailPrice": product.lst_price,
            "costPrice": template.standard_price,
            "purchaseTaxPercent": template.retail_purchase_tax_percent,
            "profitPercent": template.retail_profit_percent,
            "wholesaleMinQty": template.retail_wholesale_min_qty,
            "calculatedSellingPrice": calculated_price,
        }

    def _get_sale_partner(self, sale):
        partner_id = int(sale.get("customerOdooId") or 0)
        if partner_id:
            partner = self.env["res.partner"].sudo().browse(partner_id)
            if partner.exists():
                return partner

        partner = self.env["res.partner"].sudo().search([("name", "=", "Retail Walk-in Customer")], limit=1)
        if partner:
            return partner

        return self.env["res.partner"].sudo().create(
            {
                "name": "Retail Walk-in Customer",
                "customer_rank": 1,
            }
        )

    def _get_stock_location(self, adjustment):
        location_id = int(adjustment.get("locationOdooId") or 0)
        if location_id:
            location = self.env["stock.location"].sudo().browse(location_id)
            if location.exists() and location.usage == "internal":
                return location
            raise UserError(_("Selected stock location is not a valid internal location."))

        location = self.env["stock.location"].sudo().search([("usage", "=", "internal")], limit=1)
        if not location:
            raise UserError(_("No internal stock location found."))

        return location
