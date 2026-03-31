import json
import os
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from mcp.server.fastmcp import FastMCP

# ============================================================================
# HELPER UTILITIES FOR AGENT-ASSISTED TASK DECOMPOSITION
# These functions help agents split large tasks into batch-friendly chunks.
# ============================================================================


def chunk_by_count(items: list, n: int) -> list[list]:
    """
    Split a list into n approximately equal-sized chunks.

    Use this when you have many items and want to distribute them across
    parallel workers. Each chunk will be processed independently.

    Args:
        items: List of items to split (e.g., file paths, data rows)
        n: Number of chunks to create (typically max_workers=4)

    Returns:
        List of n sublists containing the original items

    Example:
        >>> chunk_by_count(['file1.py', 'file2.py', 'file3.py', 'file4.py'], 2)
        [['file1.py', 'file2.py'], ['file3.py', 'file4.py']]

        # Use with run_python_batch:
        chunks = chunk_by_count(files, 4)
        codes = [f"process_files({chunk})" for chunk in chunks]
        run_python_batch(codes)
    """
    if n <= 0:
        return [items]
    if n >= len(items):
        return [[item] for item in items]

    chunk_size = len(items) // n
    remainder = len(items) % n

    chunks = []
    start = 0
    for i in range(n):
        end = start + chunk_size + (1 if i < remainder else 0)
        chunks.append(items[start:end])
        start = end

    return chunks


def chunk_by_size(code: str, max_bytes: int = 500000) -> list[str]:
    """
    Split a large code string into smaller chunks based on byte size.

    Use this when you have a very large script that exceeds the 1MB limit.
    Each chunk can be executed independently if the code is modular.

    Args:
        code: Complete code string to split
        max_bytes: Maximum bytes per chunk (default 500KB)

    Returns:
        List of code chunks, each under max_bytes size

    Example:
        >>> large_code = "process_data('file1')\\nprocess_data('file2')\\n..."
        >>> chunks = chunk_by_size(large_code, 500000)
        >>> run_python_batch(chunks)
    """
    if len(code.encode("utf-8")) <= max_bytes:
        return [code]

    lines = code.split("\n")
    chunks = []
    current_chunk = ""

    for line in lines:
        test_chunk = current_chunk + line + "\n"
        if len(test_chunk.encode("utf-8")) > max_bytes:
            if current_chunk:
                chunks.append(current_chunk.rstrip())
            current_chunk = line + "\n"
        else:
            current_chunk = test_chunk

    if current_chunk:
        chunks.append(current_chunk.rstrip())

    return chunks


def suggest_batch_size(total_items: int, complexity: str = "medium") -> int:
    """
    Suggest an optimal batch size based on total items and task complexity.

    Use this to determine how many parallel workers to use for your task.

    Args:
        total_items: Total number of items to process
        complexity: Task complexity level:
            - "low": Simple operations (e.g., file size checks) → max 4 workers
            - "medium": Moderate operations (e.g., data parsing) → 3 workers
            - "high": Heavy operations (e.g., ML inference) → 2 workers

    Returns:
        Recommended number of parallel workers (1-4)

    Example:
        >>> files = list_of_100_files
        >>> workers = suggest_batch_size(len(files), "low")
        >>> chunks = chunk_by_count(files, workers)
        >>> codes = [f"analyze_files({chunk})" for chunk in chunks]
        >>> run_python_batch(codes, max_workers=workers)
    """
    complexity_factors = {
        "low": 4,  # Max parallelism for simple tasks
        "medium": 3,  # Moderate parallelism
        "high": 2,  # Limited parallelism for heavy tasks
    }
    max_workers = min(4, complexity_factors.get(complexity, 3))
    return min(max_workers, total_items)


# ============================================================================
# MCP SERVER INITIALIZATION
# ============================================================================

# [ARCHITECT]: Initializing Python Executor server to bypass terminal quoting shell-hell.
mcp = FastMCP("Python Executor")


@mcp.tool()
def run_python(code: str, timeout: int = 30, cwd: str | None = None):
    """
    Execute a single Python code snippet. Use for stateful/dependent operations.
    Use run_python_batch for parallel independent tasks. Max 1MB code, 60s timeout.
    See README.md for usage examples and helper utilities.
    """
    # Safety: clamp timeout
    actual_timeout = min(max(1, timeout), 60)

    # Safety: Limit code size to 1MB to prevent disk exhaustion
    max_code_size = 1024 * 1024  # 1MB
    if len(code) > max_code_size:
        return json.dumps(
            {
                "error": f"Code size exceeds {max_code_size} byte limit",
                "success": False,
            },
            indent=2,
            ensure_ascii=False,
        )

    # Create a temporary file to hold the code
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    # Dynamic Interpreter Detection
    target_cwd = cwd if cwd and os.path.exists(cwd) else os.getcwd()
    # Check for Windows .venv structure (Junction mode friendly)
    local_venv_python = os.path.join(target_cwd, ".venv", "Scripts", "python.exe")
    if not os.path.exists(local_venv_python):
        # Fallback for Linux/Mac or alternate structures
        local_venv_python = os.path.join(target_cwd, ".venv", "bin", "python")

    executable = local_venv_python if os.path.exists(local_venv_python) else sys.executable

    output: dict[str, Any] = {}
    try:
        # shell=False is CRITICAL here to avoid quoting hell
        result = subprocess.run(
            [executable, "-u", tmp_path],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=actual_timeout,
            encoding="utf-8",
            errors="replace",
            cwd=target_cwd,
        )

        output = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
            "executable": executable,  # Added for debug visibility
        }

    except subprocess.TimeoutExpired as e:
        output = {
            "stdout": e.stdout if e.stdout else "",
            "stderr": (
                f"Error: Process timed out after {actual_timeout}s\n"
                f"Command: {[executable, '-u', tmp_path]}\n"
                f"{e.stderr if e.stderr else ''}"
            ),
            "exit_code": -1,
            "success": False,
            "executable": executable,
        }
    except Exception as e:
        output = {
            "stdout": "",
            "stderr": f"Exception during execution: {str(e)}",
            "exit_code": -2,
            "success": False,
        }
    finally:
        # Cleanup: Remove the temporary file
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                # Log but don't fail execution if cleanup fails
                error_msg = f"\nWarning: Failed to delete temp file {tmp_path}: {e}"
                if isinstance(output, dict):
                    output["stderr"] = output.get("stderr", "") + error_msg

    return json.dumps(output, indent=2, ensure_ascii=False)


