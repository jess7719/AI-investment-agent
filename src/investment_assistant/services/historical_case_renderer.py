"""Plain-English CLI and report rendering from the public case projection."""


def percent(value):
    return "unavailable" if value is None else f"{value:+.2f}%"


def render_historical_case(case, *, shortened=False):
    card = case.beginner_view()
    lines = [f"Historical Case — {case.analogue_date.strftime('%d %B %Y')}",
             f"Similarity to today: {case.similarity_score:.1f} / 100",
             f"Comparison snapshot as of: {case.current_as_of}", card["scope_notice"],
             "", "WHAT HAPPENED?"]
    for cluster in card["developments"][:3 if shortened else 5]:
        if cluster["facts"]:
            lines.append(" ".join(cluster["facts"]))
        else:
            lines.append(cluster["title"] + ": Public reports were found, but a verified "
                         "event explanation is not available. See the linked metadata.")
    if not card["developments"]:
        lines.append("Verified historical event context is unavailable for this date. "
                     "The observations below cannot establish why prices moved.")
    if card["macro_conditions"]:
        lines.append(" ".join(card["macro_conditions"][:2 if shortened else 4]))
    lines += ["", "WHAT IS SIMILAR TO TODAY?"]
    lines += ["- " + s for s in card["similarities"]] or [
        "No sufficiently supported similarities are available."]
    lines += ["", "WHAT HAPPENED NEXT?"]
    for outcome in card["outcomes"]:
        label = f"{card['benchmark_label']} — {outcome['trading_days']} trading days"
        lines.append(f"{label}: {percent(outcome['benchmark_return'])}")
        if outcome["sufficient_benchmark_data"]:
            lines.append(f"Benchmark maximum drawdown: "
                         f"{percent(outcome['benchmark_maximum_drawdown'])}; "
                         f"{outcome['start_date']} to {outcome['end_date']}")
    lines += ["Returns use closing prices, exclude dividends and are not total returns."]
    lines += ["", "Sources:"]
    for source in card["sources"][:5 if shortened else 25]:
        published = (source['published_at'][:10] if source['publication_precision'] == 'date'
                     else source['published_at'])
        lines.append(f"[{source['reference']}] {source['publisher']} — {source['headline']} "
                     f"({published})\n{source['url']}")
    if not card["sources"]:
        lines.append("Historical event sources unavailable; market observations are in the saved "
                     "analogue evidence.")
    lines += ["", "Important: " + card["notice"]]
    return "\n".join(lines)
