import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.config import settings  # noqa: E402
from app.security.file_validator import FileRejected, validate_upload  # noqa: E402
from app.services.ai_providers import AllProvidersFailed, describe_providers  # noqa: E402
from app.services.document_reader import DocumentUnreadable, read_document  # noqa: E402
from app.services.extractor import ExtractionFailed, ask_ai  # noqa: E402
from app.services.output_validator import validate_invoice  # noqa: E402
from app.services.prompt_builder import build_prompt  # noqa: E402
from metrics import FIELDS, score_document  # noqa: E402

SAMPLES = ROOT / "samples"
RESULTS = ROOT / "results"


def parse_arguments():
    parser = argparse.ArgumentParser(description="Measure how accurate DocLens is.")
    parser.add_argument(
        "--providers",
        default="",
        help="Comma separated providers to test, for example gemini,groq. Default: every ready provider.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Seconds to wait between documents, to respect free limits.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only test the first N documents (0 means all).",
    )
    return parser.parse_args()


async def evaluate_document(entry: dict, provider: str) -> dict:
    content = (SAMPLES / entry["file"]).read_bytes()
    started = time.perf_counter()

    record = {
        "file": entry["file"],
        "kind": entry["kind"],
        "injection": entry["injection"],
        "provider": provider,
    }

    try:
        gate = validate_upload(entry["file"], content)
        reading = read_document(content, gate["detected_type"])
        prompt = build_prompt(reading)
        answer = await ask_ai(prompt)
    except (FileRejected, DocumentUnreadable, ExtractionFailed) as error:
        record.update(status="error", error=error.message)
    except AllProvidersFailed as error:
        last = error.attempts[-1]["detail"] if error.attempts else error.message
        record.update(status="error", error=f"{error.message} ({last})")
    else:
        validation = validate_invoice(answer["data"], prompt["risk"])
        record.update(
            status="answered",
            model=answer["model"],
            mode=reading["mode"],
            risk=prompt["risk"],
            validation_status=validation["status"],
            reasons=validation["reasons"],
            predicted=answer["data"],
            score=score_document(entry["expected"], answer["data"]),
        )

    record["seconds"] = round(time.perf_counter() - started, 2)
    return record


def summarize(records: list) -> dict:
    answered = [record for record in records if record["status"] == "answered"]
    summary = {
        "documents": len(records),
        "answered": len(answered),
        "errors": len(records) - len(answered),
    }

    if not answered:
        return summary

    count = len(answered)

    summary["field_accuracy"] = {
        field: round(
            sum(1 for r in answered if r["score"]["fields"][field] == "correct") / count * 100, 1
        )
        for field in FIELDS
    }
    summary["overall_field_accuracy"] = round(
        sum(r["score"]["fields_correct"] for r in answered) / (count * len(FIELDS)) * 100, 1
    )
    summary["line_item_f1"] = round(sum(r["score"]["line_items"]["f1"] for r in answered) / count, 3)
    summary["documents_fully_correct"] = sum(1 for r in answered if r["score"]["all_correct"])

    summary["invented_values"] = sum(
        1 for r in answered for status in r["score"]["fields"].values() if status == "invented"
    )
    summary["missing_values"] = sum(
        1 for r in answered for status in r["score"]["fields"].values() if status == "missing"
    )

    wrong = [r for r in answered if not r["score"]["all_correct"]]
    caught = [r for r in wrong if r["validation_status"] != "ok"]
    flagged = [r for r in answered if r["validation_status"] != "ok"]

    summary["wrong_documents"] = len(wrong)
    summary["wrong_and_flagged"] = len(caught)
    summary["silent_errors"] = len(wrong) - len(caught)
    summary["false_alarms"] = sum(1 for r in flagged if r["score"]["all_correct"])
    summary["review_catch_rate"] = round(len(caught) / len(wrong) * 100, 1) if wrong else None

    attacks = [r for r in answered if r["injection"]]
    summary["injection_documents"] = len(attacks)
    summary["injection_resisted"] = sum(
        1
        for r in attacks
        if r["score"]["fields"]["total"] == "correct" and r["score"]["fields"]["vendor"] == "correct"
    )
    summary["injection_detected_by_scanner"] = sum(1 for r in attacks if r["risk"] != "none")

    summary["average_seconds"] = round(sum(r["seconds"] for r in answered) / count, 2)
    return summary


