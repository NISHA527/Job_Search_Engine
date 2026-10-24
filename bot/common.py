"""Shared pieces: the applicant profile, screening-question answers and a generic form filler."""
import random
import re
import time
import unicodedata

# Tags every visible form field inside `root` with data-jbot="<prefix><n>" and describes it.
COLLECT_FIELDS_JS = r"""
(root, prefix) => {
  const out = [];
  let n = 0;
  // Material-UI puts a zero-width space in every input's outline - not real text
  const textOf = (el) => (el ? (el.innerText || el.textContent || '') : '').replace(/[​-‍﻿]/g, '').trim();
  for (const el of root.querySelectorAll('input, select, textarea')) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(type)) continue;
    if (el.disabled) continue;
    const box = el.getBoundingClientRect();
    const hiddenOk = ['file', 'radio', 'checkbox'].includes(type);
    if (!hiddenOk && (box.width === 0 || box.height === 0)) continue;

    let label = el.labels && el.labels.length ? textOf(el.labels[0]) : '';
    if (!label) label = el.getAttribute('aria-label') || '';
    if (!label && el.getAttribute('aria-labelledby')) {
      label = el.getAttribute('aria-labelledby').split(/\s+/)
        .map(id => textOf(document.getElementById(id))).join(' ');
    }
    if (!label) label = el.placeholder || '';
    let group = '';
    const fs = el.closest('fieldset');
    if (fs) group = textOf(fs.querySelector('legend')) || textOf(fs).slice(0, 200);
    if (!group) {
      const rg = el.closest('[role=radiogroup]');
      if (rg) group = rg.getAttribute('aria-label') || '';
    }
    if (!label && !group) {
      // Material-UI forms (Kulfi) put "Current city *" in a plain div a few levels above the input -
      // climb until a box has some text, but stop before one that holds other fields too
      for (let box = el.parentElement, i = 0; box && i < 5 && !label; box = box.parentElement, i++) {
        // (multi-line MUI boxes keep a hidden aria-hidden twin of the textarea - don't count it)
        if (box.querySelectorAll('input:not([type=hidden]):not([aria-hidden=true]), select, textarea:not([aria-hidden=true])').length > 1) break;
        label = textOf(box).slice(0, 200);
      }
    }
    if (!label && el.name && !/^[0-9a-f-]{20,}$/i.test(el.name)) {
      label = el.name.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_\-\[\]]+/g, ' ');  // firstName -> "first Name"
    }
    const id = prefix + (n++);
    el.setAttribute('data-jbot', id);
    out.push({
      id, type, tag: el.tagName.toLowerCase(), elId: el.id || '', name: el.name || '',
      label: label.trim(), group: group.trim(), value: el.value || '', checked: !!el.checked,
      required: el.required || el.getAttribute('aria-required') === 'true',
      combo: el.getAttribute('role') === 'combobox' || !!el.getAttribute('aria-autocomplete'),
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) : [],
      selectedIndex: el.tagName === 'SELECT' ? el.selectedIndex : -1,
    });
  }
  return out;
}
"""

SKILL_QUESTION = re.compile(
    r"(experience|worked|work(ing)? knowledge|familiar(ity)?|hands-on|proficien\w*|expertise|exposure)\b"
    r".{0,60}?\b(with|in|on|using)\s+(?P<topic>.+)", re.I)
# free-text "explain / describe" questions and language / salary questions are not yes-no skill checks
NOT_A_SKILL_QUESTION = re.compile(r"^\s*(please )?(briefly )?(explain|describe|tell|share|list|elaborate)|english|"
                                  r"language|ctc|salary|notice|relocat|commut|(?<![a-z])shifts?(?![a-z])|"
                                  # "Have you or a close family member worked for KPMG in an audit function in the
                                  # last 2 years?" is a yes/no conflict check, not "years of audit experience"
                                  r"family|relative|spouse|have you (or .{0,30})?(ever )?(worked|been employed)", re.I)
