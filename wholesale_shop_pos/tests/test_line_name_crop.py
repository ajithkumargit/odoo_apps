import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from ..models import purchase_import


class TestLineNameCrop(TransactionCase):
    def setUp(self):
        super().setUp()
        self.bill = self.env['shop.purchase.import'].create({
            'original_file': base64.b64encode(b'image'),
            'original_file_name': 'bill.jpg',
        })

    def test_sources_preserve_pages_with_same_filename(self):
        self.env['shop.purchase.import.page'].create({
            'import_id': self.bill.id,
            'page_file': base64.b64encode(b'page'),
            'page_file_name': 'bill.jpg',
        })
        sources = self.bill.get_product_name_crop_sources()
        self.assertEqual(len(sources), 2)
        self.assertNotEqual(sources[0]['key'], sources[1]['key'])
        self.assertIn('/original_file?', sources[0]['url'])

    def test_extract_is_preview_without_changing_bill(self):
        original = self.bill.original_file
        result = {'text': 'Product Name', 'engine': 'test'}
        with patch.object(purchase_import, 'extract_product_name', return_value=result) as extractor:
            self.assertEqual(self.bill.extract_cropped_product_name(base64.b64encode(b'crop').decode()), result)
            extractor.assert_called_once_with(b'crop')
        self.assertEqual(self.bill.original_file, original)
        self.assertFalse(self.bill.line_ids)

    def test_invalid_payload_and_ocr_error(self):
        for value in (False, '', 'not base64!'):
            with self.assertRaises(UserError):
                self.bill.extract_cropped_product_name(value)
        with patch.object(purchase_import, 'extract_product_name', side_effect=purchase_import.LocalOCRError('No name found')):
            with self.assertRaisesRegex(UserError, 'No name found'):
                self.bill.extract_cropped_product_name('YQ==')
