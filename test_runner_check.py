"""Quick check of TaskRunner and CLI functionality."""
import sys
import os

# Add the repo to path
sys.path.insert(0, r"E:\matlab os")

from matlab_os.cli.task_runner import TaskRunner
import tempfile

# Use a temp database
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    db_path = f.name

try:
    print("=== TaskRunner Test ===")
    runner = TaskRunner(db_path=db_path)
    
    # Test submit
    print("\n1. Submitting task...")
    submission_id = runner.submit("Test task description")
    print(f"   Submission ID: {submission_id}")
    assert submission_id is not None and len(submission_id) > 0
    
    # Test status
    print("\n2. Checking status...")
    task = runner.status(submission_id)
    print(f"   Task status: {task['status']}")
    assert task is not None
    assert task["status"] == "pending"
    assert task["submission_id"] == submission_id
    
    # Test retrieve with result
    print("\n3. Updating task with result and retrieving...")
    runner.db.update_task_status(
        submission_id=submission_id,
        status="completed",
        result={"output": "hello world"},
        history_entry="task completed successfully",
    )
    result = runner.retrieve(submission_id)
    print(f"   Result: {result}")
    assert result is not None
    assert result["status"] == "completed"
    assert result["result"] == {"output": "hello world"}
    
    # Test history
    print("\n4. Checking history...")
    hist = runner.history(submission_id)
    print(f"   History: {hist}")
    assert isinstance(hist, list)
    assert len(hist) >= 1
    assert "task completed successfully" in hist[-1]
    
    # Test list tasks
    print("\n5. Listing all tasks...")
    tasks = runner.list_all()
    print(f"   Total tasks: {len(tasks)}")
    assert len(tasks) >= 1
    
    runner.close()
    print("\n=== All TaskRunner tests passed! ===")
    
    # Test CLI main (will exit after one menu cycle due to input)
    print("\n=== Testing CLI entry point ===")
    from matlab_os.cli.cli import main
    print("   CLI main function imports successfully")
    
finally:
    if os.path.exists(db_path):
        os.unlink(db_path)

print("\n=== All checks completed successfully! ===")