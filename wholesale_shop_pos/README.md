# Wholesale Shop POS Core - Odoo 19

### AI Chat

After upgrading the module, open **Wholesale Shop → AI Chat**. In **Settings →
Wholesale Shop → AI Chat**, an administrator must enter an OpenAI API key and
may change the model (default `gpt-4.1-mini`). An `OPENAI_API_KEY` environment
variable on the Odoo service also works and takes precedence. Installing the
module does not create an API key or an OpenAI subscription.

The chat can search and read business records through Odoo's normal access
rights. Each user sees only their own conversations. It cannot run SQL, modify
records, edit Python files, or restart the Odoo service. Code deployment still
uses the server's existing Git and service-management workflow.

### Linux server OCR setup

From the deployed addon directory, run:

```bash
sudo bash scripts/setup_server_ocr.sh
```

For KML Shop this uses the `kmlshop` service and `/opt/kmlshop/ocr-venv/bin/python3`.
It installs the isolated OCR versions used locally, downloads the Tamil/English
models, validates inference without a private bill, configures the systemd OCR
Python path and working directory, and restarts the service. It leaves Odoo's
own Python dependencies in its existing virtualenv. Override `SHOP_ODOO_SERVICE`
and `SHOP_PADDLE_PYTHON` for another deployment.

To check the OCR runtime without downloading models or restarting:

```bash
sudo -u SERVICE_USER /opt/kmlshop/ocr-venv/bin/python3 models/paddle_bill_worker.py --check
```

The final JSON must contain `"ready": true`. Bill audit pages show the engine
actually selected. Missing Paddle models now produce an explicit fallback warning.
Use the same vendor OCR template and crop configuration when comparing two hosts.

### Local PaddleOCR Tamil + English

PaddleOCR runs in a separate CPU subprocess, leaving Odoo's dependencies alone.
From the repository root, using Python 3.12:

```powershell
python -m pip install --target .ocr-runtime/paddle-deps paddlepaddle==3.2.0 paddleocr==3.7.0
python custom_addons/wholesale_shop_pos/models/paddle_bill_worker.py --setup
python custom_addons/wholesale_shop_pos/models/paddle_bill_worker.py "path/to/test-bill.jpg"
```

Setup downloads official detection and Tamil/English recognition models once;
it does not upload bills. Set `SHOP_PADDLE_PYTHON` to that Python executable.
Windows defaults to `.ocr-runtime/python312/python.exe`; Linux detects
`/opt/kmlshop/ocr-venv/bin/python3` or uses `SHOP_PADDLE_PYTHON`. Installed model
files enable inference without a manual test-image marker. Extraction tries
PaddleOCR without destructive grid removal, retaining Tesseract as its fallback. The existing page loop handles rotation. Paddle inference
has a bounded 120-second subprocess limit, with time reserved for fallback. Restart Odoo manually after Python updates.
Run the folder benchmark below: installation is not proof of extraction accuracy.

This is the first backend/core module for the wholesale shop POS project.

## Included now

- Purchase bill staging model (`shop.purchase.import`)
- Editable bill lines (`shop.purchase.import.line`)
- Original bill attachment + raw extraction/audit text
- Vendor-product mapping for future automatic matching
- Barcode/product auto matching
- Purchase price history
- Draft purchase order creation from reviewed bill imports
- Free quantity support
- Purchase GST/tax support using standard Odoo `account.tax`
- Product fields for box barcode, box quantity and low-stock threshold
- Mobile-friendly kanban/list/form views for bill review
- Duplicate vendor bill-number validation
- Reset-to-review flow while created PO is still unconfirmed
- Supplier bill image/PDF extraction into editable bill lines
- Automatic vendor, product, currency and purchase-tax matching after extraction
- Creation of real stock products from unmatched extracted bill lines
- Initial cost, barcode, HSN/SAC, box quantity and vendor pricelist creation
- True Odoo variant creation using `Attribute: Value` pairs
- OCR upload and review directly on draft Purchase Orders/RFQs
- One-click full receipt plus draft vendor-bill creation after review
- New Product Review screen for editing simple POS product names

## Important design choice

OCR/import does **not** directly change stock. It first creates an editable staging bill. After the user reviews and matches products, the module creates a normal Odoo Purchase Order. Standard Odoo stock/accounting flows remain the source of truth.

## Not implemented yet (next phases)

1. POS OWL UI changes for automatic quantity-based retail/wholesale pricing
2. Box-barcode scan => add `shop_box_qty` units
3. Mobile camera capture flow
4. Simplified stock dashboard and low-stock alerts
5. Supplier-specific extraction rules / learned aliases
6. GST/HSN helpers for Indian localization

## Installation

Copy `wholesale_shop_pos` into your Odoo 19 custom addons folder, restart Odoo, update Apps List, and install **Wholesale Shop POS Core**.

Example:

```bash
cp -r wholesale_shop_pos /opt/odoo/custom_addons/
sudo systemctl restart odoo
```

