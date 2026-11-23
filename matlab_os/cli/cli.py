"""V1 CLI for Local AI OS.

Provides an interactive interface for:
1. Submit a task.
2. Check task status.
3. Retrieve task results.
4. View task history.
5. Exit.

Uses TaskRunner with DatabaseService for persistence across invocations.
"""

import sys
from matlab_os.cli.task_runner import TaskRunner


def main():
    """Run the V1 CLI interactive menu."""
    runner = TaskRunner()
    try:
        while True:
            print("\nLocal AI OS V1 - Menu")
            print("1. Submit a task")
            print("2. Check task status")
            print("3. Retrieve task results")
            print("4. View task history")
            print("5. Exit")
            choice = input("Enter choice [1-5]: ").strip()

            if choice == "1":
                description = input("Enter task description: ").strip()
                if not description:
                    print("Error: Task description cannot be empty.")
                    continue
                submission_id = runner.submit(description)
                print(f"Task submitted. Submission ID: {submission_id}")

            elif choice == "2":
                submission_id = input("Enter submission ID to check status: ").strip()
                task = runner.status(submission_id)
                if task is None:
                    print(f"Error: No task found with submission ID '{submission_id}'.")
                else:
                    print(f"Status: {task['status']}")
                    if task.get("result") is not None:
                        print(f"Result: {task['result']}")
                    print(f"Created at: {task.get('created_at')}")

            elif choice == "3":
                submission_id = input("Enter submission ID to retrieve results: ").strip()
                result = runner.retrieve(submission_id)
                if result is None:
                    print(f"Error: No task found with submission ID '{submission_id}'.")
                else:
                    print(f"Status: {result.get('status')}")
                    print(f"Result: {result.get('result')}")

            elif choice == "4":
                submission_id = input("Enter submission ID to view history: ").strip()
                hist = runner.history(submission_id)
                if not hist:
                    print(f"No history found for submission ID '{submission_id}'.")
                else:
                    print(f"Task history for '{submission_id}':")
                    for i, entry in enumerate(hist, 1):
                        print(f"  {i}. {entry}")

            elif choice == "5":
                print("Exiting Local AI OS V1. Goodbye!")
                break

            else:
                print("Error: Invalid choice. Please enter 1-5.")
    finally:
        runner.close()


if __name__ == "__main__":
    main()