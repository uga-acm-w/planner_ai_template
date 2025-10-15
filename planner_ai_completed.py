# planner_ai.py
# Minimal Planner.AI — turns a project brief into a task plan + schedule (CSV + ICS).
# Requirements: google-genai, tkinter; reuses your config.toml key.
# No external date libs; pure stdlib scheduling.

import csv, json, re, time, uuid
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import Tk, Text, StringVar, END, filedialog, messagebox, ttk

from google import genai
from google.genai.errors import ServerError, ClientError
from config import config

# -------------------------
# Config & client
# -------------------------
MODEL = getattr(config, "gemini_model", "models/gemini-1.5-flash")
client = genai.Client(api_key=config.gemini_api_key)

OUTDIR = Path("data/outputs"); OUTDIR.mkdir(parents=True, exist_ok=True)

# -------------------------
# Helpers
# -------------------------
def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-") or "project"

def ai(prompt: str, retries=5, base=1.2) -> str:
    last=None
    for i in range(retries):
        try:
            r = client.models.generate_content(model=MODEL, contents=prompt)
            return (r.text or "").strip()
        except (ServerError, ClientError) as e:
            last=e
            time.sleep(base*(2**i))
    raise RuntimeError(f"Gemini failed after retries: {last}")

def today_local() -> datetime:
    # you’re in America/New_York per your setup; stdlib naive dates are fine for simple ICS
    return datetime.now()

def parse_due(date_str: str) -> datetime:
    return datetime.strptime(date_str.strip(), "%Y-%m-%d")

def workdays_between(start: datetime, end: datetime) -> int:
    d=0; cur=start
    while cur.date() <= end.date():
        if cur.weekday() < 5: d += 1
        cur += timedelta(days=1)
    return max(d, 1)

def distribute_hours(total_hours: float, start: datetime, end: datetime, hours_per_week: float):
    """Yield (date, hours_that_day) across weekdays, capped by hours_per_week/5 per day."""
    per_day = max(hours_per_week/5.0, 0.5)  # at least 0.5h/day to ensure progress
    remaining = float(total_hours)
    cur = start
    while remaining > 1e-6 and cur.date() <= end.date():
        if cur.weekday() < 5:
            h = min(per_day, remaining)
            yield cur.date(), h
            remaining -= h
        cur += timedelta(days=1)

def write_ics(events, path: Path, cal_name="Planner.AI"):
    # Minimal RFC5545 .ics
    def dtstamp(dt: datetime): return dt.strftime("%Y%m%dT%H%M%S")
    now = datetime.utcnow()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//Planner.AI//EN",
        f"X-WR-CALNAME:{cal_name}",
    ]
    for ev in events:
        uid = ev.get("uid", str(uuid.uuid4()))
        start_dt = ev["start"]
        end_dt = ev["end"]
        summary = ev.get("summary","Task")
        desc = ev.get("description","")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp(now)}Z",
            f"DTSTART:{dtstamp(start_dt)}",
            f"DTEND:{dtstamp(end_dt)}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{desc.replace('\n',' ')}",
            "END:VEVENT"
        ]
    lines.append("END:VCALENDAR")
    path.write_text("\n".join(lines), encoding="utf-8")

def parse_tasks_json(s: str):
    """
    Expect JSON:
    {
      "tasks":[
        {"name":"...", "why":"...", "hours": 3.5, "depends_on":["optional name"]},
        ...
      ],
      "assumptions":"..."
    }
    """
    # Try to extract JSON block even if model wrapped it in code fences.
    m = re.search(r"\{.*\}", s, flags=re.S)
    if not m:
        raise ValueError("No JSON found from model.")
    data = json.loads(m.group(0))
    tasks = data.get("tasks", [])
    for t in tasks:
        # normalize
        t["name"] = str(t.get("name","")).strip()[:120]
        t["why"] = str(t.get("why","")).strip()
        try:
            t["hours"] = float(t.get("hours", 1.0))
        except:
            t["hours"] = 1.0
        t["depends_on"] = [str(x) for x in t.get("depends_on", [])]
    return tasks, data.get("assumptions","")