Then update the module from CLI if needed:

```bash
./odoo-bin -d YOUR_DB -u wholesale_shop_pos --stop-after-init
```

## Bill image/PDF extraction

Extraction is free and local. PaddleOCR reads Tamil and English when its local
models are installed, with Tesseract 5 `tessdata_best` as the fallback. Fitz / PyMuPDF handles
PDF text extraction and rendering; it is not itself a Tamil recognition model.
No API key is needed, and bills are not sent to an external extraction service.
This combination favors recognition quality on printed bills; no OCR engine can
guarantee correct prices, taxes or text in every blurred or cropped photograph.
For photographs containing surrounding objects or other pages, enable **Use Manual
Crop** on the Crop Bill tab and drag a rectangle around the invoice before extraction.
The uploaded original is preserved.

Install the OCR dependencies in the same Python environment that runs Odoo:

```bash
python -m pip install -r custom_addons/wholesale_shop_pos/requirements-ocr.txt
```

Install the Tesseract executable as well; the module invokes it locally through
Python using explicit arguments, including support for Windows paths with spaces.
See the [official Tesseract installation instructions](https://tesseract-ocr.github.io/tessdoc/Installation.html).
On Windows, the application also searches `C:\Program Files\Tesseract-OCR\tesseract.exe`.
For another location, set `SHOP_TESSERACT_CMD` to its full path in the Odoo service
environment.

Download the language files once, before extracting bills:

```bash
python custom_addons/wholesale_shop_pos/scripts/setup_local_ocr.py
python custom_addons/wholesale_shop_pos/scripts/setup_local_ocr.py --check
```

The setup script downloads pinned official English (`eng`), Tamil (`tam`) and
orientation (`osd`) models, verifies SHA-256 hashes, and installs them atomically
under `custom_addons/wholesale_shop_pos/.ocr-models/tessdata_best`. This first setup
needs internet access; extraction never downloads models. For a machine without
internet access, copy that complete folder from a prepared installation. Both
`eng.traineddata` and `tam.traineddata` are required; missing Tamil data produces a
setup message rather than silently extracting English only. To use an existing
model directory, set `SHOP_OCR_TESSDATA` (and use the same `--model-dir` for setup).

Restart Odoo manually after installing dependencies or changing its environment.
Use the same Python executable as the Odoo service for all commands above.

Upload a PDF, JPEG, PNG, WebP or GIF to a Bill Import and click **Extract Bill**.
The image pipeline handles page rotation, useful resizing, detected perspective
and skew, and contrast enhancement. It retains word coordinates for table
reconstruction and compares alternative readings when recognition is weak.
PDFs with embedded text are also parsed directly and compared with rendered OCR.
Tamil descriptions remain Unicode text in the editable review and name mapping.
Review quantities, free items, prices and tax rates before creating a purchase.
Unclear fields and conflicting arithmetic are listed in the bill's review notes.
Retries are limited per page/file, and long English recognition runs are split
into bounded batches. A warning marks a reading that reaches its retry limit.

Run the standalone OCR regressions without starting Odoo:

```bash
python custom_addons/wholesale_shop_pos/tests/test_local_ocr_backend.py
python custom_addons/wholesale_shop_pos/tests/test_local_ocr_pipeline.py
python custom_addons/wholesale_shop_pos/tests/test_tamil_bill_parser.py
```

For an actual photo (read only):

```bash
python custom_addons/wholesale_shop_pos/tests/ocr_samples_smoke.py path/to/bill.jpeg
```

To check new supplier layouts without touching Odoo or the original photos:

```powershell
python custom_addons/wholesale_shop_pos/tests/ocr_folder_benchmark.py "C:\Users\cajit\OneDrive\Documents\ExtractionBills" --output .ocr-runtime/bill-reports
```

Use `--pattern "*115228*"` to retest one sample. Reports contain private bill
text and extracted values; keep them local. A nonzero row count is not an
accuracy pass: compare quantities, prices, tax and row counts against the photo.
Column labels determine the general parser layout. Separate FREE and Dis%
columns and wrapped Net Rate headers are supported; a Cases column alone no
longer selects the SNK layout. Missing case conversion and large amount/quantity
disagreements are flagged instead of guessing. New layout fixes should include
a deterministic parser regression as well as a real-photo benchmark.

The normalized extraction stored for review looks like:

```json
{
  "vendor": "ABC Distributors",
  "bill_number": "INV-1001",
  "bill_date": "2026-09-04",
  "lines": [
    {
      "description": "PARLE MILK BIKIS 10",
      "vendor_product_code": "MB10",
      "barcode": "890...",
      "qty": 20,
      "free_qty": 2,
      "rate": 8.50,
      "discount_percent": 0,
      "gst_percent": 18
    }
  ]
}
```

That JSON populates `shop.purchase.import` and `shop.purchase.import.line`. The user must review vendor, product matches, quantities, rates and taxes before creating the Purchase Order.
