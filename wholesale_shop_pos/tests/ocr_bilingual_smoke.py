"""Exercise actual bilingual OCR using a shaped, printed Tamil/English bill."""
import argparse
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import time

import pymupdf as fitz
from PIL import Image

SPEC = importlib.util.spec_from_file_location("bilingual_smoke_ocr", Path(__file__).parents[1] / "models" / "local_bill_ocr.py")
ocr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ocr)


def fixture():
    doc = fitz.open()
    page = doc.new_page(width=900, height=500)
    css = """
    @font-face {font-family: tamil; src: url(latha.ttf);}
    * {font-family: tamil; font-size: 16px;}
    table {width:100%; border-collapse:collapse;}
    td, th {border:1px solid #555; padding:10px; text-align:left;}
    """
    html = """<h2>முருகன் மளிகை</h2><p>Bill No: TM142<br>தேதி: 11/09/2026</p>
    <table><tr><th>Sl</th><th>பொருள் பெயர்</th><th>அளவு</th><th>விலை</th><th>வரி %</th><th>தொகை</th></tr>
    <tr><td>1</td><td>பொன்னி அரிசி</td><td>10</td><td>20.00</td><td>5</td><td>210.00</td></tr>
    <tr><td>2</td><td>BRU காபி</td><td>5</td><td>40.00</td><td>5</td><td>210.00</td></tr>
    <tr><td>3</td><td>சோப்பு Soap</td><td>12</td><td>10.00</td><td>18</td><td>141.60</td></tr>
    </table><p>மொத்தம் 561.60</p>"""
    page.insert_htmlbox(page.rect + (20,20,-20,-20), html, css=css, archive=fitz.Archive("C:/Windows/Fonts"))
    pix = page.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False)
    result = Image.frombytes("RGB", (pix.width,pix.height), pix.samples)
    doc.close()
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--rotation", type=int, default=0)
    parser.add_argument("--small", action="store_true")
    args=parser.parse_args()
    image=fixture()
    if args.small:
        image=image.resize((900,500))
    image=image.rotate(args.rotation, expand=True, fillcolor="white")
    buffer=BytesIO()
    image.save(buffer, format="PNG")
    started=time.monotonic()
    result, engine, _fingerprint=ocr.extract_bill(buffer.getvalue(),"image/png")
    print(json.dumps({"seconds":round(time.monotonic()-started,2),
                      "rotation":args.rotation, "small":args.small,
                      "bill":result["bill_number"], "date":result["bill_date"],
                      "lines":result["lines"],
                      "methods":[p["selected_method"] for p in result["_audit"]["pages"]],
                      "warnings":[p["warnings"] for p in result["_audit"]["pages"]]},
                     ensure_ascii=True),flush=True)
    assert len(result["lines"])==3, "Expected three printed product rows"
    assert [row["quantity"] for row in result["lines"]]==[10,5,12]
    assert [row["gst_percent"] for row in result["lines"]]==[5,5,18]
    assert all(ocr._has_tamil(row["description"]) for row in result["lines"])
    assert result["bill_number"] == "TM142"
    assert result["bill_date"] == "2026-09-11"
    assert result["lines"][1]["description"].startswith("BRU"), "Keep the English brand in the Tamil product name"
    assert not any(row["description"][0].isdigit() for row in result["lines"]), "Do not include serial numbers in names"


if __name__=="__main__":
    main()
