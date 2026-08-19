"""
run.py — Entry point.

Usage:
    python run.py --setup      # one-time questionnaire
    python run.py              # run the pipeline (search -> rank -> finish -> report)
    python run.py --track job  # override track for this run only
"""

import argparse
import logging
import os
import sys
import webbrowser
from datetime import datetime

from config import LOG_FILE
from nodes.questionnaire import run_questionnaire_cli, load_profile
from nodes.cv_import import import_cv
from nodes.db import init_db
from graph import run_pipeline

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Agentic outreach pipeline")
    parser.add_argument("--import-cv", action="store_true",
                         help="Import resume PDF from input/ into cv_base.tex + fingerprint")
    parser.add_argument("--setup", action="store_true", help="Run one-time questionnaire")
    parser.add_argument("--track", choices=["professor", "job"], help="Override track for this run")
    args = parser.parse_args()

    if args.import_cv:
        print("\nImporting CV from input/...\n")
        success, message = import_cv()
        print(message)
        if not success:
            sys.exit(1)
        return

    if args.setup:
        run_questionnaire_cli()
        return

    init_db()

    try:
        profile = load_profile()
    except FileNotFoundError as e:
        print(f"\n{e}\n")
        return

    track = args.track or profile.get("track", "both")
    if track == "both":
        track = "job"  # default when both configured; use --track to target professor explicitly
        log.info("Profile track is 'both' — defaulting this run to 'job'. Use --track professor to target outreach.")

    log.info("=" * 60)
    log.info(f"Agentic Outreach Pipeline — {datetime.now().strftime('%d %b %Y %H:%M')} — track={track}")
    log.info("=" * 60)

    final_state = run_pipeline(profile, track)

    log.info(f"\nStop reason: {final_state['stop_reason']}")
    log.info(f"Finished drafts: {len(final_state['finished'])}")
    log.info(f"Report: {os.path.abspath(final_state['report_path'])}")

    webbrowser.open(f"file:///{os.path.abspath(final_state['report_path']).replace(os.sep,'/')}")

    print("\n" + "=" * 60)
    print(f"  {len(final_state['finished'])} drafts ready — {final_state['stop_reason']}")
    print(f"  Report: {os.path.abspath(final_state['report_path'])}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
