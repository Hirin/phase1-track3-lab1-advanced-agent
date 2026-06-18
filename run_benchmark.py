from __future__ import annotations
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import typer
from rich import print
from src.reflexion_lab.agents import ReActAgent, ReflexionAgent
from src.reflexion_lab.reporting import build_report, save_report
from src.reflexion_lab.utils import load_dataset, save_jsonl

app = typer.Typer(add_completion=False)

@app.command()
def main(
    dataset: str = "data/hotpot_mini.json",
    out_dir: str = "outputs/sample_run",
    reflexion_attempts: int = 3,
    mock: bool = typer.Option(True, "--mock/--no-mock", help="Use mock mode instead of real LLM API calls.")
) -> None:
    # Set mock mode flag in environment and import runtime to update
    os.environ["MOCK_MODE"] = "true" if mock else "false"
    from src.reflexion_lab import mock_runtime
    mock_runtime.MOCK_MODE = mock

    examples = load_dataset(dataset)
    react = ReActAgent()
    reflexion = ReflexionAgent(max_attempts=reflexion_attempts)

    if mock:
        print("[yellow]Running benchmark in Mock mode...[/yellow]")
        react_records = [react.run(example) for example in examples]
        reflexion_records = [reflexion.run(example) for example in examples]
    else:
        print(f"[yellow]Running benchmark in real LLM mode ({len(examples)} examples) using ThreadPoolExecutor...[/yellow]")
        with ThreadPoolExecutor(max_workers=10) as executor:
            react_records = list(executor.map(react.run, examples))
        with ThreadPoolExecutor(max_workers=10) as executor:
            reflexion_records = list(executor.map(reflexion.run, examples))

    all_records = react_records + reflexion_records
    out_path = Path(out_dir)
    save_jsonl(out_path / "react_runs.jsonl", react_records)
    save_jsonl(out_path / "reflexion_runs.jsonl", reflexion_records)

    report = build_report(all_records, dataset_name=Path(dataset).name, mode="mock" if mock else "real")
    json_path, md_path = save_report(report, out_path)
    print(f"[green]Saved[/green] {json_path}")
    print(f"[green]Saved[/green] {md_path}")
    print(json.dumps(report.summary, indent=2))

if __name__ == "__main__":
    app()
