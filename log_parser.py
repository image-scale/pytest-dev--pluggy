import re


def parse_log(log: str) -> dict[str, str]:
    """Parse test runner output into per-test results.

    Args:
        log: Full stdout+stderr output of `bash run_test.sh 2>&1`.

    Returns:
        Dict mapping test_id to status.
        - test_id: pytest native format (e.g. "testing/foo.py::TestClass::test_func[param]")
        - status: one of "PASSED", "FAILED", "SKIPPED", "ERROR"
    """
    # Strip ANSI escape codes
    log = re.sub(r'\x1b\[[0-9;]*m', '', log)

    results: dict[str, str] = {}

    # Match verbose pytest output lines like:
    # "testing/test_foo.py::test_bar PASSED             [  5%]"
    # "testing/test_foo.py::test_bar[param] FAILED      [ 50%]"
    inline_pattern = re.compile(
        r'^(\S.*?)\s+(PASSED|FAILED|SKIPPED|ERROR)\s+\[\s*\d+%\]',
        re.MULTILINE,
    )
    for m in inline_pattern.finditer(log):
        test_id = m.group(1).strip()
        status = m.group(2)
        results.setdefault(test_id, status)

    # Also catch collection errors: "ERROR tests/foo.py"
    collection_error_pattern = re.compile(
        r'^ERROR\s+(testing/\S+\.py)\s*$',
        re.MULTILINE,
    )
    for m in collection_error_pattern.finditer(log):
        results.setdefault(m.group(1), 'ERROR')

    return results

