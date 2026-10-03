import base64
from io import BytesIO
from odoo.tests.common import TransactionCase
from PIL import Image


class TestBillImageReplacement(TransactionCase):
    def setUp(self):
        super().setUp()
        self.bill = self.env['shop.purchase.import'].create({
            'original_file': base64.b64encode(b'first image'), 'original_file_name': 'first.jpg',
            'manual_crop_enabled': True, 'crop_left': 20, 'crop_top': 10, 'crop_right': 80, 'crop_bottom': 70,
        })

    def assertFullImage(self, record):
        self.assertFalse(record.manual_crop_enabled)
        self.assertEqual((record.crop_left, record.crop_top, record.crop_right, record.crop_bottom), (0, 0, 100, 100))

    def test_main_replacement_resets_old_crop(self):
        self.bill.original_file = base64.b64encode(b'new image')
        self.assertFullImage(self.bill)

    def test_continuation_replacement_resets_old_crop(self):
        page = self.env['shop.purchase.import.page'].create({
            'import_id': self.bill.id, 'page_file': base64.b64encode(b'first page'), 'page_file_name': 'first.jpg',
            'manual_crop_enabled': True, 'crop_left': 20, 'crop_top': 10, 'crop_right': 80, 'crop_bottom': 70,
        })
        page.page_file = base64.b64encode(b'new page')
        self.assertFullImage(page)

    def test_new_image_explicit_crop_is_preserved(self):
        self.bill.write({'original_file': base64.b64encode(b'new image'),
                         'manual_crop_enabled': True, 'crop_left': 5, 'crop_top': 6, 'crop_right': 95, 'crop_bottom': 96})
        self.assertTrue(self.bill.manual_crop_enabled)
        self.assertEqual((self.bill.crop_left, self.bill.crop_top, self.bill.crop_right, self.bill.crop_bottom), (5, 6, 95, 96))

    def test_unrelated_edit_keeps_crop(self):
        self.bill.note = 'Review this image'
        self.assertTrue(self.bill.manual_crop_enabled)
        self.assertEqual(self.bill.crop_left, 20)

    def test_rotate_and_save_main_and_continuation_pages(self):
        buffer = BytesIO()
        Image.new('RGB', (40, 20), 'white').save(buffer, format='JPEG')
        encoded = base64.b64encode(buffer.getvalue())
        self.bill.original_file = encoded
        page = self.env['shop.purchase.import.page'].create({
            'import_id': self.bill.id, 'page_file': encoded, 'page_file_name': 'second.jpg',
            'manual_crop_enabled': True, 'crop_left': 10,
        })
        self.bill.action_rotate_bill_image(1)
        page.action_rotate_bill_image(3)
        for record, field in ((self.bill, 'original_file'), (page, 'page_file')):
            with Image.open(BytesIO(base64.b64decode(record[field]))) as rotated:
                self.assertEqual(rotated.size, (20, 40))
            self.assertFullImage(record)
