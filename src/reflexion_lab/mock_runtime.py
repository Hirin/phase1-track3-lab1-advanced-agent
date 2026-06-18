from __future__ import annotations
import os
import json
import re
import time
import urllib.request
from dotenv import load_dotenv

from .schemas import QAExample, JudgeResult, ReflectionEntry
from .utils import normalize_answer
from .prompts import ACTOR_SYSTEM, EVALUATOR_SYSTEM, REFLECTOR_SYSTEM

# Load environment variables
load_dotenv()

MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"

FIRST_ATTEMPT_WRONG = {"hp2": "London", "hp4": "Atlantic Ocean", "hp6": "Red Sea", "hp8": "Andes"}
FAILURE_MODE_BY_QID = {"hp2": "incomplete_multi_hop", "hp4": "wrong_final_answer", "hp6": "entity_drift", "hp8": "entity_drift"}

def call_llm(messages: list[dict], system: str = None, max_tokens: int = 1024) -> tuple[str, int, int]:
    """
    Calls OpenAI API with standard request and returns (response_text, total_tokens, latency_ms).
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1/chat/completions")
    
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set. Please check your .env file.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Construct messages with system prompt in role: system
    formatted_messages = []
    if system:
        formatted_messages.append({"role": "system", "content": system})
    formatted_messages.extend(messages)

    data = {
        "model": "gpt-5.4-mini",
        "messages": formatted_messages,
        # Use max_completion_tokens as required by gpt-5.4-mini
        "max_completion_tokens": max_tokens
    }

    req = urllib.request.Request(
        base_url,
        headers=headers,
        data=json.dumps(data).encode("utf-8"),
        method="POST"
    )

    start_time = time.time()
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            latency_ms = int((time.time() - start_time) * 1000)
            res_json = json.loads(res_body)
            content = res_json["choices"][0]["message"]["content"]
            usage = res_json.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)
            return content, total_tokens, latency_ms
    except Exception as e:
        if hasattr(e, 'read'):
            err_details = e.read().decode("utf-8")
            print(f"LLM API Error Details: {err_details}")
        raise RuntimeError(f"Error calling LLM: {e}")

def extract_json_block(text: str) -> str:
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace:last_brace+1]
    return text

def extract_answer(text: str) -> str:
    match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match_final = re.search(r"(?:Final Answer|Answer):\s*(.*)", text, re.IGNORECASE)
    if match_final:
        return match_final.group(1).strip()
    # If no tags, clean up the last non-empty line or return clean text
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        return lines[-1]
    return text.strip()

def actor_answer(example: QAExample, attempt_id: int, agent_type: str, reflection_memory: list[str]) -> tuple[str, int, int]:
    if MOCK_MODE:
        if example.qid not in FIRST_ATTEMPT_WRONG:
            ans = example.gold_answer
        elif agent_type == "react":
            ans = FIRST_ATTEMPT_WRONG[example.qid]
        elif attempt_id == 1 and not reflection_memory:
            ans = FIRST_ATTEMPT_WRONG[example.qid]
        else:
            ans = example.gold_answer
        return ans, 0, 0

    # Build prompt with context documents
    context_parts = []
    for idx, doc in enumerate(example.context):
        context_parts.append(f"Document [{idx+1}] - {doc.title}:\n{doc.text}")
    context_str = "\n\n".join(context_parts)

    user_content = f"Context Documents:\n{context_str}\n\nQuestion: {example.question}"
    
    if reflection_memory:
        user_content += "\n\nHere are insights from your previous failed attempts at this question. Read them to avoid repeating the same mistakes:\n"
        for idx, ref in enumerate(reflection_memory):
            user_content += f"Attempt {idx+1} Feedback:\n{ref}\n"

    messages = [{"role": "user", "content": user_content}]
    
    response_text, tokens, latency = call_llm(messages, system=ACTOR_SYSTEM, max_tokens=1024)
    parsed_answer = extract_answer(response_text)
    return parsed_answer, tokens, latency

def evaluator(example: QAExample, answer: str) -> JudgeResult:
    if MOCK_MODE:
        if normalize_answer(example.gold_answer) == normalize_answer(answer):
            return JudgeResult(score=1, reason="Final answer matches the gold answer after normalization.", token_estimate=0, latency_ms=0)
        if normalize_answer(answer) == "london":
            return JudgeResult(score=0, reason="The answer stopped at the birthplace city and never completed the second hop to the river.", missing_evidence=["Need to identify the river that flows through London."], spurious_claims=[], token_estimate=0, latency_ms=0)
        return JudgeResult(score=0, reason="The final answer selected the wrong second-hop entity.", missing_evidence=["Need to ground the answer in the second paragraph."], spurious_claims=[answer], token_estimate=0, latency_ms=0)

    user_content = f"Question: {example.question}\nGold Answer: {example.gold_answer}\nPredicted Answer: {answer}"
    messages = [{"role": "user", "content": user_content}]
    
    response_text, tokens, latency = call_llm(messages, system=EVALUATOR_SYSTEM, max_tokens=512)
    json_str = extract_json_block(response_text)
    try:
        data = json.loads(json_str)
        return JudgeResult(
            score=int(data.get("score", 0)),
            reason=data.get("reason", "No reason provided."),
            missing_evidence=data.get("missing_evidence", []),
            spurious_claims=data.get("spurious_claims", []),
            token_estimate=tokens,
            latency_ms=latency
        )
    except Exception as e:
        # Fallback if parsing fails
        is_correct = normalize_answer(example.gold_answer) == normalize_answer(answer)
        return JudgeResult(
            score=1 if is_correct else 0,
            reason=f"Failed to parse evaluator JSON. Text: {response_text}. Error: {e}",
            token_estimate=tokens,
            latency_ms=latency
        )

def reflector(example: QAExample, attempt_id: int, judge: JudgeResult) -> ReflectionEntry:
    if MOCK_MODE:
        strategy = "Do the second hop explicitly: birthplace city -> river through that city." if example.qid == "hp2" else "Verify the final entity against the second paragraph before answering."
        return ReflectionEntry(attempt_id=attempt_id, failure_reason=judge.reason, lesson="A partial first-hop answer is not enough; the final answer must complete all hops.", next_strategy=strategy, token_estimate=0, latency_ms=0)

    user_content = (
        f"Question: {example.question}\n"
        f"Previous Attempt Answer: (failed evaluation)\n"
        f"Evaluator Feedback: {judge.reason}\n"
        f"Missing Evidence: {', '.join(judge.missing_evidence)}\n"
        f"Spurious Claims: {', '.join(judge.spurious_claims)}"
    )
    messages = [{"role": "user", "content": user_content}]
    
    response_text, tokens, latency = call_llm(messages, system=REFLECTOR_SYSTEM, max_tokens=512)
    json_str = extract_json_block(response_text)
    try:
        data = json.loads(json_str)
        return ReflectionEntry(
            attempt_id=attempt_id,
            failure_reason=data.get("failure_reason", "Failed to answer correct multi-hop hops."),
            lesson=data.get("lesson", "Verify second hop connection explicitly."),
            next_strategy=data.get("next_strategy", "Reread the context."),
            token_estimate=tokens,
            latency_ms=latency
        )
    except Exception as e:
        return ReflectionEntry(
            attempt_id=attempt_id,
            failure_reason=f"Failed to parse reflector JSON. Text: {response_text}. Error: {e}",
            lesson="Review context carefully and avoid rushing the final answer.",
            next_strategy="Do not make unsupported assumptions.",
            token_estimate=tokens,
            latency_ms=latency
        )
