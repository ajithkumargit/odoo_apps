def migrate(cr, version):
    # The retired scheduler must not leave old imports locked in its queue.
    cr.execute("""
        UPDATE shop_purchase_import
           SET extraction_status = 'not_extracted', extraction_error = NULL
         WHERE extraction_status = 'queued'
    """)