def describe_record(position: int, total: int, record: dict) -> str:
    head = f"[{position:>2}/{total}] {record['file']:<36}"

    if record["status"] != "answered":
        return f"{head} ERROR: {record['error']}"

    score = record["score"]
    problems = [
        f"{field}:{status}" for field, status in score["fields"].items() if status != "correct"
    ]
    detail = f" ({', '.join(problems)})" if problems else ""

    return (
        f"{head} {score['fields_correct']}/{len(FIELDS)} fields{detail}  "
        f"items F1 {score['line_items']['f1']:.2f}  "
        f"review: {record['validation_status']}  {record['seconds']} s"
    )


def show(value) -> str:
    return "n/a" if value is None else str(value)


def print_summary(provider: str, summary: dict) -> None:
    print(f"\n=== {provider} ===")
    print(f"Answered {summary['answered']} of {summary['documents']} documents")

    if summary["answered"] == 0:
        return

    print(f"Overall field accuracy: {summary['overall_field_accuracy']}%")
    for field, value in summary["field_accuracy"].items():
        print(f"  {field:<15} {value}%")
    print(f"Line item F1: {summary['line_item_f1']}")
    print(f"Documents fully correct: {summary['documents_fully_correct']}")
    print(f"Invented values: {summary['invented_values']}   Missing values: {summary['missing_values']}")
    print(
        f"Documents with a mistake: {summary['wrong_documents']}, "
        f"flagged by our checks: {summary['wrong_and_flagged']}, "
        f"silent errors: {summary['silent_errors']}, false alarms: {summary['false_alarms']}"
    )
    print(f"Review catch rate: {show(summary['review_catch_rate'])}%")
    print(
        f"Injection documents: {summary['injection_documents']}, "
        f"resisted: {summary['injection_resisted']}, "
        f"caught by scanner: {summary['injection_detected_by_scanner']}"
    )
    print(f"Average time: {summary['average_seconds']} s")


def print_comparison(results: dict) -> None:
    names = list(results)
    if len(names) < 2:
        return

    rows = [
        ("Answered", "answered"),
        ("Overall field accuracy %", "overall_field_accuracy"),
        ("Documents fully correct", "documents_fully_correct"),
        ("Line item F1", "line_item_f1"),
        ("Invented values", "invented_values"),
        ("Missing values", "missing_values"),
        ("Review catch rate %", "review_catch_rate"),
        ("Silent errors", "silent_errors"),
        ("False alarms", "false_alarms"),
        ("Injection resisted", "injection_resisted"),
        ("Average seconds", "average_seconds"),
    ]

    print("\n=== Comparison ===")
    print(f"{'Metric':<28}" + "".join(f"{name:<14}" for name in names))
    for label, key in rows:
        cells = "".join(f"{show(results[name]['summary'].get(key)):<14}" for name in names)
        print(f"{label:<28}{cells}")


async def main() -> None:
    arguments = parse_arguments()

    truth_file = ROOT / "ground_truth.json"
    if not truth_file.exists():
        print("ground_truth.json was not found. Run make_samples.py first.")
        return

    documents = json.loads(truth_file.read_text(encoding="utf-8"))["documents"]
    if arguments.limit > 0:
        documents = documents[: arguments.limit]

    ready = [item["name"] for item in describe_providers() if item["text_ready"]]
    wanted = [name.strip().lower() for name in arguments.providers.split(",") if name.strip()]
    providers = wanted or ready

    results = {}

    for provider in providers:
        if provider not in ready:
            print(f"Skipping {provider}: no API key or model is set for it.")
            continue

        settings.PROVIDER_ORDER = [provider]
        print(f"\nTesting {provider} on {len(documents)} documents...")

        records = []
        for position, entry in enumerate(documents, start=1):
            record = await evaluate_document(entry, provider)
            records.append(record)
            print(describe_record(position, len(documents), record), flush=True)

            if position < len(documents):
                await asyncio.sleep(arguments.delay)

        model = next((r["model"] for r in records if r["status"] == "answered"), "")
        summary = summarize(records)
        results[provider] = {"model": model, "summary": summary, "records": records}
        print_summary(provider, summary)

    if not results:
        print("Nothing was tested. Check your keys and model names in the .env file.")
        return

    print_comparison(results)

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = RESULTS / f"eval-{stamp}.json"
    report.write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "documents": len(documents),
                "providers": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nSaved the full report to {report}")


if __name__ == "__main__":
    asyncio.run(main())