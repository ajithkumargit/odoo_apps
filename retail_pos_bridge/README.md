# Retail POS Bridge

Custom Odoo 19 addon for the Angular retail POS app.

## What it adds

- Product fields for the same custom pricing rule used in the POS:
  - Wholesale minimum quantity
  - Purchase tax %
  - Profit %
  - Calculated selling price
- Bridge model: `retail.pos.bridge`
- External methods:
  - `retail_pos_products`
  - `retail_pos_create_order`
  - `retail_pos_adjust_stock`

## Install

1. Copy this folder to:

   `D:\odoo-projects\odoo-19.0\custom_addons\retail_pos_bridge`

2. Restart Odoo.
3. Enable developer mode.
4. Apps -> Update Apps List.
5. Search for `Retail POS Bridge`.
6. Install it.

Your `odoo.conf` already includes:

`D:\odoo-projects\odoo-19.0\custom_addons`

## Odoo 19 JSON-2 examples

Products:

```http
POST /json/2/retail.pos.bridge/retail_pos_products
Authorization: bearer YOUR_ODOO_API_KEY
Content-Type: application/json

{"limit": 500}
```

Create sale:

```json
{
  "sale": {
    "externalReference": "POS-LOCAL-001",
    "confirmOrder": true,
    "paymentMethod": "cash",
    "lines": [
      {
        "productOdooId": 1,
        "quantity": 4,
        "unitPrice": 9.87
      }
    ]
  }
}
```

Stock adjustment:

```json
{
  "adjustment": {
    "productOdooId": 1,
    "quantityDelta": 10,
    "reason": "Opening stock"
  }
}
```

## Notes

`retail_pos_create_order` creates and confirms a `sale.order`. That is a safe first integration because it uses standard Odoo sales and stock flow. If you later want exact Odoo POS sessions/payments, extend this module to create `pos.order` records against a selected POS session.
