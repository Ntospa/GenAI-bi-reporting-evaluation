"""Generate the Apps Script that builds the raters' Google Form (RQ3 blind evaluation).

Input  : reports/blind/R*.txt + reports/blind/blind_key.csv (scenario of each neutral ID only),
         the scenario briefs (data + questions) copied verbatim from
         human_study/forms/create_analyst_google_form.gs, so raters see exactly what the authors saw.
Output : reports/blind/create_rater_google_form.gs   (private: contains the human texts;
         git-ignored together with the whole reports/blind/ folder).

The generated script never contains the true source of any report.

Guards (both can be bypassed only with --draft, for a private preview that must not be sent):
  * the blind set must be complete (5 x 3 human + 3 AI);
  * if rq3.rater_form.launch_not_before is set, today must be on or after it;
  * rq3.rater_form.info_sheet_url must be set.

Usage:  python -m human_study.build_rater_form [--draft]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

from src.pipeline import load_cfg, _p

DIM_LABELS = {
    "factual_accuracy": "Πραγματολογική ακρίβεια",
    "completeness": "Πληρότητα",
    "analytical_reasoning": "Αναλυτική τεκμηρίωση",
    "clarity_coherence": "Σαφήνεια και συνοχή",
    "decision_usefulness": "Χρησιμότητα για τη λήψη αποφάσεων",
    "trustworthiness": "Αξιοπιστία",
}
DIM_GUIDE = [
    ("Πραγματολογική ακρίβεια", "αν οι αριθμοί και οι διαπιστώσεις συμφωνούν με τα δεδομένα του σεναρίου.",
     "περιέχει σαφή αριθμητικά ή πραγματολογικά λάθη", "όλες οι τιμές και οι διαπιστώσεις είναι ορθές"),
    ("Πληρότητα", "αν καλύπτει τα ερωτήματα και τα ουσιώδη σημεία των δεδομένων.",
     "αγνοεί ερωτήματα ή κρίσιμα μεγέθη", "καλύπτει και τα τρία ερωτήματα με τα ουσιώδη μεγέθη"),
    ("Αναλυτική τεκμηρίωση", "αν συνδέει τα μεγέθη σε συνεκτική ανάλυση χωρίς αβάσιμα άλματα.",
     "απλή παράθεση αριθμών ή αυθαίρετες αιτιάσεις",
     "σαφής, τεκμηριωμένη ανάλυση με διάκριση παρατήρησης και ερμηνείας"),
    ("Σαφήνεια και συνοχή", "αν διαβάζεται εύκολα και έχει λογική ροή.",
     "δυσνόητη ή ασύνδετη", "ρέουσα, επαγγελματική, καλά δομημένη"),
    ("Χρησιμότητα για τη λήψη αποφάσεων",
     "αν βοηθά ένα στέλεχος να καταλάβει τι συμβαίνει και τι έχει σημασία.",
     "δεν προσφέρει αξιοποιήσιμη εικόνα", "δίνει άμεσα χρήσιμη εικόνα για απόφαση"),
    ("Αξιοπιστία", "πόσο θα εμπιστευόσασταν την αναφορά.",
     "δεν θα την εμπιστευόμουν", "θα την εμπιστευόμουν πλήρως"),
]
STATEMENTS = [
    "Έχω ενημερωθεί πλήρως για τη φύση και τους σκοπούς της έρευνας, όπως αναγράφονται στο έντυπο ενημέρωσης.",
    "Επιθυμώ να λάβω μέρος στην έρευνα.",
    "Κατανοώ ότι έχω το δικαίωμα να αποσυρθώ από την έρευνα σε οποιαδήποτε στιγμή, χωρίς να αποκαλύψω τους λόγους.",
    "Σε περίπτωση που επιθυμώ να αποσύρω τα αποτελέσματά μου, μετά το πέρας της εξέτασης, έχω χρονικό περιθώριο έως τέσσερεις (4) εβδομάδες.",
    "Κατανοώ ότι οι πληροφορίες που θα συλλεχθούν θα αποθηκευτούν σε ασφαλές μέρος, για όσο διάστημα κριθεί απαραίτητο από τους/τις Ερευνητές/Ερευνήτριες.",
    "Δηλώνω ότι δεν έχω συντάξει καμία από τις αναφορές που θα αξιολογήσω και ότι θα συμπληρώσω τη φόρμα ατομικά.",
    "Κατανοώ ότι όλες οι πληροφορίες που παρέχω κατά τη διάρκεια της έρευνας θα παραμείνουν εμπιστευτικές.",
]
SCENARIO_RE = re.compile(r"addScenario\(form, (\{.*?\n  \})\);", re.S)


def _js(s):
    return json.dumps(s, ensure_ascii=False)


def _briefs(analyst_gs, scenarios):
    with open(analyst_gs, encoding="utf-8") as f:
        blocks = SCENARIO_RE.findall(f.read())
    if len(blocks) != len(scenarios):
        sys.exit(f"ABORT: found {len(blocks)} scenario briefs in {analyst_gs}, "
                 f"expected {len(scenarios)}.")
    return dict(zip(scenarios, blocks))


def run(draft=False):
    cfg = load_cfg()
    rq3 = cfg.get("rq3", {})
    rf = rq3.get("rater_form", {})
    scenarios = rq3["human_scenarios"]
    dims = rq3["dimensions"]
    reports_dir = _p(cfg, "reports_dir")
    blind_dir = os.path.join(reports_dir, "blind")
    analyst_gs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forms",
                              "create_analyst_google_form.gs")

    with open(os.path.join(blind_dir, "blind_key.csv"), encoding="utf-8") as f:
        key = list(csv.DictReader(f))
    problems = []
    n_ai = sum(r["true_source"] == "ai" for r in key)
    n_hu = sum(r["true_source"] == "human" for r in key)
    if (n_ai, n_hu) != (len(scenarios), rq3.get("n_analysts", 5) * len(scenarios)):
        problems.append(f"blind set incomplete (AI={n_ai}, human={n_hu})")
    launch_s = (rf.get("launch_not_before") or "").strip()
    if launch_s and dt.date.today() < dt.date.fromisoformat(launch_s):
        problems.append(f"launch_not_before is {launch_s} (withdrawal window still open)")
    url = (rf.get("info_sheet_url") or "").strip()
    if not url:
        problems.append("rq3.rater_form.info_sheet_url is empty")
    if problems and not draft:
        sys.exit("ABORT: " + "; ".join(problems) + ". Use --draft only for a private preview.")

    briefs = _briefs(analyst_gs, scenarios)
    groups = []
    for sid in scenarios:
        reps = []
        for r in sorted((r for r in key if r["scenario"] == sid), key=lambda r: r["neutral_id"]):
            with open(os.path.join(blind_dir, f"{r['neutral_id']}.txt"), encoding="utf-8") as f:
                reps.append({"id": r["neutral_id"], "text": f.read().strip()})
        groups.append((sid, reps))

    guide = "\n\n".join(f"{i}. {n}: {w}\n1 = {lo}, 5 = {hi}."
                        for i, (n, w, lo, hi) in enumerate(DIM_GUIDE, 1))
    rows = [DIM_LABELS[d] for d in dims]
    duration = rf.get("duration", "30–45 λεπτά")
    banner = "ΠΡΟΣΧΕΔΙΟ ΓΙΑ ΕΛΕΓΧΟ, ΔΕΝ ΑΠΟΣΤΕΛΛΕΤΑΙ. " if problems else ""

    out = []
    out.append(f"""/**
 * Δημιουργία της Google Form αξιολόγησης (MSc Thesis, RQ3 τυφλή αξιολόγηση).
 * ΠΑΡΑΓΕΤΑΙ ΑΥΤΟΜΑΤΑ από src/build_rater_form.py. ΙΔΙΩΤΙΚΟ ΑΡΧΕΙΟ: περιέχει τα κείμενα των
 * αναλυτών και δεν δημοσιεύεται ποτέ.
 * {banner}Δημιουργήθηκε: {dt.date.today().isoformat()}
 *
 * ΧΡΗΣΗ: script.google.com -> Νέο έργο -> επικόλληση -> Run createRaterForm.
 * Μετά: κεφαλίδα (form_header.png) χειροκίνητα, και έλεγχος ότι "Συλλογή διευθύνσεων email"
 * και "Περιορισμός σε 1 απάντηση" είναι ΑΝΕΝΕΡΓΑ.
 */

