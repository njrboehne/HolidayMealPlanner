from datetime import datetime, timedelta

from flask import Flask, render_template, request
import plotly.figure_factory as ff


app = Flask(__name__)


def parse_minutes(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_schedule(dishes, dinner_time_str):
    """
    Build a backward-scheduled list of tasks for the Gantt chart.

    dishes: list of dicts with keys:
      - name
      - prep_minutes (int-like string)
      - cook_minutes (int-like string)
      - temp (string)
    dinner_time_str: 'YYYY-MM-DDTHH:MM' from HTML datetime-local input
    """
    # Parse dinner time
    dinner_time = datetime.fromisoformat(dinner_time_str)

    tasks = []
    for dish in dishes:
        name = dish["name"] or "Unnamed Dish"
        prep = parse_minutes(dish["prep_minutes"], 0)
        cook = parse_minutes(dish["cook_minutes"], 0)
        temp = dish.get("temp", "")

        # Backward scheduling for each dish, independent of others:
        cook_end = dinner_time
        cook_start = cook_end - timedelta(minutes=cook)
        prep_end = cook_start
        prep_start = prep_end - timedelta(minutes=prep)

        # Prep task
        if prep > 0:
            tasks.append(
                dict(
                    Task=f"{name} - Prep",
                    Start=prep_start,
                    Finish=prep_end,
                    Resource=f"Prep ({temp})" if temp else "Prep",
                )
            )

        # Cook task
        if cook > 0:
            tasks.append(
                dict(
                    Task=f"{name} - Cook",
                    Start=cook_start,
                    Finish=cook_end,
                    Resource=f"Cook @ {temp}" if temp else "Cook",
                )
            )

    return tasks


def make_gantt(tasks):
    """
    Create a Plotly Gantt chart HTML div from tasks.
    """
    if not tasks:
        return ""

    fig = ff.create_gantt(
        tasks,
        index_col="Resource",
        show_colorbar=True,
        group_tasks=True,
        showgrid_x=True,
        showgrid_y=True,
    )
    fig.update_layout(
        title="Cooking Schedule",
        xaxis_title="Time",
        yaxis_title="Tasks",
    )

    from plotly.offline import plot

    div = plot(fig, include_plotlyjs="cdn", output_type="div")
    return div


@app.route("/", methods=["GET", "POST"])
def index():
    plot_div = ""
    dinner_time = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1)
    dinner_time_str = dinner_time.strftime("%Y-%m-%dT%H:%M")

    # Default: one empty dish row
    dishes = [
        {"name": "", "prep_minutes": "", "cook_minutes": "", "temp": ""}
    ]

    if request.method == "POST":
        dinner_time_str = request.form.get("dinner_time") or dinner_time_str

        # Collect dishes from form arrays
        names = request.form.getlist("name")
        preps = request.form.getlist("prep_minutes")
        cooks = request.form.getlist("cook_minutes")
        temps = request.form.getlist("temp")

        dishes = []
        for n, p, c, t in zip(names, preps, cooks, temps):
            # Skip completely empty rows
            if not (n or p or c or t):
                continue
            dishes.append(
                {
                    "name": n.strip(),
                    "prep_minutes": p.strip(),
                    "cook_minutes": c.strip(),
                    "temp": t.strip(),
                }
            )

        tasks = build_schedule(dishes, dinner_time_str)
        plot_div = make_gantt(tasks)

        # If user deleted all rows, keep one empty row for UI
        if not dishes:
            dishes = [
                {"name": "", "prep_minutes": "", "cook_minutes": "", "temp": ""}
            ]

    return render_template(
        "index.html",
        dishes=dishes,
        dinner_time=dinner_time_str,
        plot_div=plot_div,
    )


if __name__ == "__main__":
    app.run(debug=True)