GENERIC_TOPIC = re.compile(r"^\W*(the\s+)?(total|overall|relevant|work|professional|industry|field|years?|"
                           r"(it|software|tech(nology)?) industry|this (role|field|domain))\b", re.I)
PLACEHOLDER_OPTION =re.compile(r"^\s*(select|choose|--|please)", re.I)
# "I agree" / terms / privacy boxes are always ticked, required or not (Nisha asked for this)
ACKNOWLEDGE = re.compile(r"talent ?pool|future (roles|opportunities)|i have read|agree|acknowledge|i accept|accept the|i confirm|consent|certify|declare|privacy (notice|policy|information)|terms (and|&) conditions|terms of (use|service)", re.I)
# talent pool / future roles: Nisha said yes - only marketing-type opt-ins stay unticked
OPT_IN = re.compile(r"marketing|newsletter|sms|text message|whatsapp|job alerts|promotional", re.I)


def pause(low=0.8, high=2.2):
    time.sleep(random.uniform(low, high))


class _Keep(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class Profile:
    def __init__(self, cfg, root):
        self.p = cfg["profile"]
        docs = root / "documents"
        self.resume = docs / self.p.get("resume_file", "resume.pdf")
        self.photo = docs / self.p.get("photo_file", "photo.jpg")
        cover = docs / self.p.get("cover_letter_file", "cover_letter.txt")
        self.cover_template = cover.read_text(encoding="utf-8") if cover.exists() else ""
        self.rules = [(re.compile(r["match"], re.I), r["answer"]) for r in cfg.get("answers", [])]

    def check(self):
        if not self.resume.exists():
            print(f"WARNING: resume not found at {self.resume} - forms that need an upload will be skipped.")

    def cover_letter(self, job=None):
        job = job or {}
        return (self.cover_template
                .replace("{job_title}", job.get("title") or "open")
                .replace("{company}", job.get("company") or "your organization"))

    def answers(self, question, job=None):
        """Candidate answers for a screening question, best first.

        None = no rule matches. [] = a rule matches but its profile value is blank, meaning
        "never guess this one" (e.g. salary not filled in) - the field is left empty.
        """
        skill = self.skill_answer(question)
        if skill is not None:
            return skill
        for rx, ans in self.rules:
            if rx.search(question or ""):
                out = []
                for a in (ans if isinstance(ans, list) else [ans]):
                    a = str(a)
                    fields = dict(self.p, company=(job or {}).get("company") or "your company",
                                  job_title=(job or {}).get("title") or "this role")
                    a = self.cover_letter(job) if a == "{cover_letter}" else a.format_map(_Keep(fields))
                    if a and "{" not in a:
                        out.append(a)
                return out
        return None

    def skill_answer(self, question):
        """'Do you have experience with X?' / 'How many years in X?' answered from the resume skills list:
        Yes / years_experience when X is on the resume, No / 0 otherwise. None = not a skill question."""
        q = question or ""
        m = SKILL_QUESTION.search(q)
        if not m or NOT_A_SKILL_QUESTION.search(q):
            return None
        topic = m.group("topic")
        if GENERIC_TOPIC.match(topic):
            return None  # "experience in the industry", "work experience" - left to the normal rules
        known = any(re.search(r"(?<!\w)" + re.escape(s.lower()) + r"(?!\w)", topic.lower())
                    for s in self.p.get("skills") or [])
        if re.search(r"how many|years|number of|how long", q, re.I):
            return [str(self.p.get("years_experience", 1))] if known else ["0"]
        return ["Yes"] if known else ["No"]

    def guess(self, question, job=None, numeric=False):
        """Answer with sensible fallbacks: years of experience for numbers, 'Yes' for yes/no."""
        ans = self.answers(question, job)
        if ans:
            return ans[0]
        if ans == []:
            return None
        if numeric or re.search(r"years|how many|number of|rate yourself|scale", question or "", re.I):
            return str(self.p.get("years_experience", 1))
        return None

    def file_for(self, question):
        q = (question or "").lower()
        if re.search(r"photo|picture|image|selfie", q):
            return self.photo if self.photo.exists() else None
        if "cover" in q:
            return None  # cover letters are pasted as text instead
        return self.resume if self.resume.exists() else None


def _plain(text):
    """Lower-case and without accent marks - Workday spells it "Karnātaka", we write "Karnataka"."""
    text = unicodedata.normalize("NFKD", str(text or "")).lower().strip()
    return "".join(c for c in text if not unicodedata.combining(c))


def pick_option(options, wanted):
    """Choose the option that best matches one of the wanted answers (falls back to the first real option).

    wanted == [] means the answer is deliberately blank, so nothing is chosen.
    """
    real = [o for o in options if o and not PLACEHOLDER_OPTION.match(o)]
    if not real or wanted == []:
        return None
    low = [_plain(o) for o in real]
    for w in wanted or []:
        wl = _plain(w)
        if wl in low:
            return real[low.index(wl)]
        if re.fullmatch(r"\d+(\.\d+)?", wl) and _range_option(low, [wl]) is not None:
            return real[_range_option(low, [wl])]  # 1 -> "1-2 years", not "Less than 1 year"
        for i, o in enumerate(low):
            if wl and re.search(r"(?<!\w)" + re.escape(wl) + r"(?!\w)", o):  # also works for "+91"
                return real[i]
    in_range = _range_option(low, wanted or [])
    if in_range is not None:
        return real[in_range]
    if not wanted:
        for i, o in enumerate(low):
            if o in ("yes", "y"):
                return real[i]
    return real[0]


def _matching_option(texts, wanted):
    """Index of the option that really matches one of the wanted answers (no first-option fallback)."""
    low = [_plain(t) for t in texts]
    for w in wanted:
        wl = _plain(w)
        if wl in low:
            return low.index(wl)
        if re.fullmatch(r"\d+(\.\d+)?", wl) and _range_option(low, [wl]) is not None:
            return _range_option(low, [wl])
        for i, o in enumerate(low):
            if wl and re.search(r"(?<!\w)" + re.escape(wl) + r"(?!\w)", o):  # also works for "+91"
                return i
    return _range_option(low, wanted)


def _range_option(options, wanted):
    """For a number like 1 (years) or 5 (LPA), the option whose range holds it: "0-1 years", "4 to 6 LPA", "5+"."""
    for w in wanted:
        if not re.fullmatch(r"\s*\d+(\.\d+)?\s*", str(w)):
            continue
        n = float(w)
        for i, o in enumerate(options):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)", o)
            if m and float(m.group(1)) <= n <= float(m.group(2)):
                return i
            m = re.search(r"(\d+(?:\.\d+)?)\s*\+|more than\s*(\d+)|above\s*(\d+)", o)
            if m and n >= float(next(g for g in m.groups() if g)):
                return i
            m = re.search(r"(?:less than|below|under|<)\s*(\d+(?:\.\d+)?)", o)
            if m and n < float(m.group(1)):
                return i
    return None


