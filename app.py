{\rtf1\ansi\ansicpg1252\cocoartf2822
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 #!/usr/bin/env python3\
import argparse, tempfile, shutil, zipfile, os, re, xml.etree.ElementTree as ET\
from pathlib import Path\
\
class EpubPackage:\
    """In\uc0\u8209 memory representation of an unpacked EPUB."""\
    def __init__(self, root_path: Path):\
        self.root = root_path                # Temporary folder with extracted files\
        self.language: str | None = None\
        self.errors: list[str] = []          # Accumulated warnings / errors\
\
def detect_language(pkg: EpubPackage) -> None:\
    """Find <dc:language> (case\uc0\u8209 insensitive). If missing, record an error."""\
    opf_path = next(pkg.root.rglob('*.opf'), None)\
    if not opf_path:\
        pkg.errors.append('OPF file not found')\
        return\
    tree = ET.parse(opf_path)\
    ns = \{'dc': 'http://purl.org/dc/elements/1.1/'\}\
    lang_elem = tree.find('.//dc:language', ns)\
    if lang_elem is not None and re.fullmatch(r'^[a-z]\{2\}$', lang_elem.text.strip(), re.I):\
        pkg.language = lang_elem.text.lower()\
    else:\
        pkg.errors.append('Invalid or missing language tag')\
\
def normalise_xml_encoding(pkg: EpubPackage) -> None:\
    """Make XML declarations tolerant to single/double quotes and spaces."""\
    decl_pat = re.compile(\
        r'<\\?xml\\s+version=[\\'"]?1\\.0[\\'"]?\\s+encoding=[\\'"]?([^\\'"\\s]+)[\\'"]?.*\\?>',\
        flags=re.I)\
    for f in pkg.root.rglob('*.xml') + pkg.root.rglob('*.opf'):\
        txt = f.read_text(encoding='utf-8')\
        txt = decl_pat.sub(r'<?xml version="1.0" encoding="\\1"?>', txt)\
        f.write_text(txt, encoding='utf-8')\
\
def strip_stray_images(pkg: EpubPackage) -> None:\
    """Remove <img> tags that appear outside of a <body> element."""\
    img_pat = re.compile(r'<img[^>]*>', flags=re.I)\
    for f in pkg.root.rglob('*.xhtml') + pkg.root.rglob('*.html'):\
        txt = f.read_text(encoding='utf-8')\
        # Very simple heuristic: keep only images that are inside <body>\
        cleaned = re.sub(r'(?s)(?!<body.*?>).*?<img.*?>.*?(?=</body>)', '', txt)\
        f.write_text(cleaned, encoding='utf-8')\
\
def fix_body_id(pkg: EpubPackage) -> None:\
    """Ensure the <body> element has an id attribute."""\
    for f in pkg.root.rglob('*.xhtml') + pkg.root.rglob('*.html'):\
        txt = f.read_text(encoding='utf-8')\
        if '<body' in txt and not re.search(r'<body[^>]*\\sid=', txt):\
            txt = txt.replace('<body', '<body id="body"', 1)\
            f.write_text(txt, encoding='utf-8')\
\
def fix_invalid_hyperlinks(pkg: EpubPackage) -> None:\
    """Replace broken href=\\"#...\\" links with a safe placeholder."""\
    for f in pkg.root.rglob('*.xhtml') + pkg.root.rglob('*.html'):\
        txt = f.read_text(encoding='utf-8')\
        txt = re.sub(r'href="#[^"]+"', 'href="#"', txt)\
        f.write_text(txt, encoding='utf-8')\
\
def repackage_epub(pkg: EpubPackage, out_path: Path) -> None:\
    """Create a fresh ZIP (EPUB) from the temporary folder."""\
    with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED,\
                         allowZip64=True) as zf:\
        for root, _, files in os.walk(pkg.root):\
            for name in files:\
                full = Path(root) / name\
                arcname = full.relative_to(pkg.root)\
                zf.write(full, arcname.as_posix())\
\
def generate_output_name(src: Path, prefix: str = "fixed_") -> Path:\
    return src.parent / f"\{prefix\}\{src.name\}"\
\
def build_report(pkg: EpubPackage) -> str:\
    lines = ["--- Fix Summary ---"]\
    if pkg.language:\
        lines.append(f"Detected language: \{pkg.language\}")\
    if pkg.errors:\
        lines.append("Issues encountered:")\
        lines.extend(f"- \{e\}" for e in pkg.errors)\
    else:\
        lines.append("No critical issues.")\
    return "\\n".join(lines)\
\
def main() -> None:\
    parser = argparse.ArgumentParser(\
        description="Batch\uc0\u8209 process EPUBs for Kindle compatibility.")\
    parser.add_argument("files", nargs="+", type=Path,\
                        help="One or more .epub files")\
    args = parser.parse_args()\
\
    for epub_path in args.files:\
        if not epub_path.is_file():\
            print(f"\uc0\u10060  File not found: \{epub_path\}")\
            continue\
\
        with tempfile.TemporaryDirectory() as td:\
            work_dir = Path(td)\
            # 1\uc0\u65039 \u8419  Unpack the original EPUB\
            shutil.unpack_archive(str(epub_path), work_dir, 'zip')\
            pkg = EpubPackage(work_dir)\
\
            # 2\uc0\u65039 \u8419  Run each fixer (order matters)\
            detect_language(pkg)\
            normalise_xml_encoding(pkg)\
            strip_stray_images(pkg)\
            fix_body_id(pkg)\
            fix_invalid_hyperlinks(pkg)\
\
            # 3\uc0\u65039 \u8419  Write the cleaned EPUB\
            out_path = generate_output_name(epub_path)\
            repackage_epub(pkg, out_path)\
\
            # 4\uc0\u65039 \u8419  Show what happened\
            print(build_report(pkg))\
            print(f"\uc0\u9989  Fixed file saved as \u8594  \{out_path\}")\
\
if __name__ == "__main__":\
    main()}