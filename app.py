from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
from flask import Flask, render_template, request
from plotly.offline import plot


app = Flask(__name__)

PREP_COLOR = "#C4843C"
COOK_COLOR = "#234E3C"
CARD_COLOR = "#FFFCF8"
INK_COLOR = "#1C1917"
GRID_COLOR = "#EFE6DA"

NOT_OVEN = (
    "stove",
    "burner",
    "grill",
    "microwave",
    "air fry",
    "slow cooker",
    "instant",
    "simmer",
    "boil",
    "saute",
    "sauté",
    "steam",
    "counter",
    "none",
    "n/a",
    "room",
)


def parse_minutes(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def parse_dinner(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def default_dinner():
    now = datetime.now().replace(second=0, microsecond=0)
    dinner = now.replace(hour=18, minute=0)
    if dinner <= now + timedelta(minutes=30):
        dinner += timedelta(days=1)
    return dinner


def format_clock(moment, dinner=None):
    hour = moment.strftime("%I").lstrip("0") or "12"
    clock = f"{hour}:{moment.strftime('%M %p')}"
    if dinner is not None and moment.date() != dinner.date():
        return f"{moment.strftime('%b')} {moment.day}, {clock}"
    return clock


def format_dinner_label(moment):
    hour = moment.strftime("%I").lstrip("0") or "12"
    clock = f"{hour}:{moment.strftime('%M %p')}"
    return f"{moment.strftime('%A')}, {moment.strftime('%B')} {moment.day} · {clock}"


def format_duration(minutes):
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} hr {mins} min"
    if hours:
        return f"{hours} hr"
    return f"{mins} min"


def is_oven(temp):
    text = (temp or "").strip().lower()
    if not text:
        return False
    if any(word in text for word in NOT_OVEN):
        return False
    if any(word in text for word in ("oven", "bake", "roast", "broil")):
        return True
    return any(character.isdigit() for character in text)


def blank_dish():
    return {"name": "", "prep_minutes": "", "cook_minutes": "", "temp": "", "start_by": ""}


def parse_oven_count(value):
    return 1 if str(value) == "1" else 2


def oven_label(oven):
    return f"Oven {oven}" if oven else ""


def dish_label(name, seen):
    seen[name] = seen.get(name, 0) + 1
    if seen[name] == 1:
        return name
    return f"{name} ({seen[name]})"


def build_schedule(dishes, dinner):
    seen = {}
    totals = {}
    for dish in dishes:
        totals[dish["name"] or "Untitled dish"] = totals.get(dish["name"] or "Untitled dish", 0) + 1

    tasks = []
    schedule = []

    for dish in dishes:
        raw_name = dish["name"] or "Untitled dish"
        # Only suffix a name when the menu actually repeats it.
        if totals.get(raw_name, 0) > 1:
            name = dish_label(raw_name, seen)
        else:
            name = raw_name

        prep = parse_minutes(dish["prep_minutes"])
        cook = parse_minutes(dish["cook_minutes"])
        temp = (dish.get("temp") or "").strip()

        cook_end = dinner
        cook_start = cook_end - timedelta(minutes=cook)
        prep_end = cook_start
        prep_start = prep_end - timedelta(minutes=prep)
        first_start = prep_start if prep else cook_start

        dish["start_by"] = format_clock(first_start, dinner) if prep or cook else ""

        if prep:
            tasks.append(_task(name, "Prep", prep_start, prep_end, prep, "", ""))
            schedule.append(_step(name, "Prep", prep_start, prep_end, prep, "", "", dinner))
        if cook:
            tasks.append(_task(name, "Cook", cook_start, cook_end, cook, temp, ""))
            schedule.append(_step(name, "Cook", cook_start, cook_end, cook, temp, "", dinner))

    schedule.sort(key=lambda step: (step["start"], 0 if step["phase"] == "Prep" else 1, step["dish"]))
    return tasks, schedule


def _cook_meta(minutes, temp, oven):
    parts = [format_duration(minutes)]
    if temp:
        parts.append(temp)
    if oven:
        parts.append(oven_label(oven))
    return " · ".join(parts)


def _task(name, phase, start, end, minutes, temp, oven):
    window = f"{format_clock(start)} – {format_clock(end)}"
    if phase == "Prep":
        meta = format_duration(minutes)
    else:
        meta = _cook_meta(minutes, temp, oven)
    return {
        "Dish": name,
        "Phase": phase,
        "Start": start,
        "Finish": end,
        "Window": window,
        "Meta": meta,
    }


def _step(name, phase, start, end, minutes, temp, oven, dinner):
    if phase == "Prep":
        meta = f"{format_duration(minutes)} · until {format_clock(end, dinner)}"
    else:
        meta = _cook_meta(minutes, temp, oven)
    return {
        "dish": name,
        "phase": phase,
        "phase_class": phase.lower(),
        "time": format_clock(start, dinner),
        "meta": meta,
        "start": start,
        "end": end,
        "temp": temp,
        "oven": oven,
    }


def _clusters(steps):
    steps = sorted(steps, key=lambda step: (step["start"], step["end"]))
    clusters = []
    current = []
    current_end = None
    for step in steps:
        if not current or step["start"] < current_end:
            current.append(step)
            current_end = max(current_end, step["end"]) if current_end else step["end"]
        else:
            clusters.append(current)
            current = [step]
            current_end = step["end"]
    if current:
        clusters.append(current)
    return clusters


def _overlaps(left, right):
    return left["start"] < right["end"] and right["start"] < left["end"]


def _temp_key(step):
    return (step.get("temp") or "").strip().lower()


def place_ovens(tasks, schedule, oven_count):
    """Put each oven dish into an oven. Two ovens are filled to avoid temperature clashes."""
    count = 2 if oven_count == 2 else 1
    slots = {str(index): [] for index in range(1, count + 1)}
    cooks = [
        step
        for step in schedule
        if step["phase"] == "Cook" and is_oven(step["temp"])
    ]
    cooks.sort(key=lambda step: (step["start"], step["end"], step["dish"]))

    for step in schedule:
        step["oven"] = ""

    for step in cooks:
        best_id = "1"
        best_cost = None
        for oven_id, assigned in slots.items():
            overlapping = [other for other in assigned if _overlaps(step, other)]
            mismatches = sum(1 for other in overlapping if _temp_key(other) != _temp_key(step))
            if not overlapping:
                cost = (0, 0, 0)
            elif mismatches == 0:
                cost = (1, len(overlapping), 0)
            else:
                cost = (2, mismatches, len(overlapping))
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_id = oven_id
        step["oven"] = best_id
        slots[best_id].append(step)

    show_oven = count == 2
    task_by_key = {(task["Dish"], task["Phase"]): task for task in tasks}
    for step in schedule:
        if step["phase"] != "Cook":
            continue
        minutes = int((step["end"] - step["start"]).total_seconds() // 60)
        label = step["oven"] if show_oven else ""
        step["meta"] = _cook_meta(minutes, step["temp"], label)
        task = task_by_key.get((step["dish"], "Cook"))
        if task:
            task["Meta"] = step["meta"]


def _conflict_note(cluster, oven, oven_count):
    temps = list(dict.fromkeys(step["temp"] for step in cluster if step["temp"]))
    mismatch = len(temps) > 1
    rows = []
    for step in cluster:
        span = f"{format_clock(step['start'])} – {format_clock(step['end'])}"
        detail = f"{step['temp']} · {span}" if step["temp"] else span
        rows.append({"dish": step["dish"], "detail": detail})
    if oven_count == 1:
        if mismatch:
            return {
                "tone": "warn",
                "title": "Temperatures don't match",
                "rows": rows,
                "hint": "Stagger one dish, or cook them at the same temperature.",
            }
        return {
            "tone": "info",
            "title": "The oven is crowded",
            "rows": rows,
            "hint": "They can share the racks if everything fits.",
        }
    label = oven_label(oven)
    if mismatch:
        return {
            "tone": "warn",
            "title": f"{label} temperatures don't match",
            "rows": rows,
            "hint": "The other oven is busy then too. Stagger one dish, or share a temperature.",
        }
    return {
        "tone": "info",
        "title": f"{label} is crowded",
        "rows": rows,
        "hint": "These share an oven. They fit together if the racks allow.",
    }


def oven_conflicts(schedule, oven_count):
    notes = []
    oven_ids = ("1",) if oven_count == 1 else ("1", "2")
    for oven in oven_ids:
        cooking = [
            step
            for step in schedule
            if step["phase"] == "Cook" and step.get("oven") == oven
        ]
        for cluster in _clusters(cooking):
            if len(cluster) > 1:
                notes.append(_conflict_note(cluster, oven, oven_count))
    return notes


def summarize(schedule, dinner):
    first = schedule[0]
    lead = dinner - first["start"]
    lead_minutes = max(0, int(lead.total_seconds() // 60))
    verb = "prep" if first["phase"] == "Prep" else "cooking"
    return {
        "opener": f"First step is {verb} for {first['dish']} at {first['time']}.",
        "first_step": first["time"],
        "ready": format_clock(dinner),
        "lead": format_duration(lead_minutes),
        "dinner_label": format_dinner_label(dinner),
    }


def make_gantt(tasks, dinner):
    if not tasks:
        return ""

    frame = pd.DataFrame(tasks)
    dish_order = list(dict.fromkeys(frame["Dish"]))
    earliest = min(frame["Start"])
    span = max((dinner - earliest).total_seconds() / 60, 30)
    pad = timedelta(minutes=max(12, int(span * 0.06)))

    figure = px.timeline(
        frame,
        x_start="Start",
        x_end="Finish",
        y="Dish",
        color="Phase",
        color_discrete_map={"Prep": PREP_COLOR, "Cook": COOK_COLOR},
        custom_data=["Phase", "Window", "Meta"],
    )
    figure.update_traces(
        marker_line_width=0,
        hovertemplate="<b>%{y}</b><br>%{customdata[0]} · %{customdata[1]}<br>%{customdata[2]}<extra></extra>",
    )
    figure.update_yaxes(
        categoryorder="array",
        categoryarray=list(reversed(dish_order)),
        title=None,
        automargin=True,
        showgrid=False,
    )
    figure.update_xaxes(
        title=None,
        tickformat="%-I:%M %p",
        range=[earliest - pad, dinner + pad],
        showgrid=True,
        gridcolor=GRID_COLOR,
        zeroline=False,
    )
    figure.update_layout(
        paper_bgcolor=CARD_COLOR,
        plot_bgcolor=CARD_COLOR,
        font=dict(family="Outfit, sans-serif", color=INK_COLOR, size=13),
        margin=dict(l=0, r=12, t=40, b=8),
        height=min(680, max(280, 62 * len(dish_order) + 96)),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            x=0,
            title_text="",
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor=INK_COLOR,
            font_size=13,
            font_family="Outfit, sans-serif",
            font_color="#F7F3EE",
        ),
    )
    return plot(
        figure,
        include_plotlyjs="cdn",
        output_type="div",
        config={"displayModeBar": False, "displaylogo": False, "responsive": True},
    )


def read_dishes(form):
    names = form.getlist("name")
    preps = form.getlist("prep_minutes")
    cooks = form.getlist("cook_minutes")
    temps = form.getlist("temp")

    dishes = []
    for name, prep, cook, temp in zip(names, preps, cooks, temps):
        if not (name or prep or cook or temp):
            continue
        dishes.append(
            {
                "name": name.strip(),
                "prep_minutes": prep.strip(),
                "cook_minutes": cook.strip(),
                "temp": temp.strip(),
                "start_by": "",
            }
        )
    return dishes


@app.route("/", methods=["GET", "POST"])
def index():
    dinner = default_dinner()
    dinner_time = dinner.strftime("%Y-%m-%dT%H:%M")
    dishes = [blank_dish()]
    schedule = []
    summary = None
    conflicts = []
    plot_div = ""
    notice = ""
    oven_count = 2

    if request.method == "POST":
        posted = request.form.get("dinner_time") or dinner_time
        oven_count = parse_oven_count(request.form.get("oven_count"))
        parsed = parse_dinner(posted)
        dishes = read_dishes(request.form) or [blank_dish()]

        if parsed is None:
            notice = "That dinner time could not be read. Choose a date and time, then try again."
            dinner_time = posted
        else:
            dinner = parsed
            dinner_time = posted
            tasks, schedule = build_schedule(dishes, dinner)
            if schedule:
                place_ovens(tasks, schedule, oven_count)
                summary = summarize(schedule, dinner)
                conflicts = oven_conflicts(schedule, oven_count)
                plot_div = make_gantt(tasks, dinner)
            else:
                notice = "Add a prep or cook time to at least one dish."

    dish_count = sum(1 for dish in dishes if dish["name"].strip())

    return render_template(
        "index.html",
        dishes=dishes,
        dinner_time=dinner_time,
        schedule=schedule,
        summary=summary,
        conflicts=conflicts,
        plot_div=plot_div,
        notice=notice,
        dish_count=dish_count,
        oven_count=oven_count,
    )


if __name__ == "__main__":
    app.run(debug=True)