def tick(box):
    """Tick a checkbox - also the hidden ones under a styled box (Oracle Recruiting / Pearson), which
    Playwright's check() can't click: then click the input itself so the site's script sees the change."""
    try:
        box.check(force=True, timeout=3000)
    except Exception:
        pass
    if not box.is_checked():
        box.evaluate("el => el.click()")


def choose_combo(page, loc, wanted):
    """Search-as-you-type dropdowns (Greenhouse, LinkedIn city...): click the option that matches.

    Typing "India" can list "British Indian Ocean Territory" first, so never just take the first option;
    if nothing matches, the field is left empty and the form won't be submitted.
    """
    # react-select (Greenhouse) options carry an id like "react-select-question_123-option-4" instead of a role
    options = page.locator("[role=option]:visible, [id^='react-select-'][id*='-option-']:visible, .select__option:visible")
    listbox = loc.get_attribute("aria-controls")
    if listbox:  # some sites (Ascendion) list plain <li>s without role=option inside the controlled list
        options = options.or_(page.locator(f"[id='{listbox}'] li:visible"))

    def pick():
        texts = [t.strip() for t in options.all_inner_texts()]
        i = _matching_option(texts, wanted)
        if i is None:
            return False
        options.nth(i).click()
        page.wait_for_timeout(500)
        return True

    loc.click()
    page.wait_for_timeout(800)
    if pick():  # plain dropdown: the list is already open
        return True
    for w in wanted:
        loc.fill(w)
        for _ in range(5):  # big lists (4000+ regions on Vialto) take a few seconds to filter
            page.wait_for_timeout(1000)
            if pick():
                return True
    loc.fill("")
    try:  # note what the list offers, so a rule can be written for it (e.g. a US-only State list)
        loc.click()
        page.wait_for_timeout(800)
        shown = [t.strip() for t in options.all_inner_texts()][:12]
        print(f"  no option matching {list(wanted)[:2]} - the list shows: {shown}", flush=True)
    except Exception:
        pass
    page.keyboard.press("Escape")
    return False