def topological_sort(tasks):
    name_to_task = {t["name"]: t for t in tasks}
    indeg = {t["name"]: 0 for t in tasks}
    for t in tasks:
        for dep in t["depends_on"]:
            if dep in indeg:
                indeg[t["name"]] += 1
    q = [n for n,d in indeg.items() if d==0]
    order=[]
    while q:
        n = q.pop(0)
        order.append(name_to_task[n])
        for t in tasks:
            if n in t["depends_on"]:
                indeg[t["name"]] -= 1
                if indeg[t["name"]] == 0:
                    q.append(t["name"])
    # If cycle, just return original order
    return order if len(order)==len(tasks) else tasks

def schedule_tasks(tasks, start: datetime, due: datetime, hours_per_week: float):
    """Greedy forward schedule by topo-order across weekdays."""
    # Make a simple per-task window evenly across the total span
    span_days = max(workdays_between(start, due), 1)
    total_hours = sum(t["hours"] for t in tasks) or 1.0
    events=[]
    csv_rows=[]
    cur_start = start

    # Build a dict that tracks each task's earliest start after its deps finish
    finished_dates = {}

    for t in topological_sort(tasks):
        # Earliest start: max(current pointer, all deps' end)
        dep_end = max([finished_dates.get(dep, start) for dep in t["depends_on"]] or [start])
        task_start = max(cur_start, dep_end)
        # Allocate hours day-by-day
        blocks = list(distribute_hours(t["hours"], task_start, due, hours_per_week))
        if not blocks:
            # if we ran out of days, push same-day small block on due date
            blocks = [(due.date(), t["hours"])]
        first_day = None; last_day=None
        for d, h in blocks:
            # 10:00–(10:00+h) simple block
            st = datetime.combine(d, datetime.min.time()) + timedelta(hours=10)
            et = st + timedelta(hours=h)
            events.append({
                "start": st,
                "end": et,
                "summary": f"[{h:.1f}h] {t['name']}",
                "description": t["why"] or "Task"
            })
            if first_day is None: first_day = d
            last_day = d
        finished_dates[t["name"]] = datetime.combine(last_day, datetime.min.time()) + timedelta(hours=18)
        cur_start = finished_dates[t["name"]]
        csv_rows.append([t["name"], f"{t['hours']:.1f}", str(first_day), str(last_day), t["why"]])
    return events, csv_rows

def make_prompt(title, due_date, hours_per_week, brief_text):
    return f"""
You are a project planning assistant for a student project.

GOAL: Break the project into 5–10 concrete tasks (each 1–6 hours) that a busy student can do on weekdays.
CONSTRAINTS:
- Respect logical order: include "depends_on" by task names when needed.
- Keep total hours realistic.
- Prefer small, actionable tasks (e.g., "Collect 3 sources", "Draft intro", "Build first prototype button", "User test with 2 people").
- Output STRICT JSON only (no commentary, no markdown). Use this schema:

{{
  "tasks":[
    {{"name":"short action task", "why":"why this matters", "hours": 2.0, "depends_on": []}},
    ...
  ],
  "assumptions":"short bullet list or sentences about your assumptions"
}}

INPUTS:
- Project title: "{title}"
- Due date: {due_date} (YYYY-MM-DD)
- Available hours per week: {hours_per_week}
- Brief or guidelines:
\"\"\"{brief_text[:8000]}\"\"\"
"""

