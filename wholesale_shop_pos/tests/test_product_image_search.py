import base64
from io import BytesIO
from unittest.mock import MagicMock, patch

from PIL import Image
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase
from ..models import product_image_search as search


class TestProductImageSearch(TransactionCase):
    def setUp(self):
        super().setUp()
        self.products = self.env['product.template']
        self.env['ir.config_parameter'].sudo().set_param('wholesale_shop_pos.serpapi_key', '')

    def response(self, payload=None, content=b'', status=200):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.json.return_value = payload
        response.iter_content.return_value = [content]
        return response

    def test_no_key_and_invalid_query_do_not_call_provider(self):
        with patch.object(search.requests, 'get') as get:
            self.assertFalse(self.products.shop_image_search_available())
            for query in ('Coffee', '', False, 'a' * 201):
                with self.assertRaises(UserError):
                    self.products.shop_search_product_images(query)
            get.assert_not_called()

    def test_search_returns_five_unique_allowed_thumbnails(self):
        self.env['ir.config_parameter'].sudo().set_param('wholesale_shop_pos.serpapi_key', 'test-key')
        items = [{'thumbnail': 'http://127.0.0.1/private'}]
        items += [{'thumbnail': 'https://encrypted-tbn0.gstatic.com/images?q=%d' % i, 'title': 'Coffee'} for i in range(8)]
        items.insert(2, items[1])
        with patch.object(search.requests, 'get', return_value=self.response({'images_results': items})) as get:
            result = self.products.shop_search_product_images('Coffee')
        self.assertEqual(len(result), 5)
        self.assertEqual(len({row['url'] for row in result}), 5)
        self.assertEqual(get.call_args.kwargs['params']['engine'], 'google_images')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_download_rejects_arbitrary_urls_and_redirects(self):
        unsafe = ['http://127.0.0.1/image', 'https://serpapi.com.evil.test/searches/aa/images/bb.jpg',
                  'https://serpapi.com/search.json?api_key=x', 'https://user@encrypted-tbn0.gstatic.com/images',
                  'https://encrypted-tbn0.gstatic.com:8443/images', 'file:///secret']
        with patch.object(search.requests, 'get') as get:
            for url in unsafe:
                with self.assertRaises(UserError):
                    self.products.shop_fetch_product_image(url)
            get.assert_not_called()
        with patch.object(search.requests, 'get', return_value=self.response(status=302)):
            with self.assertRaises(UserError):
                self.products.shop_fetch_product_image('https://encrypted-tbn0.gstatic.com/images?q=one')

    def test_download_converts_image_and_rejects_invalid_content(self):
        image = BytesIO()
        Image.new('RGB', (64, 32), 'red').save(image, format='JPEG')
        url = 'https://serpapi.com/searches/aa/images/bb.jpeg'
        with patch.object(search.requests, 'get', return_value=self.response(content=image.getvalue())):
            data = self.products.shop_fetch_product_image(url)
        with Image.open(BytesIO(base64.b64decode(data))) as decoded:
            self.assertEqual(decoded.format, 'PNG')
            self.assertEqual(decoded.size, (64, 32))
        with patch.object(search.requests, 'get', return_value=self.response(content=b'<html>not image</html>')):
            with self.assertRaises(UserError):
                self.products.shop_fetch_product_image(url)

    def test_public_user_cannot_use_search(self):
        with patch.object(search.requests, 'get') as get:
            with self.assertRaises(AccessError):
                self.products.with_user(self.env.ref('base.public_user')).shop_image_search_available()
            get.assert_not_called()

    def test_provider_errors_do_not_expose_key(self):
        self.env['ir.config_parameter'].sudo().set_param('wholesale_shop_pos.serpapi_key', 'private-key')
        with patch.object(search.requests, 'get', return_value=self.response(status=401)):
            with self.assertRaises(UserError) as error:
                self.products.shop_search_product_images('Coffee')
        self.assertNotIn('private-key', str(error.exception))
