"""
graph.py — Wires the LangGraph StateGraph.

Flow:
  search ──> rank ──(loop back if not done)──> search
                │
                └──(done)──> finish ──> report ──> END

The Search<->Rank loop is the only genuinely agentic part — everything
downstream is a fixed, single-pass sequence.
"""

import logging
from langgraph.graph import StateGraph, END

from state import GraphState
from nodes.search import run_search_round
from nodes.rank import run_rank_round
from nodes.finish import run_finish
from nodes.report import generate_report
from nodes.memory import update_lessons
from config import MAX_SEARCH_STEPS, DRAFTS_PER_RUN, ROUNDS_BEFORE_GIVE_UP

log = logging.getLogger(__name__)


def _finish_node(state: dict) -> dict:
    state["stop_reason"] = _determine_stop_reason(state)
    # Distill this run's Search<->Rank feedback into the persisted,
    # cross-run lessons memo for this track, before handing off to Finish.
    update_lessons(state["track"], state["feedback_log"])
    return run_finish(state)


def _report_node(state: dict) -> dict:
    path = generate_report(state)
    state["report_path"] = path
    return state


def _determine_stop_reason(state: dict) -> str:
    """Figures out why the search loop stopped — used by finish node for reporting."""
    qualified_count = len(state["qualified"])
    step_count = state["step_count"]
    max_steps = state["max_steps"]

    if qualified_count >= DRAFTS_PER_RUN:
        return f"Found {qualified_count} qualified candidates (target reached)."

    if step_count >= max_steps:
        return (
            f"Step budget exhausted ({step_count}/{max_steps}). "
            f"Returning {qualified_count} qualified candidate(s) found so far."
        )

    recent = state["feedback_log"][-ROUNDS_BEFORE_GIVE_UP:]
    if len(recent) >= ROUNDS_BEFORE_GIVE_UP and all(r["verdict"] in ("weak", "empty") for r in recent):
        return (
            f"{ROUNDS_BEFORE_GIVE_UP} consecutive weak/empty rounds — stopping early. "
            f"Returning {qualified_count} qualified candidate(s) found so far."
        )

    return f"Returning {qualified_count} qualified candidate(s) found so far."


def _should_continue_searching(state: dict) -> str:
    """Conditional edge — routing decision only. Does NOT mutate state —
    LangGraph routing functions aren't guaranteed to have mutations persist
    to the next node, so stop_reason is set separately in _finish_node."""
    qualified_count = len(state["qualified"])
    step_count = state["step_count"]
    max_steps = state["max_steps"]

    if qualified_count >= DRAFTS_PER_RUN:
        return "finish"

    if step_count >= max_steps:
        return "finish"

    recent = state["feedback_log"][-ROUNDS_BEFORE_GIVE_UP:]
    if len(recent) >= ROUNDS_BEFORE_GIVE_UP and all(r["verdict"] in ("weak", "empty") for r in recent):
        return "finish"

    return "search"


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("search", run_search_round)
    graph.add_node("rank", run_rank_round)
    graph.add_node("finish", _finish_node)
    graph.add_node("report", _report_node)

    graph.set_entry_point("search")
    graph.add_edge("search", "rank")
    graph.add_conditional_edges("rank", _should_continue_searching, {
        "search": "search",
        "finish": "finish",
    })
    graph.add_edge("finish", "report")
    graph.add_edge("report", END)

    return graph.compile()


def run_pipeline(profile: dict, track: str) -> dict:
    initial_state: GraphState = {
        "profile": profile,
        "track": track,
        "step_count": 0,
        "max_steps": MAX_SEARCH_STEPS,
        "search_history": [],
        "feedback_log": [],
        "candidates": [],
        "seen_ids": [],
        "geography_coverage_note": None,
        "qualified": [],
        "stop_reason": "",
        "finished": [],
        "report_path": None,
    }

    app = build_graph()
    final_state = app.invoke(initial_state, config={"recursion_limit": 100})
    return final_state
