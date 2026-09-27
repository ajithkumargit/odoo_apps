# Product photo search

Open a product or variant and click the search icon below its photo. Enter a product name, brand and pack size.

Without an API key, **OK** opens Google Images in another tab. Download the photo you want, return to the popup and click **Upload Chosen Photo**. Save the product to persist the image and regenerate its thumbnails.

To show up to five selectable Google image thumbnails inside the popup, an administrator can enter a SerpAPI key under **Settings → Wholesale Shop → Product Photos** and save settings. Search requests use that account's quota. Keys stay on the server. See https://serpapi.com/google-images-api for the provider's setup and account details.

Clicking a result copies the provider's thumbnail into the product image field. It does not save unrelated product edits automatically. Existing upload and remove-photo controls remain available. Closing the popup without selecting a photo leaves the product unchanged.

After deploying this change, upgrade `wholesale_shop_pos` on the active database, restart Odoo and hard-refresh the browser. Restarting alone does not update the views or settings field.

Automated checks cover the search response, access checks, permitted image hosts, failed downloads, no-key fallback, selection, upload, and cancelled requests. Live API searches require a configured key; no key is included in the module.
