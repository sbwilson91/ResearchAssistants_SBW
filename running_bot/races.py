"""Race countdown and training phase tracker."""
from datetime import date

PHASES = [
    (0,  1,  "Race Week", "Execute the race plan. Keep it sharp, stay calm."),
    (1,  3,  "Taper",     "Reduce volume, maintain intensity. Trust the training."),
    (3,  8,  "Peak",      "Highest quality work. Race-specific sessions and long efforts."),
    (8,  12, "Build",     "Increasing intensity and race-specific work. Consistency is key."),
    (12, 52, "Base",      "Foundation work. Aerobic development and injury prevention."),
]

RACE_TYPE_LABELS = {
    "half_marathon":  "Half Marathon",
    "marathon":       "Marathon",
    "backyard_ultra": "Backyard Ultra",
    "10k":            "10K",
    "5k":             "5K",
    "ultra":          "Ultra",
}


def _weeks_until(race_date: date, today: date) -> float:
    return (race_date - today).days / 7


def _get_phase(weeks_until: float) -> tuple[str, str]:
    for low, high, name, detail in PHASES:
        if low <= weeks_until < high:
            return name, detail
    return "Off-Season", "Recovery and unstructured training."


def get_race_data(config: dict, today: date | None = None) -> dict:
    """
    Returns structured race data for use in insights and report generation.

    Result shape:
    {
        "races": [{"name", "date", "type", "type_label", "goal", "days_until",
                   "weeks_until", "phase", "phase_detail", "primary"}, ...],
        "primary": <race dict or None>,
        "phase": <phase name for primary race>,
        "phase_detail": <phase detail for primary race>,
    }
    """
    if today is None:
        today = date.today()

    races_cfg = config.get("races", [])
    races = []

    for r in races_cfg:
        race_date = date.fromisoformat(r["date"])
        days_until = (race_date - today).days
        weeks_until = days_until / 7
        phase, phase_detail = _get_phase(weeks_until)
        type_label = RACE_TYPE_LABELS.get(r.get("type", ""), r.get("type", "Race"))

        races.append({
            "name":         r["name"],
            "date":         race_date,
            "date_str":     r["date"],
            "type":         r.get("type", ""),
            "type_label":   type_label,
            "goal":         r.get("goal"),
            "goal_seconds": r.get("goal_seconds"),
            "days_until":   days_until,
            "weeks_until":  round(weeks_until, 1),
            "phase":        phase,
            "phase_detail": phase_detail,
            "primary":      r.get("primary", False),
        })

    races.sort(key=lambda r: r["date"])

    primary = next((r for r in races if r["primary"]), races[0] if races else None)

    return {
        "races":        races,
        "primary":      primary,
        "phase":        primary["phase"] if primary else "Base",
        "phase_detail": primary["phase_detail"] if primary else "",
    }
