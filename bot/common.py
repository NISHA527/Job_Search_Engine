"""Shared pieces: the applicant profile, screening-question answers and a generic form filler."""
import random
import re
import time

# Tags every visible form field inside `root` with data-jbot="<prefix><n>" and describes it.
COLLECT_FIELDS_JS = r"""
(root, prefix) => {
  const out = [];
  let n = 0;
  const textOf = (el) => (el ? (el.innerText || el.textContent || '') : '').trim();
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
      const box = el.parentElement && el.parentElement.parentElement;
      label = textOf(box).slice(0, 200);
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

PLACEHOLDER_OPTION = re.compile(r"^\s*(select|choose|--|please)", re.I)


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
        for rx, ans in self.rules:
            if rx.search(question or ""):
                out = []
                for a in (ans if isinstance(ans, list) else [ans]):
                    a = str(a)
                    a = self.cover_letter(job) if a == "{cover_letter}" else a.format_map(_Keep(self.p))
                    if a and "{" not in a:
                        out.append(a)
                return out
        return None

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


def pick_option(options, wanted):
    """Choose the option that best matches one of the wanted answers (falls back to the first real option).

    wanted == [] means the answer is deliberately blank, so nothing is chosen.
    """
    real = [o for o in options if o and not PLACEHOLDER_OPTION.match(o)]
    if not real or wanted == []:
        return None
    low = [o.lower().strip() for o in real]
    for w in wanted or []:
        wl = w.lower().strip()
        if wl in low:
            return real[low.index(wl)]
        for i, o in enumerate(low):
            if wl and re.search(r"\b" + re.escape(wl) + r"\b", o):
                return real[i]
    if not wanted:
        for i, o in enumerate(low):
            if o in ("yes", "y"):
                return real[i]
    return real[0]


def _numeric(value):
    m = re.search(r"\d+(\.\d+)?", value or "")
    return m.group(0) if m else None


def fill_form(page, root, prof, job=None):
    """Fill every empty field inside `root` (a Locator) using the profile. Best effort, never raises."""
    prefix = f"jb{int(time.time() * 1000)}_"
    try:
        fields = root.evaluate(COLLECT_FIELDS_JS, prefix)
    except Exception:
        return
    radio_groups = {}
    for f in fields:
        loc = page.locator(f'[data-jbot="{f["id"]}"]')
        question = f"{f['group']} {f['label']}".strip()
        t = f["type"]
        try:
            if t == "file":
                path = prof.file_for(question)
                if path and not f["value"]:
                    loc.set_input_files(str(path))
                    pause(1.5, 3)
            elif t == "radio":
                radio_groups.setdefault(f["name"] or f["group"], []).append(f)
            elif t == "checkbox":
                if f["required"] and not f["checked"]:
                    loc.check(force=True)
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
                loc.fill(value)
                if f["combo"]:
                    page.wait_for_timeout(1200)
                    loc.press("ArrowDown")
                    loc.press("Enter")
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
