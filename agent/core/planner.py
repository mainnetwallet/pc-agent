"""Planner: interprets user intent and produces an execution plan.

Handles misspellings, incomplete instructions, and contextual references.
Bengali / Banglish / English supported via language detection hints.
"""

import difflib
import re
from typing import Any, Dict, List, Optional

from llm.llm import llm
from discovery.registry import registry


# Common app-name corrections (misspelling tolerance)
APP_CORRECTIONS = {
    "crome": "chrome", "chrrome": "chrome", "chorme": "chrome",
    "notpad": "notepad", "notepd": "notepad",
    "excel": "excel", "exel": "excel",
    "word": "word", "wrod": "word",
    "powrpoint": "powerpoint", "ppt": "powerpoint",
    "edge": "edge", "edg": "edge",
    "firefox": "firefox", "ff": "firefox",
    "vs code": "vscode", "visual studio code": "vscode",
    "cmd": "cmd", "command prompt": "cmd",
    "powershell": "powershell", "ps": "powershell",
}


def correct_app_name(name: str) -> str:
    """Correct common misspellings of application names."""
    key = name.strip().lower()
    if key in APP_CORRECTIONS:
        return APP_CORRECTIONS[key]
    # Fuzzy match against known corrections
    matches = difflib.get_close_matches(key, list(APP_CORRECTIONS.keys()), n=1, cutoff=0.75)
    if matches:
        return APP_CORRECTIONS[matches[0]]
    return name


def detect_language(text: str) -> str:
    """Very light language hint: english | bengali | banglish."""
    # Bengali script range
    if re.search(r"[\u0980-\u09FF]", text):
        return "bengali"
    # Banglish heuristics: romanized bengali words
    banglish_markers = ["ki", "hobe", "kor", "koro", "jan", "nai", "ache", "hobe", "plz", "pls"]
    low = text.lower()
    if any(m in low.split() for m in banglish_markers):
        return "banglish"
    return "english"


class Planner:
    def __init__(self):
        pass

    def interpret(self, instruction: str, context: str = "") -> Dict[str, Any]:
        """Interpret the user's objective, correcting errors and clarifying."""
        lang = detect_language(instruction)
        interpreted = instruction
        assumptions: List[str] = [f"Detected language: {lang}"]

        # Correct app-name typos mentioned in the instruction
        corrected = instruction
        for wrong, right in APP_CORRECTIONS.items():
            if wrong == right:
                continue
            # Whole words only: a bare substring match turned "apps" into "apowershell"
            pattern = r"\b" + re.escape(wrong) + r"\b"
            if re.search(pattern, corrected, flags=re.IGNORECASE):
                corrected = re.sub(pattern, right, corrected, flags=re.IGNORECASE)
                assumptions.append(f"Corrected '{wrong}' -> '{right}'")

        # Resolve contextual references using memory/context
        resolved = self._resolve_references(corrected, context)
        if resolved != corrected:
            assumptions.append("Resolved contextual reference from history/memory")

        return {
            "interpreted_goal": resolved,
            "original_instruction": instruction,
            "language": lang,
            "assumptions": assumptions,
        }

    def _resolve_references(self, instruction: str, context: str) -> str:
        """Replace vague references like 'my previous project' with concrete info."""
        from memory.sqlite_store import memory
        lowered = instruction.lower()
        if "previous project" in lowered or "last project" in lowered:
            projects = memory.search_projects("")[:5]
            if projects:
                names = ", ".join(p["name"] for p in projects[:3])
                return instruction.replace("my previous project", names).replace("last project", names)
        if "recent file" in lowered:
            # no-op placeholder; could use recent files
            pass
        return instruction

    def plan(self, instruction: str, context: str = "") -> Dict[str, Any]:
        """Produce a full execution plan (interpreted_goal, assumptions, success_criteria,
        required_tools, execution_steps, dependencies, risk_level)."""
        interpretation = self.interpret(instruction, context)
        available_tools = registry.list_tools()
        plan = llm.plan(interpretation["interpreted_goal"],
                        context=context,
                        available_tools=available_tools)
        # Merge interpretation
        plan["interpreted_goal"] = interpretation["interpreted_goal"]
        plan["language"] = interpretation["language"]
        plan["assumptions"] = interpretation["assumptions"] + plan.get("assumptions", [])
        return plan


planner = Planner()
