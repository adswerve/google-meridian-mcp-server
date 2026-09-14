"""Guard: `import meridian` must resolve to the INSTALLED package, not the
vendored reference clone, and its version must satisfy the pin.

Run in a SUBPROCESS on purpose. pytest collects tests/contract before
tests/integration and tests/unit, and importing meridian here would pull in
jax, whose __init__ runs os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '1')
-- making this guard a second polluter of exactly the kind
execution/base_subprocess.py now defends against. Same pattern as
tests/contract/test_server_meridian_free.py.

The footgun this catches was hit for real during canary work: with the
shell's cwd inside references/meridian, `import meridian` resolved to the
vendored 1.7.0 clone.

Scope: references/ is gitignored, so a git worktree does not contain it and
this test passes trivially there. It fires in the main checkout and in any
environment that clones the reference tree.
"""

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent("""
    import importlib.metadata
    import pathlib
    import meridian

    location = pathlib.Path(meridian.__file__).resolve()
    assert "references" not in location.parts, (
        "meridian resolved to the vendored reference clone at "
        f"{location} -- the installed package is being shadowed"
    )

    version = importlib.metadata.version("google-meridian")
    major = int(version.split(".")[0])
    assert major == 2, f"google-meridian {version} does not satisfy >=2.0,<3"
    print("OK", version, location)
""")


def test_installed_meridian_is_not_the_reference_clone_and_matches_the_pin():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stderr