def _execute_single_snippet(index: int, code: str, timeout: int, cwd: str | None) -> dict[str, Any]:
    """
    Worker function to execute a single Python snippet with its index.
    Uses UUID-named temp files to prevent collisions in parallel execution.
    Returns result dict with index for order preservation.
    """
    # Safety: clamp timeout
    actual_timeout = min(max(1, timeout), 60)

    # Create a UUID-named temporary file to prevent collisions
    unique_filename = f"mcp_py_{uuid.uuid4().hex}.py"
    tmp_path = os.path.join(tempfile.gettempdir(), unique_filename)

    # Dynamic Interpreter Detection
    target_cwd = cwd if cwd and os.path.exists(cwd) else os.getcwd()
    local_venv_python = os.path.join(target_cwd, ".venv", "Scripts", "python.exe")
    if not os.path.exists(local_venv_python):
        local_venv_python = os.path.join(target_cwd, ".venv", "bin", "python")
    executable = local_venv_python if os.path.exists(local_venv_python) else sys.executable

    output: dict[str, Any] = {}
    try:
        # Write code to the temporary file
        with open(tmp_path, "w", encoding="utf-8") as tmp:
            tmp.write(code)

        # Execute the code
        result = subprocess.run(
            [executable, "-u", tmp_path],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=actual_timeout,
            encoding="utf-8",
            errors="replace",
            cwd=target_cwd,
        )

        output = {
            "index": index,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
            "executable": executable,
        }

    except subprocess.TimeoutExpired as e:
        output = {
            "index": index,
            "stdout": e.stdout if e.stdout else "",
            "stderr": (
                f"Error: Process timed out after {actual_timeout}s\n{e.stderr if e.stderr else ''}"
            ),
            "exit_code": -1,
            "success": False,
            "executable": executable,
        }
    except Exception as e:
        output = {
            "index": index,
            "stdout": "",
            "stderr": f"Exception during execution: {str(e)}",
            "exit_code": -2,
            "success": False,
        }
    finally:
        # Cleanup: Remove the temporary file
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass  # Don't fail execution if cleanup fails

    return output


@mcp.tool()
def run_python_batch(
    codes: list[str],
    timeout: int = 30,
    max_workers: int = 4,
    cwd: str | None = None,
) -> str:
    """
    Execute multiple Python snippets in parallel (max 4 workers, max 20 snippets).
    Use for independent tasks: file processing, parallel data analysis.
    Use run_python for stateful/dependent operations. See README.md for examples.
    """
    # Validate inputs
    if not isinstance(codes, list):
        return json.dumps(
            {"error": "codes must be a list of strings", "results": []},
            indent=2,
            ensure_ascii=False,
        )

    if len(codes) == 0:
        return json.dumps({"results": []}, indent=2, ensure_ascii=False)

    # Safety: Limit batch size to prevent memory exhaustion
    max_batch_size = 20
    if len(codes) > max_batch_size:
        return json.dumps(
            {
                "error": f"Batch size exceeds limit of {max_batch_size} snippets",
                "results": [],
            },
            indent=2,
            ensure_ascii=False,
        )

    # Safety: Validate all items are strings
    if not all(isinstance(c, str) for c in codes):
        return json.dumps(
            {"error": "All items in codes list must be strings", "results": []},
            indent=2,
            ensure_ascii=False,
        )

    # Safety: Limit individual code size to 1MB to prevent disk exhaustion
    max_code_size = 1024 * 1024  # 1MB
    for i, code in enumerate(codes):
        if len(code) > max_code_size:
            return json.dumps(
                {
                    "error": (f"Code snippet at index {i} exceeds {max_code_size} byte limit"),
                    "results": [],
                },
                indent=2,
                ensure_ascii=False,
            )

    # Clamp parameters for safety
    actual_max_workers = min(max(1, max_workers), 4)  # Hard cap at 4
    actual_timeout = min(max(1, timeout), 60)  # Max 60s per snippet

    # Pre-allocate results list to preserve order
    results: list[dict[str, Any] | None] = [None] * len(codes)

    try:
        # Execute snippets in parallel using ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=actual_max_workers) as executor:
            # Submit all tasks with their index for order tracking
            futures = {
                executor.submit(_execute_single_snippet, i, code, actual_timeout, cwd): i
                for i, code in enumerate(codes)
            }

            # Collect results as they complete, preserving order
            for future in as_completed(futures):
                result = future.result()
                index = result["index"]
                results[index] = {
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "exit_code": result["exit_code"],
                    "success": result["success"],
                    "executable": result.get("executable"),
                }

    except Exception as e:
        # Handle batch-level failures
        return json.dumps(
            {
                "error": f"Batch execution failed: {str(e)}",
                "results": [
                    {
                        "stdout": "",
                        "stderr": f"Batch execution failed: {str(e)}",
                        "exit_code": -3,
                        "success": False,
                    }
                    for _ in codes
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    return json.dumps({"results": results}, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
