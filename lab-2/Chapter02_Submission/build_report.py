"""
Builds Chapter02_Activities_Report.docx from the four activity scripts.

For each activity it embeds:  objective -> full Python source -> live terminal
output (captured by actually running the script) -> the screenshots found in
the Screenshots/ folder.

Drop your screenshots into Screenshots/ using the filenames listed in
SCREENSHOTS below, then re-run:   python build_report.py
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "Screenshots"

ACTIVITIES = [
    {
        "key": "2.1",
        "title": "Activity 2.1 - Environment Setup Verification",
        "folder": "Activity_2.1",
        "script": "activity_2_1_setup_verification.py",
        "json": "activity_2_1_environment_report.json",
        "objective": (
            "Verify every component required by the MongoDB labs before writing any "
            "application code: the Python interpreter, the PyMongo driver, a reachable "
            "local MongoDB, a reachable MongoDB Atlas cluster, the Docker engine, and the "
            "optional packages used by later chapters. The checklist is then written to "
            "MongoDB (chapter02.setup_checks) and exported to JSON."),
        "concepts": [
            "MongoClient(uri, serverSelectionTimeoutMS=...) - fail fast instead of hanging.",
            "client.admin.command(\"ping\") - the cheapest proof a deployment is alive.",
            "client.server_info() - returns the buildInfo document (version, git hash, "
            "storage engines, modules).",
            "Most first-lab failures are environmental (driver missing, server not running, "
            "IP not allow-listed) - not MongoDB bugs.",
        ],
    },
    {
        "key": "2.1-TIY",
        "title": "Activity 2.1 - Try It Yourself: Comparing Two Deployments with server_info()",
        "folder": "Activity_2.1_Try_It_Yourself",
        "script": "activity_2_1_try_it_yourself.py",
        "json": "activity_2_1_server_comparison.json",
        "objective": (
            "\"Try running both Atlas and Docker MongoDB simultaneously. Write a script that "
            "connects to both and compares their server version information using the "
            "server_info() command.\"\n\n"
            "The script holds TWO MongoClient connections open at the same time - one to the "
            "self-managed local MongoDB and one to managed MongoDB Atlas - calls server_info() "
            "on each, and prints a side-by-side comparison. The local deployment is "
            "auto-detected and labelled DOCKER or NATIVE."),
        "concepts": [
            "PyMongo clients are independent connection pools - several can be open at once.",
            "server_info() differences that matter: version, modules (Atlas reports "
            "\"enterprise\"), storage engines, and maxBsonObjectSize.",
            "hello / serverStatus reveal topology: a local mongod is a standalone, while an "
            "Atlas cluster is a 3-node replica set.",
            "Managed (DBaaS) deployments restrict admin commands - that restriction is itself "
            "the observable difference between self-managed and managed MongoDB.",
        ],
    },
    {
        "key": "2.2",
        "title": "Activity 2.2 - Bookstore CRUD Operations",
        "folder": "Activity_2.2",
        "script": "activity_2_2_bookstore_crud.py",
        "json": "activity_2_2_books.json",
        "objective": (
            "Perform the full Create / Read / Update / Delete cycle against a "
            "bookstore.books collection using PyMongo. Each book document embeds its "
            "publisher as a nested object and carries a tags array, previewing the "
            "embedding pattern from Chapter 3."),
        "concepts": [
            "Create: insert_many(list_of_dicts) - documents are plain Python dicts; there is "
            "no schema-definition step.",
            "Read: find(filter, projection) - the first argument selects which documents, the "
            "second which fields.",
            "Query operators: $gt, $gte, $lte on numbers; dot notation "
            "(\"publisher.name\") reaches into nested objects; a bare value against an array "
            "field (\"tags\": \"AI\") matches any element.",
            "Update: update_many({}, {\"$mul\": {\"price\": 1.10}}) - $mul scales the field "
            "server-side, no read-modify-write round trip.",
            "Delete: delete_one(filter) removes the first match; delete_many removes all.",
        ],
    },
    {
        "key": "2.2-TIY",
        "title": "Activity 2.2 - Try It Yourself: 10% Price Increase for Books Published Before 2010",
        "folder": "Activity_2.2_Try_It_Yourself",
        "script": "activity_2_2_try_it_yourself.py",
        "json": "activity_2_2_try_it_yourself_books.json",
        "objective": (
            "\"Add an update operation to your script that increases the price of all books "
            "published before 2010 by 10 percent. Verify the update in Compass.\"\n\n"
            "Eight books are seeded - four published before 2010 and four from 2010 onwards - "
            "so that the filter can be proved selective. The script snapshots every price, "
            "runs the targeted update, and verifies that each pre-2010 price rose by exactly "
            "10% while every 2010+ price is untouched."),
        "concepts": [
            "The operation: col.update_many({\"year\": {\"$lt\": 2010}}, "
            "{\"$mul\": {\"price\": 1.10}})",
            "$lt restricts the update to matching documents - contrast with Activity 2.2, "
            "where an empty filter {} updated every book.",
            "matched_count vs modified_count: matched is how many the filter selected, "
            "modified is how many actually changed on disk.",
            "$mul is atomic per document - correct even under concurrent writers, unlike "
            "fetching a price into Python and writing it back.",
            "Verification in Compass: filter {\"year\": {\"$lt\": 2010}} shows the four raised "
            "prices; {\"year\": {\"$gte\": 2010}} shows the four unchanged ones.",
        ],
    },
]


# ---------------------------------------------------------------- cover page --
# Fill these in once; the cover page is regenerated from here on every build,
# so it survives re-running this script (hand edits to the .docx do not).
STUDENT = {
    "Name": "Jovit Chawla",
    "USN":  "24BTRCL089",
    "Date": "31/08/2026",
}

# filename -> (activity key, caption, required?)
SCREENSHOTS = {
    "2.1_terminal_output.png": ("2.1", "Activity 2.1 script running in the terminal - full environment checklist.", True),
    "2.1_mongodb_running.png": ("2.1", "Proof the MongoDB server is running (mongosh session or the service status).", True),
    "2.1_compass_setup_checks.png": ("2.1", "MongoDB Compass: chapter02 > setup_checks - the checklist document written by the script.", True),

    "2.1-TIY_terminal_comparison.png": ("2.1-TIY", "Terminal: both connections open simultaneously and the side-by-side server_info() table.", True),
    "2.1-TIY_compass_two_connections.png": ("2.1-TIY", "Compass with BOTH connections active in the sidebar - localhost and the Atlas cluster.", True),
    "2.1-TIY_atlas_cluster.png": ("2.1-TIY", "MongoDB Atlas web console showing the cluster and its MongoDB version.", True),
    "2.1-TIY_compass_server_comparison.png": ("2.1-TIY", "Compass: chapter02 > server_comparison - the stored comparison document, expanded.", False),

    "2.2_terminal_output.png": ("2.2", "Activity 2.2 script running - the full CREATE / READ / UPDATE / DELETE sequence.", True),
    "2.2_compass_books_collection.png": ("2.2", "Compass: bookstore > books - the four remaining documents after the delete.", True),
    "2.2_compass_nested_document.png": ("2.2", "Compass: a single book document expanded, showing the nested publisher object and the tags array.", True),
    "2.2_compass_filter_year.png": ("2.2", "Compass: filter {\"year\": {\"$gt\": 2020}} applied to bookstore > books.", False),

    "2.2-TIY_terminal_output.png": ("2.2-TIY", "Terminal: before / after price tables and the verification block showing exactly 10.00%.", True),
    "2.2-TIY_compass_before.png": ("2.2-TIY", "Compass BEFORE the update - original prices in bookstore > books_price_update.", False),
    "2.2-TIY_compass_updated_pre2010.png": ("2.2-TIY", "Compass AFTER the update, filter {\"year\": {\"$lt\": 2010}} - the four raised prices.", True),
    "2.2-TIY_compass_unchanged_post2010.png": ("2.2-TIY", "Compass, filter {\"year\": {\"$gte\": 2010}} - the four books left untouched.", True),
    "2.2-TIY_compass_document_detail.png": ("2.2-TIY", "Compass: one updated document expanded (e.g. Clean Code, price 43.989...).", False),
}


# ------------------------------------------------------------------ helpers --
def shade(paragraph, hex_fill):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hex_fill)
    paragraph.paragraph_format.element.get_or_add_pPr().append(el)


def mono_block(doc, text, fill="F4F4F4", size=7.5):
    for line in text.rstrip().splitlines() or [""]:
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.0
        pf.left_indent = Inches(0.12)
        run = p.add_run(line if line.strip() else " ")
        run.font.name = "Consolas"
        run.font.size = Pt(size)
        shade(p, fill)
    doc.add_paragraph()


def run_script(folder, script):
    """Execute an activity script and capture its terminal output."""
    path = ROOT / folder / script
    print(f"  running {folder}/{script} ...")
    try:
        proc = subprocess.run([sys.executable, script], cwd=path.parent,
                              capture_output=True, text=True, timeout=180)
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
        return out.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "(script timed out after 180s)"


def add_screenshots(doc, key):
    wanted = [(fn, cap, req) for fn, (k, cap, req) in SCREENSHOTS.items() if k == key]
    if not wanted:
        return
    doc.add_heading("Screenshots", level=2)
    for filename, caption, required in wanted:
        img = SHOTS / filename
        if img.exists():
            doc.add_picture(str(img), width=Inches(6.3))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap = doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = cap.add_run(caption)
            run.italic = True
            run.font.size = Pt(9)
        else:
            p = doc.add_paragraph()
            tag = "REQUIRED" if required else "optional"
            run = p.add_run(f"[ {tag} screenshot missing:  Screenshots/{filename} ]\n{caption}")
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0xB0, 0x00, 0x00) if required else RGBColor(0x80, 0x80, 0x80)
            shade(p, "FFF3F3" if required else "F7F7F7")
        doc.add_paragraph()


# --------------------------------------------------------------------- main --
def main():
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    # ---- title page
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run("NoSQL Databases using MongoDB")
    r.bold = True
    r.font.size = Pt(22)

    s = doc.add_paragraph()
    s.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = s.add_run("Chapter 02 - Cloud Computing Primer and Environment Setup\n"
                  "Activities 2.1 and 2.2, including both \"Try It Yourself\" exercises")
    r.font.size = Pt(13)

    doc.add_paragraph()

    # ---- student details table
    rows = [(k, v) for k, v in STUDENT.items() if v]
    table = doc.add_table(rows=len(rows), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (key, value) in enumerate(rows):
        cells = table.rows[i].cells
        cells[0].width = Inches(1.8)
        cells[1].width = Inches(3.4)
        kr = cells[0].paragraphs[0].add_run(f"{key}")
        kr.bold = True
        kr.font.size = Pt(11)
        vr = cells[1].paragraphs[0].add_run(str(value))
        vr.font.size = Pt(11)
        if str(value).startswith("<") and str(value).endswith(">"):
            vr.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)

    doc.add_page_break()
    doc.add_heading("Contents of This Submission", level=2)
    for a in ACTIVITIES:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(f"{a['folder']}/  ").bold = True
        p.add_run(f"{a['script']}  +  {a['json']}")

    doc.add_page_break()

    # ---- one section per activity
    for i, a in enumerate(ACTIVITIES):
        doc.add_heading(a["title"], level=1)

        doc.add_heading("Objective", level=2)
        doc.add_paragraph(a["objective"])

        doc.add_heading("Key Concepts", level=2)
        for c in a["concepts"]:
            doc.add_paragraph(c, style="List Bullet")

        doc.add_heading(f"Python Script - {a['folder']}/{a['script']}", level=2)
        source = (ROOT / a["folder"] / a["script"]).read_text()
        mono_block(doc, source, fill="F4F4F4", size=7)

        doc.add_heading("Terminal Output", level=2)
        mono_block(doc, run_script(a["folder"], a["script"]), fill="EAF3EA", size=7)

        doc.add_heading(f"MongoDB Data File - {a['json']}", level=2)
        jpath = ROOT / a["folder"] / a["json"]
        if jpath.exists():
            text = jpath.read_text()
            preview = text if len(text) < 6000 else text[:6000] + "\n... (truncated - see the .json file in the submission)"
            mono_block(doc, preview, fill="F0F4FA", size=6.5)
        else:
            doc.add_paragraph(f"(not generated - run {a['script']} first)")

        add_screenshots(doc, a["key"])

        if i < len(ACTIVITIES) - 1:
            doc.add_page_break()

    out = ROOT / "Chapter02_Activities_Report.docx"
    doc.save(out)

    missing = [fn for fn, (_, _, req) in SCREENSHOTS.items()
               if req and not (SHOTS / fn).exists()]
    print(f"\n[OK] Wrote {out.name}")
    if missing:
        print(f"[!] {len(missing)} required screenshot(s) still missing:")
        for m in missing:
            print(f"      Screenshots/{m}")
        print("    Add them and re-run this script to rebuild the report.")


if __name__ == "__main__":
    main()