# -------------------------
# Tiny Tkinter UI
# -------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Planner.AI — Minimal")
        self.title_var = StringVar(value="Untitled Project")
        self.due_var = StringVar(value=(today_local()+timedelta(days=14)).strftime("%Y-%m-%d"))
        self.hpw_var = StringVar(value="8")
        self.brief = Text(root, height=12, wrap="word")

        frm = ttk.Frame(root, padding=12); frm.grid(sticky="nsew")
        root.columnconfigure(0, weight=1); root.rowconfigure(0, weight=1)
        for i in range(4): frm.columnconfigure(i, weight=1)

        ttk.Label(frm, text="Project Title").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.title_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=6)

        ttk.Label(frm, text="Due (YYYY-MM-DD)").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.due_var, width=14).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(frm, text="Hours/week").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm, textvariable=self.hpw_var, width=8).grid(row=1, column=3, sticky="w", padx=6)

        ttk.Button(frm, text="Load brief from file…", command=self.load_file).grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6,4))
        self.brief.grid(row=3, column=0, columnspan=4, sticky="nsew"); frm.rowconfigure(3, weight=1)

        btns = ttk.Frame(frm); btns.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8,0))
        for i in range(2): btns.columnconfigure(i, weight=1)
        ttk.Button(btns, text="Generate Plan", command=self.generate).grid(row=0, column=0, sticky="ew", padx=(0,6))
        ttk.Button(btns, text="Clear", command=lambda: self.brief.delete("1.0", END)).grid(row=0, column=1, sticky="ew")

        self.status = StringVar(value="Ready.")
        ttk.Label(frm, textvariable=self.status).grid(row=5, column=0, columnspan=4, sticky="w", pady=(8,0))

    def load_file(self):
        p = filedialog.askopenfilename(title="Select a brief or guidelines file",
                                       filetypes=[("Text-like","*.txt *.md *.pdf *.docx"), ("All","*.*")])
        if not p: return
        text = ""
        try:
            from pypdf import PdfReader
            from docx import Document as DocxDocument
            ext = Path(p).suffix.lower()
            if ext == ".pdf":
                rd = PdfReader(p); text = "\n".join(pg.extract_text() or "" for pg in rd.pages)
            elif ext == ".docx":
                doc = DocxDocument(p); text = "\n".join(par.text for par in doc.paragraphs)
            else:
                text = Path(p).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            try: text = Path(p).read_text(encoding="utf-8", errors="ignore")
            except Exception: text = ""
        if not text.strip():
            messagebox.showerror("Planner.AI","Could not read file.")
            return
        self.brief.delete("1.0", END); self.brief.insert(END, text)

    def generate(self):
        try:
            title = self.title_var.get().strip() or "Untitled Project"
            due = parse_due(self.due_var.get())
            start = today_local()
            if due.date() < start.date():
                messagebox.showerror("Planner.AI","Due date is in the past.")
                return
            try:
                # how = hours oer week 
                hpw = float(self.hpw_var.get())
            except:
                hpw = 8.0
            brief_text = self.brief.get("1.0", END).strip()
            if not brief_text:
                messagebox.showinfo("Planner.AI","Paste or load a brief first.")
                return
            
            self.status.set("Asking Gemini for task breakdown…")
            self.root.update_idletasks()
            
            # fetcing the due date time imputted
            raw = ai(make_prompt(title, due.strftime("%Y-%m-%d"), hpw, brief_text))

            tasks, assumptions = parse_tasks_json(raw)
            if not tasks:
                raise RuntimeError("No tasks returned by model.")

            self.status.set("Scheduling tasks to due date…")
            self.root.update_idletasks()
            events, rows = schedule_tasks(tasks, start, due, hpw)

            proj_dir = OUTDIR / slugify(title)
            proj_dir.mkdir(parents=True, exist_ok=True)

            # CSV
            csvp = proj_dir / "plan.csv"
            with csvp.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Task","Est. Hours","Start","End","Notes"])
                w.writerows(rows)

            # ICS
            icsp = proj_dir / "plan.ics"
            write_ics(events, icsp, cal_name=f"Planner.AI — {title}")

            # MD summary
            mdp = proj_dir / "plan.md"
            total_h = sum(t["hours"] for t in tasks)
            md = [f"# Plan: {title}", f"- Due: {due.date()}", f"- Hours/week: {hpw}", f"- Estimated total hours: {total_h:.1f}", ""]
            md.append("## Tasks")
            for r in rows:
                md.append(f"- **{r[0]}** — {r[1]}h ({r[2]} → {r[3]})")
            if assumptions:
                md += ["", "## Assumptions", assumptions]
            mdp.write_text("\n".join(md), encoding="utf-8")

            self.status.set(f"Done. Saved:\n{csvp}\n{icsp}\n{mdp}")
            messagebox.showinfo("Planner.AI", f"Saved:\n{csvp}\n{icsp}\n{mdp}")
        except Exception as e:
            messagebox.showerror("Planner.AI", f"Error: {e}")
            self.status.set("Error.")

def main():
    if not config.gemini_api_key or str(config.gemini_api_key).startswith("PUT_"):
        raise SystemExit("Add your Google AI Studio key to config.toml (gemini_api_key).")
    root = Tk()
    App(root)
    root.geometry("800x600")
    root.mainloop()

if __name__ == "__main__":
    main()
