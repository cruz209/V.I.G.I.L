"""
validate_diffs.py
==================
Extracts Python code from VIGIL-generated unified diffs and validates:
  1. Syntactic validity via ast.parse()
  2. Import success in a clean subprocess environment

Run from anywhere:
    python validate_diffs.py --results-dir "C:\path\to\swe_vigil_results" --n 20
    python validate_diffs.py --results-dir "C:\path\to\swe_vigil_results" --n 100

Output:
    validate_results.json   — per-instance breakdown
    validate_summary.txt    — paper-ready numbers
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── Diff parser ─────────────────────────────────────────────────────────────

def extract_added_python(diff_text: str) -> str:
    """
    Extract all lines added by the diff (lines starting with '+' but not '+++').
    Returns them as a single Python source string.
    """
    lines = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])  # strip leading '+'
    return "\n".join(lines)


def find_diff_file(instance_dir: Path) -> Optional[Path]:
    """Find the LLM-generated .diff file inside an instance directory."""
    proposals = instance_dir / "vigil_output" / "output" / "proposals"
    if not proposals.exists():
        return None
    diffs = sorted(proposals.glob("*.diff"))
    return diffs[0] if diffs else None


# ─── Validators ───────────────────────────────────────────────────────────────

def check_syntax(code: str) -> Tuple[bool, Optional[str]]:
    """Returns (is_valid, error_message)."""
    if not code.strip():
        return False, "empty code"
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"
    except Exception as e:
        return False, str(e)


def check_imports(code: str, timeout: int = 10) -> Tuple[bool, Optional[str]]:
    """
    Write code to a temp file and attempt to import it in a subprocess.
    Returns (success, error_message).
    """
    if not code.strip():
        return False, "empty code"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, "-c",
             f"import ast; ast.parse(open(r'{tmp_path}').read()); "
             f"exec(compile(open(r'{tmp_path}').read(), r'{tmp_path}', 'exec'))"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, None
        else:
            # Extract meaningful error
            err = result.stderr.strip().splitlines()
            short = err[-1] if err else "unknown error"
            return False, short
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ─── Main validator ───────────────────────────────────────────────────────────

def validate_instance(instance_dir: Path) -> Dict:
    result = {
        "instance": instance_dir.name,
        "diff_found": False,
        "diff_lines": 0,
        "code_lines": 0,
        "syntax_valid": False,
        "syntax_error": None,
        "import_success": False,
        "import_error": None,
        "diff_has_hunks": False,
    }

    diff_path = find_diff_file(instance_dir)
    if diff_path is None:
        result["import_error"] = "no diff file found"
        return result

    result["diff_found"] = True
    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    result["diff_lines"] = len(diff_text.splitlines())
    result["diff_has_hunks"] = "@@" in diff_text

    code = extract_added_python(diff_text)
    result["code_lines"] = len([l for l in code.splitlines() if l.strip()])

    if not code.strip():
        result["syntax_error"] = "no added Python lines in diff"
        return result

    # Syntax check
    syntax_ok, syntax_err = check_syntax(code)
    result["syntax_valid"] = syntax_ok
    result["syntax_error"] = syntax_err

    # Import/exec check (only if syntax passes)
    if syntax_ok:
        import_ok, import_err = check_imports(code)
        result["import_success"] = import_ok
        result["import_error"] = import_err

    return result


def run_validation(results_dir: Path, n: int = 20) -> List[Dict]:
    instance_dirs = sorted([
        d for d in results_dir.iterdir()
        if d.is_dir()
    ])[:n]

    print(f"Validating {len(instance_dirs)} instances from {results_dir}")
    print(f"{'Instance':<50} {'Diff':>5} {'Syntax':>7} {'Import':>7}")
    print("-" * 75)

    results = []
    for i, inst_dir in enumerate(instance_dirs):
        r = validate_instance(inst_dir)
        results.append(r)

        diff_ok = "✓" if r["diff_found"] else "✗"
        syn_ok  = "✓" if r["syntax_valid"] else "✗"
        imp_ok  = "✓" if r["import_success"] else ("─" if not r["syntax_valid"] else "✗")
        name    = inst_dir.name[:48]
        print(f"  {name:<48} {diff_ok:>5}  {syn_ok:>6}  {imp_ok:>6}")

    return results


def print_summary(results: List[Dict], out_path: Path):
    n = len(results)
    diff_found     = sum(1 for r in results if r["diff_found"])
    has_hunks      = sum(1 for r in results if r["diff_has_hunks"])
    syntax_valid   = sum(1 for r in results if r["syntax_valid"])
    import_success = sum(1 for r in results if r["import_success"])

    # Error breakdown
    syntax_errors = {}
    for r in results:
        if r["syntax_error"] and not r["syntax_valid"]:
            key = r["syntax_error"].split(":")[0] if r["syntax_error"] else "unknown"
            syntax_errors[key] = syntax_errors.get(key, 0) + 1

    import_errors = {}
    for r in results:
        if r["syntax_valid"] and r["import_error"]:
            key = r["import_error"].split(":")[0] if r["import_error"] else "unknown"
            import_errors[key] = import_errors.get(key, 0) + 1

    summary = f"""
VIGIL Diff Execution Validation
================================
Instances evaluated:     {n}
Diff files found:        {diff_found}/{n} ({diff_found/n*100:.1f}%)
Diffs with hunk markers: {has_hunks}/{n} ({has_hunks/n*100:.1f}%)

SYNTAX VALIDITY
  Valid Python syntax:   {syntax_valid}/{n} ({syntax_valid/n*100:.1f}%)
  Invalid:               {n-syntax_valid}/{n} ({(n-syntax_valid)/n*100:.1f}%)

IMPORT / EXEC SUCCESS (of syntax-valid diffs)
  Successful exec:       {import_success}/{syntax_valid if syntax_valid else 1} ({import_success/max(syntax_valid,1)*100:.1f}% of syntax-valid)
  Overall exec rate:     {import_success}/{n} ({import_success/n*100:.1f}% of all instances)

PAPER-READY NUMBERS
  "{syntax_valid}/{n} ({syntax_valid/n*100:.0f}%) of VIGIL-generated diffs contain syntactically valid Python."
  "{import_success}/{n} ({import_success/n*100:.0f}%) execute successfully in a clean environment."

Syntax error breakdown:
{chr(10).join(f"  {k}: {v}" for k, v in sorted(syntax_errors.items(), key=lambda x: -x[1]))}

Import error breakdown:
{chr(10).join(f"  {k}: {v}" for k, v in sorted(import_errors.items(), key=lambda x: -x[1]))}
"""
    print(summary)
    out_path.write_text(summary, encoding="utf-8")
    print(f"Summary written to: {out_path}")
    return summary


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validate VIGIL diff correctness")
    parser.add_argument("--results-dir", required=True,
                        help="Path to swe_vigil_results directory")
    parser.add_argument("--n", type=int, default=20,
                        help="Number of instances to validate (default: 20)")
    parser.add_argument("--out", type=str, default="validate_results.json")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        print(f"ERROR: {results_dir} does not exist")
        sys.exit(1)

    results = run_validation(results_dir, args.n)

    # Save raw results
    out_json = Path(args.out)
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nRaw results written to: {out_json}")

    # Print and save summary
    summary_path = out_json.with_suffix(".txt")
    print_summary(results, summary_path)


if __name__ == "__main__":
    main()