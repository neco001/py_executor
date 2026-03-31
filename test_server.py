"""Test script for CI/CD - avoids Windows multiprocessing import issues."""

import subprocess
import sys


def test_single_execution():
    """Test single Python execution via subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", "print('Hello')"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "Hello" in result.stdout
    print("✅ Single execution test passed")


def test_batch_execution():
    """Test batch execution via subprocess calls."""
    codes = ["print(1)", "print(2)", "print(3)"]
    results = []
    for code in codes:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        results.append(result)

    assert all(r.returncode == 0 for r in results)
    assert "1" in results[0].stdout
    assert "2" in results[1].stdout
    assert "3" in results[2].stdout
    print("✅ Batch execution test passed")


def test_server_import():
    """Test that server module can be imported."""
    import server

    assert hasattr(server, "run_python")
    assert hasattr(server, "run_python_batch")
    assert hasattr(server, "chunk_by_count")
    assert hasattr(server, "chunk_by_size")
    assert hasattr(server, "suggest_batch_size")
    print("✅ Server import test passed")


if __name__ == "__main__":
    test_single_execution()
    test_batch_execution()
    test_server_import()
    print("\n🎉 All tests passed!")