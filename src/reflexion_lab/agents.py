from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from .mock_runtime import FAILURE_MODE_BY_QID, actor_answer, evaluator, reflector
from .schemas import AttemptTrace, QAExample, ReflectionEntry, RunRecord

@dataclass
class BaseAgent:
    agent_type: Literal["react", "reflexion"]
    max_attempts: int = 1
    def run(self, example: QAExample) -> RunRecord:
        reflection_memory: list[str] = []
        reflections: list[ReflectionEntry] = []
        traces: list[AttemptTrace] = []
        final_answer = ""
        final_score = 0

        # Adaptive max attempts based on difficulty
        actual_max_attempts = self.max_attempts
        if self.agent_type == "reflexion":
            if example.difficulty == "easy":
                actual_max_attempts = min(2, self.max_attempts)
            elif example.difficulty == "hard":
                actual_max_attempts = max(4, self.max_attempts)

        for attempt_id in range(1, actual_max_attempts + 1):
            answer, actor_tokens, actor_latency = actor_answer(example, attempt_id, self.agent_type, reflection_memory)
            judge = evaluator(example, answer)
            
            # Start token/latency with Actor + Evaluator
            token_estimate = actor_tokens + judge.token_estimate
            latency_ms = actor_latency + judge.latency_ms
            
            trace = AttemptTrace(
                attempt_id=attempt_id, 
                answer=answer, 
                score=judge.score, 
                reason=judge.reason, 
                token_estimate=token_estimate, 
                latency_ms=latency_ms
            )
            final_answer = answer
            final_score = judge.score
            if judge.score == 1:
                traces.append(trace)
                break
            
            # Reflexion logic: if reflexion agent and not last attempt, run reflector
            if self.agent_type == "reflexion" and attempt_id < actual_max_attempts:
                reflection = reflector(example, attempt_id, judge)
                trace.reflection = reflection
                # Add reflector token and latency to this attempt trace
                trace.token_estimate += reflection.token_estimate
                trace.latency_ms += reflection.latency_ms
                
                reflections.append(reflection)
                # Formulate a structured lesson to put in memory
                memory_entry = (
                    f"Attempt {attempt_id} answer: '{answer}'. "
                    f"Failure reason: {reflection.failure_reason}. "
                    f"Lesson: {reflection.lesson}. "
                    f"Next strategy: {reflection.next_strategy}."
                )
                reflection_memory.append(memory_entry)
                
                # Memory compression / pruning: keep only the first/most fundamental lesson and the latest one
                # to prevent context window bloat and instruction confusion
                if len(reflection_memory) > 2:
                    reflection_memory = [reflection_memory[0], reflection_memory[-1]]
            
            traces.append(trace)
        total_tokens = sum(t.token_estimate for t in traces)
        total_latency = sum(t.latency_ms for t in traces)
        
        # General, dynamic failure mode classification
        if final_score == 1:
            failure_mode = "none"
        else:
            if judge.missing_evidence and len(judge.missing_evidence) > 0:
                failure_mode = "incomplete_multi_hop"
            elif judge.spurious_claims and len(judge.spurious_claims) > 0:
                failure_mode = "entity_drift"
            else:
                failure_mode = "wrong_final_answer"

        return RunRecord(
            qid=example.qid,
            question=example.question,
            gold_answer=example.gold_answer,
            agent_type=self.agent_type,
            predicted_answer=final_answer,
            is_correct=bool(final_score),
            attempts=len(traces),
            token_estimate=total_tokens,
            latency_ms=total_latency,
            failure_mode=failure_mode,
            reflections=reflections,
            traces=traces
        )

class ReActAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(agent_type="react", max_attempts=1)

class ReflexionAgent(BaseAgent):
    def __init__(self, max_attempts: int = 3) -> None:
        super().__init__(agent_type="reflexion", max_attempts=max_attempts)