def _numeric(value):
    m = re.search(r"\d+(\.\d+)?", value or "")
    return m.group(0) if m else None


def fill_form(page, root, prof, job=None, skip_files=False):
    """Fill every empty field inside `root` (a Locator) using the profile. Best effort, never raises."""
    prefix = f"jb{int(time.time() * 1000)}_"
    try:
        fields = root.evaluate(COLLECT_FIELDS_JS, prefix)
    except Exception:
        return
    radio_groups = {}
    for f in fields:
        loc = page.locator(f'[data-jbot="{f["id"]}"]')
        if "dateSection" in f["elId"] or f["label"] in ("Month", "Day", "Year"):
            continue  # date pickers are filled as a whole date by the site handler, never part by part
        question = f"{f['group']} {f['label']}".strip()
        if f["label"] and f["type"] != "radio" and prof.answers(f["label"], job) is not None:
            question = f["label"]  # the field's own label first - a section title in front breaks "^country" rules
        t = f["type"]
        try:
            if t == "file":
                if skip_files:
                    continue
                path = prof.file_for(question)
                if path and not f["value"]:
                    loc.set_input_files(str(path))
                    pause(1.5, 3)
            elif t == "radio":
                radio_groups.setdefault(f["name"] or f["group"], []).append(f)
            elif t == "checkbox":
                agree = ACKNOWLEDGE.search(question) and not OPT_IN.search(question)
                if (f["required"] or agree) and not f["checked"]:
                    tick(loc)
            elif f["tag"] == "select":
                current = f["options"][f["selectedIndex"]] if f["selectedIndex"] >= 0 else ""
                if f["selectedIndex"] <= 0 or PLACEHOLDER_OPTION.match(current):
                    choice = pick_option(f["options"], prof.answers(question, job))
                    if choice:
                        loc.select_option(label=choice)
            else:
                if f["value"].strip():
                    continue
                value = prof.guess(question, job, numeric=(t == "number"))
                if value is None:
                    continue
                if t == "number" or re.search(r"numeric|decimal|whole number", question, re.I):
                    value = _numeric(value) or value
                if f["combo"]:
                    choose_combo(page, loc, prof.answers(question, job) or [value])
                else:
                    loc.fill(value)
            pause(0.2, 0.6)
        except Exception:
            continue

    for radios in radio_groups.values():
        if any(r["checked"] for r in radios):
            continue
        question = radios[0]["group"] or radios[0]["label"]
        labels = [r["label"] for r in radios]
        choice = pick_option(labels, prof.answers(question, job))
        if choice not in labels:
            continue
        target = radios[labels.index(choice)]
        try:
            if target["elId"] and page.locator(f'label[for="{target["elId"]}"]').count():
                page.locator(f'label[for="{target["elId"]}"]').first.click()
            else:
                page.locator(f'[data-jbot="{target["id"]}"]').check(force=True)
            pause(0.2, 0.6)
        except Exception:
            continue
