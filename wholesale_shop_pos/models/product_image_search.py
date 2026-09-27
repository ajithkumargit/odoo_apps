import base64
import re
import time
from io import BytesIO
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


def _thumbnail_url_allowed(url):
    """Only download provider-hosted thumbnails, never arbitrary search URLs."""
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        if parsed.hostname == 'serpapi.com':
            return bool(re.fullmatch(r'/searches/[a-f0-9]+/images/[a-f0-9]+\.(?:jpeg|jpg|png|webp)', parsed.path))
        return bool(re.fullmatch(r'encrypted-tbn[0-9]+\.gstatic\.com', parsed.hostname or '') and parsed.path == '/images')
    except ValueError:
        return False


class ProductImageSearch(models.Model):
    _inherit = 'product.template'

    def _check_shop_image_search_access(self):
        if not self.env.user.has_group('base.group_user'):
            raise AccessError(_('Only internal product editors can search product images.'))
        self.check_access('write')

    @api.model
    def shop_image_search_available(self):
        self._check_shop_image_search_access()
        return bool(self.env['ir.config_parameter'].sudo().get_param('wholesale_shop_pos.serpapi_key'))

    @api.model
    def shop_search_product_images(self, query):
        self._check_shop_image_search_access()
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise UserError(_('Enter a product name of up to 200 characters.'))
        key = self.env['ir.config_parameter'].sudo().get_param('wholesale_shop_pos.serpapi_key')
        if not key:
            raise UserError(_('Automatic image results need a SerpAPI key in Settings → Wholesale Shop. You can also open Google Images and upload a chosen photo.'))
        try:
            with requests.get('https://serpapi.com/search.json', params={
                'engine': 'google_images', 'q': query.strip(), 'api_key': key,
                'safe': 'active', 'gl': 'in', 'hl': 'en',
            }, timeout=(5, 20), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise UserError(_('Image search is unavailable. Check the SerpAPI key and search quota in Settings.'))
                payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise UserError(_('Image search could not connect. Please try again.')) from error
        if not isinstance(payload, dict) or payload.get('error'):
            raise UserError(_('Image search is unavailable. Check the SerpAPI key and search quota in Settings.'))
        results, seen = [], set()
        for item in payload.get('images_results', []):
            url = item.get('thumbnail') if isinstance(item, dict) else None
            if not _thumbnail_url_allowed(url) or url in seen:
                continue
            seen.add(url)
            results.append({'url': url, 'title': str(item.get('title') or query)[:200]})
            if len(results) == 5:
                break
        return results

    @api.model
    def shop_fetch_product_image(self, url):
        self._check_shop_image_search_access()
        if not _thumbnail_url_allowed(url):
            raise UserError(_('Choose an image from the search results.'))
        limit = 5 * 1024 * 1024
        content = bytearray()
        started = time.monotonic()
        try:
            with requests.get(url, timeout=(5, 15), stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise UserError(_('This photo is unavailable. Select another image.'))
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > limit or time.monotonic() - started > 20:
                        raise UserError(_('This photo is too large or slow to download. Select another image.'))
            with Image.open(BytesIO(content)) as image:
                if image.width * image.height > 20_000_000:
                    raise UserError(_('This photo is too large. Select another image.'))
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1920, 1920))
                output = BytesIO()
                image.convert('RGBA').save(output, format='PNG')
            return base64.b64encode(output.getvalue()).decode('ascii')
        except (requests.RequestException, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
            raise UserError(_('This photo could not be read. Select another image.')) from error


class ProductImageSearchSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    shop_serpapi_key = fields.Char(
        string='SerpAPI Key', config_parameter='wholesale_shop_pos.serpapi_key',
        groups='base.group_system',
    )
