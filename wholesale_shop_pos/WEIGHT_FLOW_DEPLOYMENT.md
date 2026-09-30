# Weight and bulk purchase flows: 19.0.1.45.0

Back up the database and filestore before upgrading. Stop the application, run its existing Python/odoo-bin command with the same config and database plus `-u wholesale_shop_pos --stop-after-init --no-http`, then restart only if the upgrade succeeds. Restarting without upgrading will not create the new Bulk Stock Product field.

## Purchase entry

For one 50 kg bag costing 2450, enter Quantity 1, Unit kg, Unit Price 2450 and Items in One Purchase Unit 50. On confirmation, the unit becomes a 50 kg pack. Receipt adds 50 kg; received quantity and bill quantity remain one pack; bill total remains 2450; cost becomes 49/kg before any applicable taxes or discount. Alternatively enter 50 kg at 49 with Items in One Purchase Unit 1.

Reviewed imports already multiply bag quantity and divide bag price when preparing the PO. They continue to use base quantities with count 1. Do not apply the bag count again to those normalized lines.

Completed historical receipts are not changed. Editing their cost inputs can update product cost but does not repair inventory quantities or incorrect accounting documents. Review and correct historical orders through returns and replacement documents as appropriate.

## Loose stock versus separate packs

On each loose weighted product, select the variant holding bulk inventory under Weight Pricing > Bulk Stock Product (normally 1kg). Receive purchases into that product. POS sales and refunds of its other weight variants then draw from or restore that source. With kg units, POS quantities are already fractional kg; with Units, quantities are converted by the weight ratio. Sales lines retain the selected variant and its selling price.

Leave Bulk Stock Product empty for products with separately stocked packs. It is optional and editable. Other non-weight attributes must match the source; different flavours or brands do not share stock. Shared stock currently supports untracked products; lot/serial-tracked products retain their normal stock behavior. This routing covers POS stock movements, not Sales app deliveries or arbitrary inventory transfers.

Setting the source does not consolidate existing stock. Review active and archived variants for leftover balances. Use reviewed inventory corrections rather than directly changing historical stock moves or SQL quantities. Linked POS refunds retain the stock source used by their original sale, including old sales that used separate variant stock.

## Cost and selling prices

For Units weight variants, cost = Cost/kg * grams / 1000. For kg variants, the accounting cost remains per kg and Selected Weight Cost displays the proportional amount. Positive Profit % recalculates sales when the kg cost changes; zero Profit % keeps the manually entered sales price. An explicit sales amount in the same edit wins. Blank/free OCR lines do not erase costs.

Archived original bulk products on old imports can still restore the kg cost after weight variants are created. Existing real weighted-pack bill lines continue to convert their own pack cost into the kg basis.

## Verification before deployment

Run the wholesale_shop_pos test suite on a restored copy, plus `node wholesale_shop_pos/tests/test_pos_weight_quantity.cjs`. Check a purchase receipt, partial receipt, vendor bill, return/reimport, manual cost/profit edit, and POS sale/refund. Verify both kg and Units products and an unrelated separately stocked product.

Run `scripts/audit_weight_flows.py` inside an Odoo shell and call `audit_weight_flows(env)` for a read-only report. Inspect count_without_stock_conversion, archived balances, and the intended profit settings. Review unexpected historical amounts with the business owner; do not automatically infer corrections from current product prices.

The module upgrade does not automatically select bulk stock sources, move inventory, alter historical purchases, or replace saved profit percentages.
