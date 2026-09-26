"""Photographed rate-first Tamil bills with bag quantities and handwritten marks."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('rate_first_ocr', Path(__file__).parents[1] / 'models' / 'local_bill_ocr.py')
ocr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ocr)


class TestRateFirstBillParser(unittest.TestCase):
    def setUp(self):
        self.page = json.loads((Path(__file__).parent / 'fixtures' / 'rate_first_tamil_table_tokens.json').read_text())

    def parse(self, tokens=None):
        return ocr._parse_page(tokens or self.page['tokens'], self.page['width'], self.page['height'])

    def test_all_rows_preserve_tamil_names_and_printed_amounts(self):
        result = self.parse()
        self.assertEqual(len(result['lines']), 13)
        self.assertEqual([line['quantity'] for line in result['lines']], [4,1,1,10,10,5,1,1,2,1,1,1,1])
        amounts = [10680,6400,6250,850,1050,850,2650,1960,1580,1000,2460,490,1770]
        for line, amount in zip(result['lines'], amounts):
            self.assertEqual(line['quantity'] * line['purchase_rate'], amount)
            self.assertTrue(ocr._has_tamil(line['description']))
            self.assertEqual(line['gst_percent'], 0)
        self.assertNotIn('2000', result['lines'][7]['description'])
        self.assertNotIn('8w', result['lines'][8]['description'])
        self.assertNotIn('noo', result['lines'][11]['description'])
        self.assertTrue(any('recovered' in warning for warning in result['warnings']))
        self.assertTrue(any('Tax percentage' in warning for warning in result['warnings']))

    def test_missing_quantity_unit_is_not_inferred_from_amount(self):
        tokens = [t for t in self.page['tokens'] if not (t['text'] == 'Bag' and 675 < t['yc'] < 685)]
        self.assertEqual(len(self.parse(tokens)['lines']), 12)

    def test_fractional_bag_quantity_is_not_inferred(self):
        tokens = copy.deepcopy(self.page['tokens'])
        next(t for t in tokens if t['text'] == '1,000.00' and t['xc'] > 1100)['text'] = '1,500.00'
        self.assertEqual(len(self.parse(tokens)['lines']), 12)

    def test_explicit_conflicting_quantity_is_not_replaced(self):
        tokens = copy.deepcopy(self.page['tokens'])
        next(t for t in tokens if t['text'] == '4' and t['xc'] > 1000)['text'] = '3'
        result = self.parse(tokens)
        self.assertEqual(len(result['lines']), 12)
        self.assertTrue(any('explicit quantity disagrees' in warning for warning in result['warnings']))

    def test_crop_without_serial_column_keeps_all_rows(self):
        tokens = [t for t in self.page['tokens'] if t['x0'] > 195]
        self.assertEqual(len(self.parse(tokens)['lines']), 13)


if __name__ == '__main__':
    unittest.main()