function createRaterForm() {{
  var form = FormApp.create('Αξιολόγηση Επιχειρησιακών Αναφορών - Μεταπτυχιακή Έρευνα');
  form.setDescription(
    'Ευχαριστώ θερμά που αφιερώνετε λίγο από τον χρόνο σας.\\n\\n'
    + 'Η έρευνα αυτή εξετάζει κατά πόσο συστήματα τεχνητής νοημοσύνης μπορούν να παράγουν '
    + 'επιχειρησιακές αναφορές συγκρίσιμης ποιότητας με αναφορές που συντάσσουν άνθρωποι '
    + 'αναλυτές, στο πλαίσιο μεταπτυχιακής διατριβής στο πρόγραμμα σπουδών MSc in Artificial '
    + 'Intelligence του University of Essex. Θα σας ζητηθεί να διαβάσετε και να βαθμολογήσετε '
    + '18 σύντομες αναφορές. Η συμμετοχή σας είναι εθελοντική και ανώνυμη και διαρκεί περίπου '
    + {_js(duration)} + '. Τα δεδομένα θα χρησιμοποιηθούν αποκλειστικά για ακαδημαϊκούς σκοπούς.\\n\\n'
    + 'Το Έντυπο Ενημέρωσης είναι διαθέσιμο εδώ: ' + {_js(url or "[ΣΥΝΔΕΣΜΟΣ ΕΝΤΥΠΟΥ]")}
  );
  form.setCollectEmail(false);
  form.setLimitOneResponsePerUser(false);
  form.setAllowResponseEdits(false);
  form.setProgressBar(true);
  form.setShowLinkToRespondAgain(false);
  form.setConfirmationMessage(
    'Έτοιμο, η υποβολή καταγράφηκε. Σας ευχαριστώ πολύ για τον χρόνο και τη βοήθειά σας.\\n\\n'
    + 'Υπενθύμιση: διατηρείτε το δικαίωμα απόσυρσης των δεδομένων σας έως και τέσσερις (4) '
    + 'εβδομάδες από την υποβολή, με ένα email στον ερευνητή αναφέροντας τον κωδικό απόσυρσης '
    + 'που δηλώσατε. Κρατήστε τον σημειωμένο.'
  );

  var statements = {_js(STATEMENTS)};
  var decl = form.addCheckboxItem()
    .setTitle('Δηλώσεις συγκατάθεσης')
    .setHelpText('Παρακαλώ σημειώστε ότι συμφωνείτε με καθεμία από τις παρακάτω δηλώσεις.')
    .setChoiceValues(statements)
    .setRequired(true);
  decl.setValidation(FormApp.createCheckboxValidation()
    .setHelpText('Για να συμμετάσχετε, πρέπει να συμφωνείτε με όλες τις δηλώσεις.')
    .requireSelectExactly(statements.length).build());

  var codeItem = form.addTextItem()
    .setTitle('Κωδικός απόσυρσης (τον επιλέγετε εσείς)')
    .setHelpText(
      'Τρία λατινικά ΚΕΦΑΛΑΙΑ γράμματα και τρία ψηφία, για παράδειγμα ROM742. Προτείνεται: τα '
      + 'τρία πρώτα γράμματα μιας πόλης που σας αρέσει και ένας τριψήφιος αριθμός που θα '
      + 'θυμάστε.\\n\\n'
      + 'Ο κωδικός χρησιμεύει μόνο για να μπορέσετε να αποσύρετε τα δεδομένα σας εντός τεσσάρων '
      + '(4) εβδομάδων, στέλνοντάς τον με email στον ερευνητή. Δεν σας ταυτοποιεί και δεν '
      + 'συνδέεται με το όνομά σας.\\n\\n'
      + 'Μην χρησιμοποιήσετε όνομα, αρχικά, ημερομηνία γέννησης, τηλέφωνο, αριθμό μητρώου ή την '
      + 'πόλη στην οποία ζείτε.')
    .setRequired(true);
  codeItem.setValidation(FormApp.createTextValidation()
    .setHelpText('Μορφή: 3 λατινικά ΚΕΦΑΛΑΙΑ γράμματα και 3 ψηφία, π.χ. ROM742.')
    .requireTextMatchesPattern('^[A-Z]{{3}}[0-9]{{3}}$').build());

  form.addMultipleChoiceItem()
    .setTitle('Εμπειρία με επιχειρησιακές ή αναλυτικές αναφορές')
    .setChoiceValues(['Καμία', 'Περιορισμένη', 'Τακτική'])
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Κύριος επαγγελματικός προσανατολισμός')
    .setChoiceValues(['Δεδομένα και BI', 'Επιχειρησιακός ή διοικητικός', 'Άλλο'])
    .setRequired(true);

  form.addSectionHeaderItem()
    .setTitle('Πώς να βαθμολογήσετε')
    .setHelpText('\\u00A0\\n'
      + 'Οι αναφορές είναι οργανωμένες σε τρία σενάρια. Στην αρχή κάθε σεναρίου βλέπετε τα '
      + 'δεδομένα και τα ερωτήματα που δόθηκαν στους συντάκτες. Όλες οι αναφορές ενός σεναρίου '
      + 'απαντούν στα ίδια ερωτήματα με τα ίδια δεδομένα. Βαθμολογήστε κάθε αναφορά με βάση τα '
      + 'δεδομένα του σεναρίου, σε κλίμακα 1 έως 5 (1 = πολύ χαμηλό, 5 = πολύ υψηλό).\\n\\n'
      + {_js(guide)} + '\\n\\n'
      + 'Η εκτίμηση προέλευσης κάτω από κάθε αναφορά είναι προαιρετική. '
      + 'Συμπληρώστε τη φόρμα ατομικά, χωρίς να ζητήσετε τη γνώμη τρίτων. Προτείνεται να τη '
      + 'συμπληρώσετε σε μία συνεδρία, καθώς η πρόοδος μπορεί να μην αποθηκεύεται.');

  var dims = {_js(rows)};
""")
    for n, (sid, reps) in enumerate(groups, 1):
        out.append(f"  addScenarioBrief(form, {briefs[sid]});\n")
        for r in reps:
            out.append(f"  addReport(form, dims, {_js(r['id'])}, {_js(r['text'])});\n")
    out.append("""
  Logger.log('ΦΟΡΜΑ (επεξεργασία): ' + form.getEditUrl());
  Logger.log('ΦΟΡΜΑ (αποστολή στους αξιολογητές): ' + form.getPublishedUrl());
  return form.getPublishedUrl();
}

function addScenarioBrief(form, s) {
  form.addPageBreakItem().setTitle(s.page);
  var lines = s.data.map(function (row) { return '• ' + row[0] + ': ' + row[1]; }).join('\\n');
  var qs = s.questions.map(function (q, i) { return (i + 1) + '. ' + q; }).join('\\n');
  form.addSectionHeaderItem()
    .setTitle('Δεδομένα και ερωτήματα του σεναρίου')
    .setHelpText('\\u00A0\\nΠερίοδος: ' + s.period + '\\n\\n' + lines
      + '\\n\\nΕρωτήματα που δόθηκαν στους συντάκτες:\\n' + qs);
}

function addReport(form, dims, id, text) {
  form.addSectionHeaderItem().setTitle('Αναφορά ' + id).setHelpText(text);
  form.addGridItem()
    .setTitle('Αναφορά ' + id + ', βαθμολογία')
    .setHelpText('1 = πολύ χαμηλό, 5 = πολύ υψηλό')
    .setRows(dims)
    .setColumns(['1', '2', '3', '4', '5'])
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Αναφορά ' + id + ', εκτίμηση προέλευσης (προαιρετικό)')
    .setChoiceValues(['Άνθρωπος', 'Σύστημα τεχνητής νοημοσύνης', 'Δεν μπορώ να κρίνω'])
    .setRequired(false);
}
""")
    path = os.path.join(blind_dir, "create_rater_google_form.gs")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))
    print(f"[rater_form] wrote {path} ({sum(len(r) for _, r in groups)} reports, "
          f"{len(groups)} scenarios)")
    if problems:
        print("[rater_form] DRAFT ONLY, do not send: " + "; ".join(problems))
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--draft", action="store_true")
    run(ap.parse_args().draft)
