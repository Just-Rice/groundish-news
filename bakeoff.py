"""Compare summary models on real stories before committing to one.

    python3 bakeoff.py                       # 12 stories, Opus 5 vs Sonnet 5
    python3 bakeoff.py 30                    # 30 stories
    python3 bakeoff.py 20 claude-opus-5 claude-haiku-4-5

Picks the hardest stories it can find — the ones with the most outlets and the
widest spread across the spectrum, plus any blindspots — because that is where
models differ. Easy stories all read the same. Prints the summaries side by side
with real token counts and the monthly cost each model implies.

Needs credentials: `export ANTHROPIC_API_KEY=...` or `ant auth login`.
"""
import concurrent.futures as futures
import sys
import textwrap

import llm_summary
import pipeline

# $ per million tokens (input, output). Sonnet 5 shows introductory pricing,
# which runs through 2026-08-31 and then becomes $3 / $15.
PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
NEW_STORIES_PER_DAY = 91          # measured from this feed list


def pick(stories, count):
    """Hardest first: most outlets, widest spread of leans, blindspots included."""
    def hardness(story):
        leans = sum(1 for v in story["bar"].values() if v)
        return (bool(story["blindspot"]), leans, story["outlet_count"])
    return sorted(stories, key=hardness, reverse=True)[:count]


def main():
    args = sys.argv[1:]
    count = int(args[0]) if args and args[0].isdigit() else 12
    models = [a for a in args if not a.isdigit()] or ["claude-opus-5", "claude-sonnet-5"]

    if not llm_summary.available():
        raise SystemExit(
            "No Anthropic credentials found.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...   (or run: ant auth login)\n"
            "then re-run this script.")

    data = pipeline.load()
    if not data:
        raise SystemExit("No data/stories.json yet — run: python3 pipeline.py")

    chosen = pick(data["stories"], count)
    print(f"Comparing {len(models)} models on {len(chosen)} stories: {', '.join(models)}\n")

    results, totals = {}, {m: [0, 0] for m in models}
    jobs = [(m, s) for m in models for s in chosen]

    def run(job):
        model, story = job
        return model, story["id"], llm_summary.summarize_one(story, model=model)

    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        for model, story_id, result in pool.map(run, jobs):
            results[(model, story_id)] = result
            if result and "usage" in result:
                totals[model][0] += result["usage"]["input"]
                totals[model][1] += result["usage"]["output"]

    for story in chosen:
        print("=" * 96)
        bar = " ".join(f"{k.replace('_', '-')}:{v}" for k, v in story["bar"].items() if v)
        flag = f"  [blindspot: {story['blindspot']}]" if story["blindspot"] else ""
        print(f"{story['title']}")
        print(f"  {story['outlet_count']} outlets · {bar}{flag}")
        extract = (story.get("consensus") or {}).get("text")
        if extract:
            print("\n  " + "\033[2m" + "extractive".ljust(16) + "\033[0m"
                  + textwrap.fill(extract, 78, subsequent_indent=" " * 18)[18:])
        for model in models:
            res = results.get((model, story["id"]))
            text = res.get("text") if res and "text" in res else f"FAILED: {res}"
            print("\n  " + model.ljust(16)
                  + textwrap.fill(text, 78, subsequent_indent=" " * 18)[18:])
        print()

    print("=" * 96)
    print(f"{'model':<20} {'in':>8} {'out':>8} {'$/story':>10} {'$/month':>10}   at "
          f"{NEW_STORIES_PER_DAY} new stories/day")
    for model in models:
        tin, tout = totals[model]
        pin, pout = PRICING.get(model, (0, 0))
        cost = tin / 1e6 * pin + tout / 1e6 * pout
        per_story = cost / max(len(chosen), 1)
        print(f"{model:<20} {tin:>8} {tout:>8} {per_story:>10.5f} "
              f"{per_story * NEW_STORIES_PER_DAY * 30:>10.2f}")
    print("\nThinking tokens count as output, so these are real totals, not estimates.")


if __name__ == "__main__":
    main()
