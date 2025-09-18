#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import tempfile
import shutil
import zipfile
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import Flask, request, send_file, abort

# ----------------------------------------------------------------------
#  Core EPUB‑processing helpers
# ----------------------------------------------------------------------


class EpubPackage:
    """In‑memory representation of an unpacked EPUB."""

    def __init__(self, root_path: Path):
        self.root = root_path               # Temporary folder with extracted files
        self.language: str | None = None
        self.errors: list[str] = []         # Accumulated warnings / errors


def detect_language(pkg: EpubPackage) -> None:
    """Find <dc:language> (case‑insensitive). If missing, record an error."""
    opf_path = next(pkg.root.rglob('*.opf'), None)
    if not opf_path:
        pkg.errors.append('OPF file not found')
        return

    tree = ET.parse(opf_path)
    ns = {'dc': 'http://purl.org/dc/elements/1.1/'}
    lang_elem = tree.find('.//dc:language', ns)

    if lang_elem is not None and re.fullmatch(r'^[a-z]{2}$',
                                            lang_elem.text.strip(),
                                            re.I):
        pkg.language = lang_elem.text.lower()
    else:
        pkg.errors.append('Invalid or missing language tag')


def normalise_xml_encoding(pkg: EpubPackage) -> None:
    """Make XML declarations tolerant to single/double quotes and extra spaces."""
    # Matches: <?xml version="1.0" encoding="utf-8"?>
    decl_pat = re.compile(
        r'<\?xml\s+version=[\'"]?1\.0[\'"]?\s+encoding=[\'"]?([^\'"\s]+)[\'"]?.*\?>',
        flags=re.I,
    )

    for f in list(pkg.root.rglob('*.xml')) + list(pkg.root.rglob('*.opf')):
        txt = f.read_text(encoding='utf-8')
        txt = decl_pat.sub(r'<?xml version="1.0" encoding="\1"?>', txt)
        f.write_text(txt, encoding='utf-8')


def strip_stray_images(pkg: EpubPackage) -> None:
    """Remove <img> tags that appear outside of a <body> element."""
    for f in list(pkg.root.rglob('*.xhtml')) + list(pkg.root.rglob('*.html')):
        txt = f.read_text(encoding='utf-8')
        # Keep only images that are inside a <body>…</body> block.
        cleaned = re.sub(
            r'(?s)(?!<body.*?>).*?<img.*?>.*?(?=</body>)',
            '',
            txt,
        )
        f.write_text(cleaned, encoding='utf-8')


def fix_body_id(pkg: EpubPackage) -> None:
    """Ensure the <body> element has an id attribute."""
    for f in list(pkg.root.rglob('*.xhtml')) + list(pkg.root.rglob('*.html')):
        txt = f.read_text(encoding='utf-8')
        if '<body' in txt and not re.search(r'<body[^>]*\sid=', txt):
            txt = txt.replace('<body', '<body id="body"', 1)
            f.write_text(txt, encoding='utf-8')


def fix_invalid_hyperlinks(pkg: EpubPackage) -> None:
    """Replace broken href=\"#...\" links with a safe placeholder."""
    for f in list(pkg.root.rglob('*.xhtml')) + list(pkg.root.rglob('*.html')):
        txt = f.read_text(encoding='utf-8')
        txt = re.sub(r'href="#[^"]+"', 'href="#"', txt)
        f.write_text(txt, encoding='utf-8')


def repackage_epub(pkg: EpubPackage, out_path: Path) -> None:
    """Create a fresh ZIP (EPUB) from the temporary folder."""
    with zipfile.ZipFile(out_path,
                        'w',
                        compression=zipfile.ZIP_DEFLATED,
                        allowZip64=True) as zf:
        for root, _, files in os.walk(pkg.root):
            for name in files:
                full = Path(root) / name
                arcname = full.relative_to(pkg.root)
                zf.write(full, arcname.as_posix())


def generate_output_name(src: Path, prefix: str = "fixed_") -> Path:
    return src.parent / f"{prefix}{src.name}"


def build_report(pkg: EpubPackage) -> str:
    lines = ["--- Fix Summary ---"]
    if pkg.language:
        lines.append(f"Detected language: {pkg.language}")
    if pkg.errors:
        lines.append("Issues encountered:")
        lines.extend(f"- {e}" for e in pkg.errors)
    else:
        lines.append("No critical issues.")
    return "\n".join(lines)


# ----------------------------------------------------------------------
#  CLI entry‑point (useful for local testing)
# ----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch‑process EPUBs for Kindle compatibility."
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="One or more .epub files",
    )
    args = parser.parse_args()

    for epub_path in args.files:
        if not epub_path.is_file():
            print(f"❌ File not found: {epub_path}")
            continue

        with tempfile.TemporaryDirectory() as td:
            work_dir = Path(td)

            # 1️⃣ Unpack the original EPUB
            shutil.unpack_archive(str(epub_path), work_dir, "zip")
            pkg = EpubPackage(work_dir)

            # 2️⃣ Run each fixer (order matters)
            detect_language(pkg)
            normalise_xml_encoding(pkg)
            strip_stray_images(pkg)
            fix_body_id(pkg)
            fix_invalid_hyperlinks(pkg)

            # 3️⃣ Write the cleaned EPUB
            out_path = generate_output_name(epub_path)
            repackage_epub(pkg, out_path)

            # 4️⃣ Show what happened
            print(build_report(pkg))
            print(f"✅ Fixed file saved as → {out_path}")


# ----------------------------------------------------------------------
#  Flask web‑service (what Render actually runs)
# ----------------------------------------------------------------------
app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    """Simple HTML upload form."""
    return """
    <h2>Kindle‑Fixer (online)</h2>
    <form method="post" enctype="multipart/form-data" action="/process">
        <input type="file" name="epub" accept=".epub" required />
        <button type="submit">Fix EPUB</button>
    </form>
    """


@app.route("/process", methods=["POST"])
def process():
    """Handle the uploaded EPUB, run the fixers, and return a report + download."""
    uploaded = request.files.get("epub")
    if not uploaded:
        abort(400, "No file uploaded")

    # Save the uploaded file to a temporary location
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td)
        src_path = work_dir / uploaded.filename
        uploaded.save(src_path)

        # Unpack, fix, repack
        unpack_dir = work_dir / "unpacked"
        unpack_dir.mkdir()
        shutil.unpack_archive(str(src_path), unpack_dir, "zip")
        pkg = EpubPackage(unpack_dir)

        detect_language(pkg)
        normalise_xml_encoding(pkg)
        strip_stray_images(pkg)
        fix_body_id(pkg)
        fix_invalid_hyperlinks(pkg)

        out_path = generate_output_name(src_path)
        repackage_epub(pkg, out_path)

        # Store the output path so the download endpoint can fetch it
        request.environ["fixed_epub_path"] = str(out_path)

        report_html = f"""
        <h2>Fix Summary</h2>
        <pre>{build_report(pkg)}</pre>
        <a href="/download/{out_path.name}">Download fixed EPUB</a>
        """
        return report_html


@app.route("/download/<filename>", methods=["GET"])
def download(filename):
    """Serve the cleaned EPUB that was produced in the same request."""
    fixed_path = request.environ.get("fixed_epub_path")
    if not fixed_path or Path(fixed_path).name != filename:
        abort(404, "File not found")
    return send_file(
        fixed_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/epub+zip",
    )


# ----------------------------------------------------------------------
#  Entry‑point for `python app.py` (local testing)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Render uses port 10000 internally; we run on 8080 locally.
    app.run(host="0.0.0.0", port=8080, debug=False)
