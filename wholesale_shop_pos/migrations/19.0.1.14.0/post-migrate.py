def migrate(cr, version):
    """Backfill explicit roles from real business documents, never ranks."""
    cr.execute(
        """
        UPDATE res_partner partner
           SET shop_is_vendor = TRUE
         WHERE NOT partner.shop_is_vendor
           AND partner.id IN (
                SELECT partner_id FROM purchase_order WHERE partner_id IS NOT NULL
                UNION
                SELECT partner_id FROM product_supplierinfo WHERE partner_id IS NOT NULL
                UNION
                SELECT partner_id FROM account_move
                 WHERE partner_id IS NOT NULL
                   AND move_type IN ('in_invoice', 'in_refund', 'in_receipt')
                UNION
                SELECT vendor_id FROM shop_purchase_import WHERE vendor_id IS NOT NULL
                UNION
                SELECT partner_id FROM shop_vendor_product_map WHERE partner_id IS NOT NULL
                UNION
                SELECT partner_id FROM shop_bill_ocr_template WHERE partner_id IS NOT NULL
           )
        """
    )
    cr.execute(
        """
        UPDATE res_partner partner
           SET shop_is_customer = TRUE
         WHERE NOT partner.shop_is_customer
           AND partner.id IN (
                SELECT partner_id FROM pos_order WHERE partner_id IS NOT NULL
                UNION
                SELECT partner_id FROM account_move
                 WHERE partner_id IS NOT NULL
                   AND move_type IN ('out_invoice', 'out_refund', 'out_receipt')
           )
        """
    )
