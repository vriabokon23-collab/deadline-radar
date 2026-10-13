"""Render the synthetic sample handbooks in this directory to PDF.

The prose lives here rather than only inside the PDFs so that changes to the
sample data show up in a reviewable diff. Regenerate with:

    .venv/bin/python sample_data/generate_handbooks.py

Module codes, staff names and dates are invented. Expected extraction results
are recorded in sample_data/README.md; change the two together.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

OUTPUT_DIR = Path(__file__).resolve().parent

Block = tuple[str, str | list[str]]

COMP0197: list[Block] = [
    ("title", "COMP0197 Applied Machine Learning Systems"),
    ("subtitle", "Module handbook 2026/27 &mdash; Term 1 &mdash; 15 credits"),
    ("h1", "1. Module overview"),
    ("p",
     "COMP0197 is about what happens to a machine learning model after the notebook is "
     "closed. We treat a trained model as one component inside a larger system and spend "
     "the term on the parts of that system which decide whether it survives contact with "
     "real users: data collection, evaluation design, deployment, monitoring, and the "
     "documentation that lets somebody else take the work over. The module is deliberately "
     "practical. You will spend more time reading other people's pipelines than deriving "
     "results on paper."),
    ("p",
     "The module is convened by Dr A. Okonkwo and taught jointly with Dr L. Marchetti. "
     "Office hours are listed on the Moodle page and are held in person in the Malet Place "
     "Engineering Building."),
    ("h1", "2. Learning outcomes"),
    ("p", "By the end of the module you should be able to:"),
    ("bullets", [
        "Design an evaluation that reflects how a model will actually be used, including "
        "the failure modes that matter to the people affected by it.",
        "Build and instrument a deployment pipeline, and explain the trade-offs you made "
        "in doing so.",
        "Document a model so that a reader outside your team can judge whether it is fit "
        "for their purpose.",
        "Read and critique a published machine learning system, not just a published "
        "result.",
    ]),
    ("h1", "3. Teaching pattern"),
    ("p",
     "Two hours of lectures and one two-hour laboratory session per week across Term 1. "
     "Laboratory sessions begin in the second week of term; the first week is lectures "
     "only. There is no teaching in the reading week commencing Monday 9 November 2026, "
     "although the laboratory machines remain available and project teams are expected to "
     "use that week for their own planning."),
    ("p",
     "Attendance at laboratory sessions is recorded but is not itself assessed. Students "
     "who miss more than three sessions will be contacted by the departmental tutor."),
    ("h1", "4. Reading and resources"),
    ("p",
     "There is no single textbook. A reading list is published on Moodle and updated "
     "through the term. Two or three papers are set for each lecture and you are expected "
     "to have read at least one of them before the session. Laboratory work uses Python "
     "3.11 on the department's teaching cluster; accounts are created automatically once "
     "your module registration is confirmed."),
    ("h1", "5. Assessment"),
    ("p",
     "The module is assessed entirely by coursework. There is no written examination. Four "
     "components contribute to the final mark and all four must be attempted for a mark to "
     "be returned. They are described below in the order they fall due."),
    ("h2", "5.1 Problem Set 1 (15%)"),
    ("p",
     "Six short exercises on evaluation design and metric selection, released in the "
     "fourth week of term. The problems are individual work and are marked out of sixty. "
     "There is no word limit; answers are expected to be concise, and most students write "
     "between a paragraph and a page per exercise. Submit by 4 December 2026, 16:00 "
     "through the Moodle submission point. Model solutions are released immediately after "
     "the deadline, so late submissions cannot be accepted under any circumstances."),
    ("h2", "5.2 Coursework 1: model card report (35%)"),
    ("p",
     "You will be given a trained model, its training data, and nothing else, and asked to "
     "produce a model card of no more than 2,500 words covering intended use, evaluation, "
     "limitations, and the questions you could not answer from the material provided. The "
     "last of those sections usually carries the strongest marks. This component is due in "
     "Week 5 and is submitted as a single PDF."),
    ("h2", "5.3 Group project: deployment case study (20%)"),
    ("p",
     "Working in teams of four, you will take one of the systems discussed in lectures and "
     "write a case study of how it is deployed, instrumented and monitored. The report "
     "should be around 4,000 words for the team as a whole, not per member. Teams are "
     "formed in the laboratory session of the third week and cannot be changed afterwards "
     "without the convenor's agreement. The report is due in UCL timetable week 14. Every "
     "member of a team receives the same mark unless the team asks for a contribution "
     "split, in which case the convenor will interview the team before marks are issued."),
    ("h2", "5.4 Showcase presentation (30%)"),
    ("p",
     "Each team presents its case study to the cohort and to two members of staff. "
     "Presentations are twelve minutes with eight minutes of questions, and the questions "
     "are marked. The showcase runs all day on Thursday 10 December 2026 in Roberts 110. "
     "The running order is drawn at random and published the preceding Friday, so be "
     "prepared to present at any point during the day."),
    ("h1", "6. Submission and late penalties"),
    ("p",
     "Unless stated otherwise above, coursework is submitted through Moodle and the "
     "departmental deadline convention of 16:00 applies. Work submitted up to two working "
     "days late is capped at the pass mark; work submitted after that receives zero. If "
     "something has gone wrong, use the extenuating circumstances process rather than "
     "emailing the convenor, and do so before the deadline wherever possible."),
    ("h1", "7. Feedback"),
    ("p",
     "Written feedback on Coursework 1 is returned within one calendar month of the "
     "deadline. Problem Set 1 is marked against a published scheme and individual feedback "
     "is brief by design. Presentation feedback is given verbally on the day and confirmed "
     "in writing afterwards."),
    ("h1", "8. Academic integrity"),
    ("p",
     "You may use generative AI tools for any component of this module provided you say "
     "where and how, in an appendix that does not count towards the word limit. Undeclared "
     "use is treated as a breach of academic integrity. Collaboration on the problem set "
     "is not permitted; collaboration within your team on the group project obviously is."),
]

STAT0023: list[Block] = [
    ("title", "STAT0023 Statistical Computing and Data Communication"),
    ("subtitle", "Student handbook 2026/27 &mdash; Term 1 &mdash; 15 credits"),
    ("h1", "Introduction"),
    ("p",
     "This module has two halves that are taught together rather than in sequence. The "
     "computing half covers the practical business of getting a statistical analysis to "
     "run, to run again a month later, and to run on somebody else's machine. The "
     "communication half covers what you then say about it. Students often arrive "
     "expecting the first half to be the hard one and leave having found the second "
     "harder."),
    ("p",
     "Convenor: Dr R. Fairhurst, Department of Statistical Science. Teaching assistants "
     "are listed on Moodle. Questions about the material belong on the module forum rather "
     "than in email, so that the answer is visible to everyone."),
    ("h1", "Prerequisites"),
    ("p",
     "A first course in statistical inference and some previous exposure to programming, "
     "in any language. Students without the latter should expect the first three weeks to "
     "be demanding and should attend the optional drop-in sessions. Module registration "
     "closes on 9 October 2026 and cannot be extended."),
    ("h1", "Teaching arrangements"),
    ("p",
     "One lecture and one computer practical each week of Term 1, with no teaching in the "
     "reading week commencing 9 November 2026. Practicals are not assessed in themselves, "
     "but the in-course assessments draw directly on them, and students who skip "
     "practicals reliably find the assessments harder than they need to be."),
    ("p",
     "All work for this module is done in R. The teaching version is R 4.4 with the "
     "tidyverse installed. If you work on your own machine, check your output against a "
     "cluster machine before you submit, because version differences have caused problems "
     "in previous years."),
    ("h1", "Assessment"),
    ("p",
     "The module is assessed by two pieces of in-course work and a take-home examination. "
     "The in-course work is designed to be done as the term progresses rather than in the "
     "week before each deadline."),
    ("h2", "In-course assessment 1 (30%)"),
    ("p",
     "A single analysis task with a supplied dataset, submitted as a reproducible R script "
     "and a short commentary. There is no word limit on the commentary, but anything "
     "beyond two pages suggests the analysis has not been thought through. The deadline is "
     "Term 1 Week 10. Note that this module numbers its weeks from the start of teaching, "
     "which is not the same as the numbering used on the central UCL timetable, so check "
     "the Moodle calendar if you are unsure which date that is."),
    ("h2", "In-course assessment 2: data communication report"),
    ("p",
     "You will take the analysis from the first assessment and rewrite it for a named "
     "non-specialist audience in 1,500 words, with at most three figures. The deadline "
     "falls two weeks after the reading week. The weighting of this component is confirmed "
     "on Moodle at the start of term once the cohort size is known; in recent years it has "
     "been between fifteen and twenty-five per cent."),
    ("h2", "Take-home examination (50%)"),
    ("p",
     "A three-hour take-home paper sat on Monday 11 January 2027 between 10:00 and 13:00, "
     "with a further thirty minutes allowed for submission. The paper is open book. You may "
     "use any written material and any software, but not another person. Past papers for "
     "the three preceding years are on Moodle, with solutions for the two oldest."),
    ("h1", "Submission"),
    ("p",
     "In-course work is submitted through the Moodle submission points, which close at "
     "16:00 on the day of the deadline. Submit early: the submission point does not accept "
     "work after it closes, and a failed upload at 15:58 is not an extenuating "
     "circumstance. Keep a copy of the submission receipt."),
    ("h1", "Late work and extenuating circumstances"),
    ("p",
     "The standard departmental penalties apply. If illness or another serious disruption "
     "affects your work, submit an extenuating circumstances claim with evidence through "
     "Portico. Claims are considered by a panel, not by the convenor, and the convenor "
     "cannot grant an extension informally however reasonable the request."),
    ("h1", "Feedback and marks"),
    ("p",
     "Marks and written feedback for in-course assessment 1 are returned in the final week "
     "of term. Feedback on the second assessment is returned early in Term 2. Examination "
     "marks are released by Student Records with the rest of your results and are "
     "provisional until the board of examiners has met."),
]

ANTH0045: list[Block] = [
    ("title", "ANTH0045 Digital Anthropology: Research Methods"),
    ("subtitle", "Course outline 2026/27 &mdash; Term 2 &mdash; 15 credits"),
    ("h1", "About this course"),
    ("p",
     "ANTH0045 is a methods course taught through practice. Rather than surveying digital "
     "anthropology as a literature, we spend the term doing a small piece of fieldwork in "
     "an online setting of your choosing and treat the difficulties that arise as the "
     "subject matter. Most of what you will learn comes from the gap between the method as "
     "described in the reading and the method as it behaves once real people are "
     "involved."),
    ("p",
     "The course is convened by Dr H. Lindqvist and is capped at twenty-four students. It "
     "is intended for second and third year undergraduates, and for masters students who "
     "have not previously done qualitative fieldwork. There is no quantitative "
     "component."),
    ("h1", "How the term is organised"),
    ("p",
     "We meet once a week for a two-hour seminar across Term 2, with no meeting in the "
     "reading week commencing 15 February 2027. The first four seminars are led by the "
     "convenor and cover research design, ethics, consent in online settings, and field "
     "note practice. From the fifth seminar onwards the sessions are led by students and "
     "organised around whatever the fieldwork has thrown up that week."),
    ("p",
     "Before any fieldwork begins you must have an approved ethics form on file. The form "
     "is discussed in the second seminar and must be approved before you make contact with "
     "anybody in your field site. This is not a formality, and the department will not "
     "accept work based on fieldwork conducted without approval."),
    ("h1", "What you will produce"),
    ("p",
     "The visible output of the course is a research essay, described below. Less visibly, "
     "you will produce a great deal of writing that is not the essay: field notes, reading "
     "notes, methodological asides, and the record of decisions you made and later "
     "regretted. Seminar participation is not assessed directly, though the fieldwork "
     "notebook you keep across the term is, and it is handed in alongside the essay. The "
     "notebook is marked pass or fail rather than contributing a percentage, and a fail "
     "means a fail for the course however good the essay. Two thousand words of notes is "
     "typical; students who write considerably less usually find the essay harder, because "
     "the essay is largely an argument assembled out of the notebook."),
    ("p",
     "Keep the notebook in whatever form suits you, but keep it in one place and keep it "
     "legible to somebody other than yourself. A notebook that cannot be read cannot be "
     "marked."),
    ("h1", "Assessment"),
    ("p",
     "The research essay carries sixty per cent of the course mark and runs to 4,000 "
     "words, excluding references and any appendices. It should present a focused argument "
     "grounded in your own fieldwork rather than a general review of the literature. The "
     "essay must be submitted by 12 noon on Friday 5 March 2027 through the Moodle "
     "submission point for this course."),
    ("p",
     "The remaining forty per cent is awarded for the seminar presentation in which you "
     "take the group through your fieldwork in progress. Presentations run in the second "
     "half of term and your slot is allocated by the convenor in the first seminar, once "
     "we know how many students are taking the course and what their field sites are. The "
     "schedule is circulated by email and posted on Moodle. It is not fixed in advance of "
     "the first meeting, and has in previous years changed during the term as field sites "
     "fell through."),
    ("p",
     "A reading list sign-up for the student-led seminars opens in Week 2 and closes once "
     "every session has a presenter. Signing up is a scheduling exercise and carries no "
     "marks of its own, though the quality of the session you lead informs the "
     "presentation mark."),
    ("h1", "A note on the word limit"),
    ("p",
     "The word limit on the essay is a limit and not a target. Essays over the limit are "
     "penalised according to the faculty scheme. The notebook has no limit in either "
     "direction."),
    ("h1", "Ethics, consent and data"),
    ("p",
     "Online fieldwork makes consent genuinely difficult, and the course spends a full "
     "seminar on why. You are responsible for the data you collect, including deleting it "
     "when the course ends. Do not store field material in services your participants "
     "could plausibly access, and do not quote anybody in the essay in a way that would "
     "let a reader find them."),
    ("h1", "Support"),
    ("p",
     "The convenor holds office hours weekly through Term 2; the times are on Moodle. "
     "Fieldwork goes wrong for almost everybody at some point, and the earlier you say so "
     "the more can be done about it. If your field site collapses entirely, come and see "
     "the convenor rather than trying to rescue it alone."),
]

HANDBOOKS: list[tuple[str, list[Block]]] = [
    ("COMP0197_applied_machine_learning_systems.pdf", COMP0197),
    ("STAT0023_statistical_computing.pdf", STAT0023),
    ("ANTH0045_digital_anthropology.pdf", ANTH0045),
]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("hbTitle", parent=base["Title"], fontSize=17, leading=21,
                                spaceAfter=4),
        "subtitle": ParagraphStyle("hbSubtitle", parent=base["Normal"], fontSize=10.5,
                                   leading=14, textColor="#444444", alignment=1,
                                   spaceAfter=18),
        "h1": ParagraphStyle("hbH1", parent=base["Heading1"], fontSize=13, leading=16,
                             spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("hbH2", parent=base["Heading2"], fontSize=11, leading=14,
                             spaceBefore=10, spaceAfter=4),
        "p": ParagraphStyle("hbBody", parent=base["BodyText"], fontSize=10.5, leading=15,
                            alignment=TA_JUSTIFY, spaceAfter=8),
    }


def _flowables(blocks: list[Block], styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    out: list[Flowable] = []
    for kind, content in blocks:
        if isinstance(content, list):
            out.append(ListFlowable(
                [ListItem(Paragraph(item, styles["p"])) for item in content],
                bulletType="bullet", leftIndent=14, bulletFontSize=8,
            ))
            out.append(Spacer(1, 6))
        else:
            out.append(Paragraph(content, styles[kind]))
    return out


def build(output_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Write every handbook PDF, returning the paths written."""
    styles = _styles()
    written: list[Path] = []
    for filename, blocks in HANDBOOKS:
        path = output_dir / filename
        title = str(blocks[0][1]).replace("&mdash;", "-")
        doc = SimpleDocTemplate(
            str(path), pagesize=A4,
            leftMargin=2.2 * cm, rightMargin=2.2 * cm,
            topMargin=2 * cm, bottomMargin=2 * cm,
            title=title, author="Synthetic sample data for Deadline Radar",
        )
        doc.build(_flowables(blocks, styles))
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in build():
        print(f"wrote {written_path.name}")
