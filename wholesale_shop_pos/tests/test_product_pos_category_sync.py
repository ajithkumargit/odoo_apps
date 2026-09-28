from odoo.tests.common import TransactionCase


class TestProductPosCategorySync(TransactionCase):
    def test_profit_percentage_only_updates_sales_price_when_changed(self):
        template = self.env["product.template"].create({
            "name": "Profit Priced Product",
            "standard_price": 100.0,
            "list_price": 150.0,
            "shop_profit_percent": 20.0,
        })

        self.assertAlmostEqual(template.list_price, 150.0, places=2)

        variant = template.product_variant_id.with_company(self.env.company)
        variant.standard_price = 80.0
        self.assertAlmostEqual(template.list_price, 150.0, places=2)

        template.shop_profit_percent = 25.0
        self.assertAlmostEqual(template.list_price, 100.0, places=2)

        template.shop_price_check = 120.0
        self.assertAlmostEqual(
            template.shop_price_check_profit_percent, 50.0, places=2
        )
        self.assertAlmostEqual(template.list_price, 100.0, places=2)
        template.action_apply_shop_checked_profit()
        self.assertAlmostEqual(template.shop_profit_percent, 50.0, places=2)
        self.assertAlmostEqual(template.list_price, 120.0, places=2)

    def test_each_variant_profit_controls_its_own_pos_and_pricelist_price(self):
        attribute = self.env["product.attribute"].create({"name": "Pack Size"})
        small = self.env["product.attribute.value"].create({
            "name": "Small",
            "attribute_id": attribute.id,
        })
        large = self.env["product.attribute.value"].create({
            "name": "Large",
            "attribute_id": attribute.id,
        })
        template = self.env["product.template"].create({
            "name": "Variant Profit Product",
            "list_price": 10.0,
            "attribute_line_ids": [(0, 0, {
                "attribute_id": attribute.id,
                "value_ids": [(6, 0, (small | large).ids)],
            })],
        })
        variants = template.product_variant_ids.sorted("id")
        self.assertEqual(len(variants), 2)

        variants[0].write({
            "standard_price": 100.0,
            "shop_variant_profit_percent": 20.0,
        })
        variants[1].write({
            "standard_price": 50.0,
            "shop_variant_profit_percent": 10.0,
        })

        self.assertAlmostEqual(variants[0].lst_price, 120.0, places=2)
        self.assertAlmostEqual(variants[1].lst_price, 55.0, places=2)
        variants[0].shop_variant_price_check = 130.0
        self.assertAlmostEqual(
            variants[0].shop_variant_price_check_profit_percent, 30.0, places=2
        )
        self.assertAlmostEqual(variants[0].lst_price, 120.0, places=2)
        variants[0].action_apply_shop_variant_checked_profit()
        self.assertAlmostEqual(
            variants[0].shop_variant_profit_percent, 30.0, places=2
        )
        self.assertAlmostEqual(variants[0].lst_price, 130.0, places=2)
        pricelist = self.env["product.pricelist"].create({
            "name": "Variant Profit Pricelist",
            "currency_id": self.env.company.currency_id.id,
        })
        self.assertAlmostEqual(
            pricelist._get_product_price(variants[0], 1.0), 130.0, places=2
        )
        self.assertAlmostEqual(
            pricelist._get_product_price(variants[1], 1.0), 55.0, places=2
        )

    def test_product_category_creates_pos_category_and_assigns_product(self):
        product_category = self.env["product.category"].create({
            "name": "Sync Beverages",
        })

        self.assertTrue(product_category.shop_pos_category_id)
        self.assertEqual(product_category.shop_pos_category_id.name, "Sync Beverages")
        self.assertEqual(
            product_category.shop_pos_category_id.shop_product_category_id,
            product_category,
        )

        product = self.env["product.template"].create({
            "name": "Sync Cola",
            "categ_id": product_category.id,
            "sale_ok": True,
        })

        self.assertTrue(product.available_in_pos)
        self.assertEqual(
            product.pos_categ_ids,
            product_category.shop_pos_category_id,
        )

        existing_parent = self.env["product.category"].with_context(
            shop_category_sync=True
        ).create({"name": "Existing Product Parent"})
        self.assertFalse(existing_parent.shop_pos_category_id)
        product_category.parent_id = existing_parent
        self.assertEqual(
            product_category.shop_pos_category_id.parent_id,
            existing_parent.shop_pos_category_id,
        )

    def test_pos_category_creates_product_category_and_assigns_product(self):
        pos_category = self.env["pos.category"].create({
            "name": "Sync Snacks",
        })

        self.assertTrue(pos_category.shop_product_category_id)
        self.assertEqual(
            pos_category.shop_product_category_id.name,
            "Sync Snacks",
        )
        self.assertEqual(
            pos_category.shop_product_category_id.shop_pos_category_id,
            pos_category,
        )

        product = self.env["product.template"].create({
            "name": "Sync Chips",
            "pos_categ_ids": [(6, 0, pos_category.ids)],
            "sale_ok": True,
        })

        self.assertTrue(product.available_in_pos)
        self.assertEqual(product.categ_id, pos_category.shop_product_category_id)

        existing_parent = self.env["pos.category"].with_context(
            shop_category_sync=True
        ).create({"name": "Existing POS Parent"})
        self.assertFalse(existing_parent.shop_product_category_id)
        pos_category.parent_id = existing_parent
        self.assertEqual(
            pos_category.shop_product_category_id.parent_id,
            existing_parent.shop_product_category_id,
        )
