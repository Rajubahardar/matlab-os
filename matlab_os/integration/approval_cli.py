"""Human-facing CLI for MATLAB mutation approvals.

Run this in your own terminal.  The agent can create requests, but approving
requires this process, the signing key, and an exact typed confirmation:

    python -m matlab_os.integration.approval_cli init
    python -m matlab_os.integration.approval_cli pending
    python -m matlab_os.integration.approval_cli show <token_id>
    python -m matlab_os.integration.approval_cli approve <token_id> --as "Your Name"
    python -m matlab_os.integration.approval_cli reject <token_id> --as "Your Name"
"""

from __future__ import annotations

import argparse
import json
import sys

from matlab_os.integration.approval_surface import (
    ApprovalSurface,
    ApprovalSurfaceError,
    CONFIRMATION_TEMPLATE,
    create_key_file,
)


def _surface(args: argparse.Namespace) -> ApprovalSurface:
    return ApprovalSurface(
        queue_dir=args.queue or None,
        key_file=args.key_file or None,
    )


def _print_descriptor(record: dict) -> None:
    descriptor = record["descriptor"]
    print(f"token_id   : {record['token_id']}")
    print(f"status     : {record['status']}")
    print(f"operation  : {record['operation']}")
    print(f"tool       : {descriptor['tool']}")
    print(f"target     : {descriptor['target']}")
    print(f"actor      : {descriptor['actor']}")
    print(f"request_id : {descriptor['request_id']}")
    print(f"digest     : {descriptor['digest']}")
    print(f"arguments  : {json.dumps(descriptor['arguments'], indent=2, sort_keys=True)}")
    print(f"expires_at : {record['expires_at']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MATLAB mutation approval console")
    parser.add_argument("--queue", help="queue directory")
    parser.add_argument("--key-file", help="signing key path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the signing key (human only)")

    p_pending = sub.add_parser("pending", help="list pending requests")
    p_pending.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="show one request in full")
    p_show.add_argument("token_id")

    for name, help_text in (("approve", "approve a request"), ("reject", "reject a request")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("token_id")
        p.add_argument("--as", dest="approver", required=True, help="your name")

    args = parser.parse_args(argv)

    if args.command == "init":
        try:
            path = create_key_file(args.key_file or None)
        except ApprovalSurfaceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"key created at {path}")
        print("Keep this file out of the repository and out of the agent's reach.")
        return 0

    try:
        surface = _surface(args)
    except ApprovalSurfaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "pending":
        pending = surface.pending()
        if args.json:
            print(json.dumps(pending, indent=2))
            return 0
        if not pending:
            print("No pending approvals.")
            return 0
        for record in pending:
            descriptor = record["descriptor"]
            print(
                f"{record['token_id']}  {record['operation']:<14} "
                f"{descriptor['tool']:<26} {descriptor['target']}"
            )
        return 0

    if args.command == "show":
        for record in surface.pending():
            if record["token_id"] == args.token_id:
                _print_descriptor(record)
                return 0
        print("Request not found or no longer pending.", file=sys.stderr)
        return 1

    approve = args.command == "approve"
    confirmation = ""
    if approve:
        expected = CONFIRMATION_TEMPLATE.format(token_id=args.token_id)
        print(f"Review the operation, then type exactly: {expected}")
        try:
            confirmation = input("confirmation> ").strip()
        except EOFError:
            print("No confirmation provided.", file=sys.stderr)
            return 1

    try:
        result = surface.decide(
            args.token_id,
            approve=approve,
            approver=args.approver,
            confirmation=confirmation,
        )
    except ApprovalSurfaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
