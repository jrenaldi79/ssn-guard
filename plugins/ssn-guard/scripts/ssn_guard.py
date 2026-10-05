#!/usr/bin/env python3
"""ssn-guard hook entry point for Claude Code and OpenAI Codex.

One script handles every hook event. It reads the event JSON on stdin and
writes the decision JSON on stdout.

  UserPromptSubmit  Blocks a prompt that contains an SSN, or that mentions
                    (@path) a file that contains one.
  PreToolUse        Bash: rewrites the command so its output goes through
                    the masker before anyone sees it. This also covers
                    commands that fail and long-running commands.
                    Read (Claude Code): checks PDFs and images, which go to
                    the model as raw bytes and cannot be masked.
  PostToolUse       Masks every string in the tool result.
                    Claude Code: returns updatedToolOutput (same shape).
                    Codex: returns decision "block" with the masked text,
                    which Codex shows to the model instead of the result.

If this script fails before a tool runs, it exits with code 2. Both tools
treat exit code 2 as "block", so an error never lets raw data through.
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ssn_mask import contains_ssn, mask_text, mask_value  # noqa: E402

WRAP_MARKER = "# ssn-guard:wrapped"
MASKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ssn_mask.py")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
TMP_PREFIX = "ssn-guard."


# ---------------------------------------------------------------- helpers

def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip().lower()


def flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def platform(data: dict) -> str:
    forced = env("SSN_GUARD_PLATFORM", "")
    if forced in ("claude", "codex"):
        return forced
    # Codex sets PLUGIN_ROOT for plugin hooks and sends turn_id with every
    # event. Claude Code does neither for these events.
    if os.environ.get("PLUGIN_ROOT") or "turn_id" in data:
        return "codex"
    return "claude"


def data_dir() -> str:
    for name in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA"):
        if os.environ.get(name):
            return os.environ[name]
    return os.path.join(os.path.expanduser("~"), ".ssn-guard")


def audit(event: str, tool: str, count: int, action: str, plat: str) -> None:
    """Write one line per detection. The line never holds the number itself."""
    if not flag("SSN_GUARD_AUDIT", True) or count == 0:
        return
    try:
        path = os.path.join(data_dir(), "audit.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {
            "time": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "platform": plat,
            "event": event,
            "tool": tool,
            "count": count,
            "action": action,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def emit(obj: dict) -> int:
    sys.stdout.write(json.dumps(obj))
    return 0


def notice(count: int) -> str:
    return (
        f"ssn-guard masked {count} value(s) that look like Social Security "
        "numbers in this tool result. Only the last four digits are shown "
        "(***-**-1234). The full numbers are not available."
    )


# ------------------------------------------------------- UserPromptSubmit

_AT_PATH = re.compile(r"(?:^|\s)@(\"[^\"]+\"|'[^']+'|\S+)")


def mentioned_files(prompt: str, cwd: str) -> list[str]:
    found = []
    for raw in _AT_PATH.findall(prompt):
        path = raw.strip("\"'").split("#", 1)[0].rstrip(",.;:)")
        if not path:
            continue
        full = os.path.expanduser(path)
        if not os.path.isabs(full):
            full = os.path.join(cwd, full)
        if os.path.isfile(full):
            found.append(full)
    return found


def file_has_ssn(path: str, limit: int = 20_000_000) -> bool | None:
    """True or False for text files. None when the file is not readable text."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text = pdf_text(path)
        return None if text is None else contains_ssn(text)
    if ext in IMAGE_EXT:
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return contains_ssn(raw.decode("utf-8", errors="replace"))


def on_prompt(data: dict, plat: str) -> int:
    if not flag("SSN_GUARD_BLOCK_PROMPTS", True):
        return 0
    prompt = data.get("prompt") or ""
    count = mask_text(prompt)[1]
    reason = None
    if count:
        reason = (
            "ssn-guard stopped this message. It contains what looks like a "
            "Social Security number, so it was not sent to the model. Remove "
            "the number or mask it (for example ***-**-1234), then send again."
        )
    else:
        for path in mentioned_files(prompt, data.get("cwd") or os.getcwd()):
            if file_has_ssn(path):
                count = 1
                reason = (
                    f"ssn-guard stopped this message. The file {os.path.basename(path)} "
                    "contains what looks like a Social Security number, and a file "
                    "attached with @ goes to the model without masking. Remove the @ "
                    "and ask the assistant to read the file instead. Its tools mask "
                    "the numbers."
                )
                break
    if not reason:
        return 0
    audit("UserPromptSubmit", "", count, "blocked", plat)
    out = {"decision": "block", "reason": reason}
    if plat == "claude":
        out["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "suppressOriginalPrompt": True,
        }
    return emit(out)


# ------------------------------------------------------------- PreToolUse

