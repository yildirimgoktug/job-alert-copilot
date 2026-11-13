import zipfile

from jac.importers.cv_html import parse_cv_html
from jac.importers.cv_pdf import parse_cv_pdf
from jac.importers.draft import merge_into_profile
from jac.importers.linkedin_export import parse_linkedin_export
from jac.profile import Profile, dump_profile, load_profile
from jac.profile_check import check_profile
from pdfmaker import make_pdf

CV_HTML = """<html><head><title>Sam Rivers — CV</title></head><body>
<header><div class="top"><div class="contact">Leeds, UK &nbsp;·&nbsp; <a href="mailto:sam@example.com">sam@example.com</a><br>
<a href="https://github.com/samrivers">github.com/samrivers</a></div></div>
<div class="tagline">BSc Data Science student</div></header>
<h2>Profile</h2><p>Student who likes <b>data</b>.</p>
<h2>Education</h2>
<div class="entry"><div class="row"><div><span class="role">BSc Data Science</span>, University of Leeds</div><div class="date">2023 – 2026</div></div>
<div>First-class average.</div></div>
<h2>Experience</h2>
<div class="entry"><div class="row"><div><span class="role">Data Intern</span> · Acme Ltd</div><div class="date">Jun 2025 – Aug 2025</div></div>
<ul><li>Cleaned <b>3</b> datasets.</li><li>Built a dashboard (v1.2).</li></ul></div>
<h2>AI Projects</h2>
<div class="proj"><span class="role">Churn Model</span> <span class="meta">· individual · Python, scikit-learn · <a href="https://github.com/samrivers/churn">github.com/samrivers/churn</a></span><br>
Predicts churn. Reached 81% accuracy.</div>
<h2>Publications</h2>
<div class="proj">S. Rivers. <i>A Small Study.</i> Preprints.org, 2026.</div>
<h2>Skills</h2><table class="skills"><tr><td>Software</td><td>Python, SQL, Git</td></tr></table>
</body></html>"""


def test_html_cv_is_parsed_into_a_draft():
    profile = parse_cv_html(CV_HTML)
    assert profile.identity.name == "Sam Rivers"
    assert profile.identity.email == "sam@example.com"
    assert profile.identity.location == "Leeds, UK"
    assert profile.summary == "Student who likes data."
    kinds = {f.title: f.kind for f in profile.facts}
    assert kinds == {"BSc Data Science": "education", "Data Intern": "experience", "Churn Model": "project", "A Small Study.": "publication"}
    intern = next(f for f in profile.facts if f.title == "Data Intern")
    assert intern.org == "Acme Ltd" and intern.dates == "Jun 2025 – Aug 2025"
    assert [b.text for b in intern.bullets] == ["Cleaned 3 datasets.", "Built a dashboard (v1.2)."]
    churn = next(f for f in profile.facts if f.title == "Churn Model")
    assert churn.team == "individual" and "scikit-learn" in churn.tags
    assert [b.text for b in churn.bullets] == ["Predicts churn.", "Reached 81% accuracy."]
    assert profile.skills[0].items == ["Python", "SQL", "Git"]


def test_draft_round_trips_through_yaml(tmp_path):
    draft = parse_cv_html(CV_HTML)
    path = tmp_path / "profile.yaml"
    dump_profile(draft, path)
    assert load_profile(path) == draft


def test_merge_keeps_constraints_and_never_claim(tmp_path):
    path = tmp_path / "profile.yaml"
    existing = Profile(never_claim=["Rust"])
    existing.constraints.locations = ["Leeds"]
    dump_profile(existing, path)
    merged = merge_into_profile(parse_cv_html(CV_HTML), path)
    assert merged.never_claim == ["Rust"] and merged.constraints.locations == ["Leeds"]
    assert path.read_text(encoding="utf-8").startswith("# DRAFT")
    assert len(load_profile(path).facts) == 4


def test_linkedin_export_folder_and_zip(tmp_path):
    files = {
        "Profile.csv": "First Name,Last Name,Headline,Summary,Geo Location\nSam,Rivers,Data student,Likes data.,Leeds\n",
        "Positions.csv": "Company Name,Title,Description,Location,Started On,Finished On\nAcme,Data Intern,\"Cleaned data.\nBuilt a dashboard.\",Leeds,Jun 2025,Aug 2025\n",
        "Education.csv": "School Name,Start Date,End Date,Notes,Degree Name,Activities\nUniversity of Leeds,2023,2026,,BSc Data Science,\n",
        "Skills.csv": "Name\nPython\nSQL\n",
        "Projects.csv": "Title,Description,Url,Started On,Finished On\nChurn,Predicts churn.,https://example.com,Jan 2026,\n",
    }
    folder = tmp_path / "export"
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8-sig")
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, text in files.items():
            z.writestr(f"Basic_LinkedInDataExport/{name}", text)
    for source in (folder, archive):
        profile = parse_linkedin_export(source)
        assert profile.identity.name == "Sam Rivers" and profile.summary == "Likes data."
        assert {f.kind for f in profile.facts} == {"experience", "education", "project"}
        job = next(f for f in profile.facts if f.kind == "experience")
        assert [b.text for b in job.bullets] == ["Cleaned data.", "Built a dashboard."]
        assert job.dates == "Jun 2025 – Aug 2025"
        assert profile.skills[0].items == ["Python", "SQL"]


def test_pdf_cv_is_parsed_into_a_draft(tmp_path):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(make_pdf([
        "Sam Rivers", "sam@example.com | +44 7700 900123", "github.com/samrivers",
        "Experience",
        "Data Intern, Acme Ltd  Jun 2025 - Aug 2025",
        "- Cleaned 3 datasets for the sales team",
        "- Built a dashboard",
        "Education",
        "BSc Data Science, University of Leeds",
        "Skills",
        "Software: Python, SQL",
    ]))
    profile = parse_cv_pdf(pdf)
    assert profile.identity.name == "Sam Rivers" and profile.identity.email == "sam@example.com"
    assert profile.identity.links["github"].endswith("github.com/samrivers")
    intern = next(f for f in profile.facts if f.kind == "experience")
    assert intern.dates.startswith("Jun 2025") and len(intern.bullets) == 2
    assert any(f.kind == "education" for f in profile.facts)
    assert profile.skills[0].items == ["Python", "SQL"]


def test_profile_check_reports_gaps():
    understood, missing = check_profile(parse_cv_html(CV_HTML))
    assert any("4 " in line or "Facts" in line for line in understood)
    assert any("never_claim" in m for m in missing)
    assert any("languages" in m.lower() for m in missing)
    empty_understood, empty_missing = check_profile(Profile())
    assert any("fact bank is empty" in m for m in empty_missing)
