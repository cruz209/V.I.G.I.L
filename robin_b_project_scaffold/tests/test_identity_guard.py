import re
from pathlib import Path

def test_core_identity_guard():
    txt = Path("sample_a_repo/agent.py").read_text(encoding="utf-8")
    core = re.search(r"(## BEGIN_CORE_IDENTITY)(.*?)(## END_CORE_IDENTITY)", txt, re.DOTALL).group(0)
    # simulate a rewrite that should NOT change core
    new = txt.replace("Operate with general proactivity and concise confirmations.", "TEMP")
    core_after = re.search(r"(## BEGIN_CORE_IDENTITY)(.*?)(## END_CORE_IDENTITY)", new, re.DOTALL).group(0)
    assert core == core_after