def cleanup_stale_temp_files() -> None:
    """Remove output files left behind by commands that called exit."""
    cutoff = time.time() - 15 * 60
    for path in glob.glob(os.path.join(tempfile.gettempdir(), TMP_PREFIX + "*")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def wrap_command(command: str, shell: str | None = None) -> str:
    if os.name == "nt":
        if shell and os.path.basename(shell).lower() not in (
            "powershell", "powershell.exe", "pwsh", "pwsh.exe"
        ):
            raise ValueError("unsupported Windows shell; command withheld")
        from windows_command import wrap_windows_command
        return wrap_windows_command(command)

    """Run the command in the current shell, catch all output in a private
    temp file, then print it through the masker. The exit code stays the same.

    - Braces (not a subshell) keep `cd` and exported variables working.
    - File descriptor 8 keeps the real stdout, so the masked output gets out
      even when the command calls `exit` inside the redirected block.
    - An EXIT trap prints the masked output when the command calls `exit`
      or stops under `set -e`.
    """
    py = shlex.quote(sys.executable or "python3")
    masker = shlex.quote(MASKER)
    tmpdir = shlex.quote(tempfile.gettempdir())
    return (
        f"{WRAP_MARKER}\n"
        f"__ssng_f=$(umask 077; mktemp {tmpdir}/{TMP_PREFIX}XXXXXX) || "
        "{ echo 'ssn-guard: cannot create a temp file; command not run' >&2; exit 1; }\n"
        "exec 8>&1\n"
        "__ssng_flush() {\n"
        '  if [ -n "${__ssng_f:-}" ] && [ -f "$__ssng_f" ]; then\n'
        f'    {py} {masker} <"$__ssng_f" >&8 || echo \'ssn-guard: masking failed; output withheld\' >&8\n'
        '    rm -f "$__ssng_f"\n'
        "  fi\n"
        "}\n"
        "trap '__ssng_rc=$?; __ssng_flush; exit $__ssng_rc' EXIT\n"
        "{\n"
        f"{command}\n"
        '} >"$__ssng_f" 2>&1\n'
        "__ssng_rc=$?\n"
        "trap - EXIT\n"
        "__ssng_flush\n"
        "exec 8>&-\n"
        "unset -f __ssng_flush\n"
        "(exit $__ssng_rc)"
    )


def pdf_text(path: str) -> str | None:
    if shutil.which("pdftotext"):
        try:
            result = subprocess.run(
                ["pdftotext", "-q", "-layout", path, "-"],
                capture_output=True, timeout=60,
            )
            text = result.stdout.decode("utf-8", errors="replace")
            return text if text.strip() else None  # no text layer: scanned
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        from pypdf import PdfReader  # type: ignore

        text = "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)
        return text if text.strip() else None
    except Exception:
        return None


def pre_decision(plat: str, decision: str, reason: str, updated=None) -> dict:
    spec = {"hookEventName": "PreToolUse", "permissionDecision": decision,
            "permissionDecisionReason": reason}
    if updated is not None:
        spec["updatedInput"] = updated
    return {"hookSpecificOutput": spec}


def on_pre_tool(data: dict, plat: str) -> int:
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}

    if tool == "Bash" and flag("SSN_GUARD_WRAP_BASH", True):
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip() or WRAP_MARKER in command:
            return 0
        cleanup_stale_temp_files()
        updated = dict(tool_input)
        updated["command"] = wrap_command(command, tool_input.get("shell"))
        if plat == "codex":
            # Codex applies updatedInput only with "allow". In Codex this
            # value only carries the new input; the sandbox and approval
            # policy still apply to the command.
            return emit(pre_decision(plat, "allow", "ssn-guard: output is masked", updated))
        # Claude Code: no permissionDecision, so the normal permission
        # prompt and rules still apply to the command.
        return emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}})

    if tool == "Read" and plat == "claude":
        path = tool_input.get("file_path") or ""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            text = pdf_text(path)
            if text is not None and contains_ssn(text):
                audit("PreToolUse", tool, 1, "denied-pdf", plat)
                return emit(pre_decision(plat, "deny", (
                    "ssn-guard blocked this read. The PDF contains text that looks like a "
                    "Social Security number, and a PDF goes to the model as raw pages that "
                    "cannot be masked. To see the text with the numbers masked, run "
                    f"`pdftotext -layout {shlex.quote(path)} -` with the Bash tool."
                )))
            if text is not None:
                return 0  # text layer found, no SSN
            return unscannable(plat, path, "PDF has no text layer that can be checked (for example a scan)")
        if ext in IMAGE_EXT:
            return unscannable(plat, path, "images cannot be checked for Social Security numbers")
    return 0


def unscannable(plat: str, path: str, why: str) -> int:
    policy = env("SSN_GUARD_UNSCANNABLE", "ask")
    if policy == "allow":
        return 0
    reason = f"ssn-guard: {os.path.basename(path)}: {why}. It may show a Social Security number to the model."
    if policy == "deny":
        audit("PreToolUse", "Read", 1, "denied-unscannable", plat)
        return emit(pre_decision(plat, "deny", reason))
    return emit(pre_decision(plat, "ask", reason + " Allow this read?"))


# ------------------------------------------------------------ PostToolUse

def on_post_tool(data: dict, plat: str) -> int:
    tool = data.get("tool_name") or ""
    response = data.get("tool_response")
    if response is None:
        return 0
    is_mcp = tool.startswith("mcp__")

    if plat == "codex":
        # Codex cannot replace a result in place. "block" with a reason
        # makes Codex show the reason to the model instead of the result.
        if isinstance(response, str):
            masked, count = mask_text(response)
            text = masked
        else:
            masked, count = mask_value(response, mask_numbers=True)
            text = json.dumps(masked, ensure_ascii=False, indent=1)
        if not count:
            return 0
        audit("PostToolUse", tool, count, "masked", plat)
        return emit({"decision": "block", "reason": notice(count) + "\n\n" + text})

    masked, count = mask_value(response, mask_numbers=is_mcp)
    if not count:
        return 0
    audit("PostToolUse", tool, count, "masked", plat)
    return emit({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": masked,
            "additionalContext": notice(count),
        }
    })


# ------------------------------------------------------------------- main

def main() -> int:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    plat = platform(data)
    event = data.get("hook_event_name") or ""
    if event == "UserPromptSubmit":
        return on_prompt(data, plat)
    if event == "PreToolUse":
        return on_pre_tool(data, plat)
    if event == "PostToolUse":
        return on_post_tool(data, plat)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed
        sys.stderr.write(f"ssn-guard hook error ({type(exc).__name__}: {exc}); blocked for safety.\n")
        sys.exit(2)
