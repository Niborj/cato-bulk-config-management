#!/usr/bin/env python3
"""Purge all sdp_static_ip_allocation objects from a TARGET tenant via catocli.

Thin wrapper over scripts/purge/engine.py -- dry-run by default, refuses
the <source-profile> source tenant, requires typed account-id confirmation to
--execute. See docs/bulk-apply-runbook.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from purge.engine import main_single  # noqa: E402

if __name__ == "__main__":
    sys.exit(main_single("sdp_static_ip_allocation"))
