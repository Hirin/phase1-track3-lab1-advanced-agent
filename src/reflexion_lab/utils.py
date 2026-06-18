from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Iterable
from .schemas import QAExample, RunRecord

def normalize_answer(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text

def load_dataset(path: str | Path) -> list[QAExample]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    processed = []
    for item in raw:
        if isinstance(item, dict):
            mapped = {}
            mapped["qid"] = str(item.get("qid", item.get("_id", item.get("id", "q_unknown"))))
            
            diff = str(item.get("difficulty", item.get("level", "medium"))).lower()
            if diff not in ["easy", "medium", "hard"]:
                diff = "medium"
            mapped["difficulty"] = diff
            
            mapped["question"] = item.get("question", "")
            mapped["gold_answer"] = item.get("gold_answer", item.get("answer", ""))
            
            context_list = item.get("context", [])
            mapped_context = []
            for doc in context_list:
                if isinstance(doc, dict):
                    mapped_context.append({
                        "title": doc.get("title", ""),
                        "text": doc.get("text", "")
                    })
                elif isinstance(doc, (list, tuple)) and len(doc) >= 2:
                    title = doc[0]
                    sentences = doc[1]
                    if isinstance(sentences, list):
                        text = " ".join(str(s) for s in sentences)
                    else:
                        text = str(sentences)
                    mapped_context.append({
                        "title": str(title),
                        "text": text
                    })
            mapped["context"] = mapped_context
            processed.append(QAExample.model_validate(mapped))
        else:
            processed.append(QAExample.model_validate(item))
    return processed

def save_jsonl(path: str | Path, records: Iterable[RunRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")
