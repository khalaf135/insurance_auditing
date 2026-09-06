#!/usr/bin/env python3
"""Main command: test, audit, and export. Cached AI only unless --execute-ai is set."""
import argparse
from datetime import datetime
import hashlib
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_client import save
from reporting import (
    all_record_export, portable_csv_export, export_results, report_results,
    compare_main, verify_main,
)



SOURCE_FILES = ('main.py', 'pipeline.py', 'agents.py', 'rules.py', 'matching.py', 'contracts.py', 'contract_builder.py', 'ai_client.py', 'confidence.py', 'reporting.py', 'test.py', 'complete_workflow.py', 'reference_experiment.py', 'unit_quantity_experiment.py', 'history_quantity_experiment.py', 'merge_h2_results.py')


def parse_args(argv=None):
    """Parse the public runner options and validate explicit replay paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--execute-ai', action='store_true',
        help='Allow new AI requests under the shared $2 safeguard',
    )
    parser.add_argument(
        '--output-dir', type=Path,
        default=ROOT / 'audit_output' / ('test_all_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')),
    )
    parser.add_argument(
        '--ai-cache-dir', type=Path,
        default=ROOT / 'audit_output/all_hospitals_ai_2usd/ai',
    )
    parser.add_argument('--budget-usd', type=float, default=2)
    parser.add_argument('--baseline-only', action='store_true',
                        help='Skip conditional improvements and leave root submission unchanged')
    parser.add_argument(
        '--json-only', action='store_true',
        help='Skip CSV exporter, retaining all-record JSON',
    )
    parser.add_argument(
        '--unit-clarifications', type=Path,
        help='Source-cited unit clarification manifest; cannot override contradictory source wording',
    )
    replay = parser.add_mutually_exclusive_group()
    replay.add_argument(
        '--agent-replay-dir', type=Path,
        help='Reuse unchanged task answers from a verified prior run, with source/cache checks',
    )
    replay.add_argument(
        '--no-agent-replay', action='store_true',
        help='Disable prior task reuse, for intentionally changed contracts or a fresh checkout',
    )
    parser.add_argument('--compare', action='store_true', help='Compare the latest completed H1 predictions with labels; no audit')
    parser.add_argument('--predictions', type=Path, help='Optional results JSON or H1 CSV for --compare')
    parser.add_argument('--verify', type=Path, help='Verify an existing output directory; no audit or AI calls')
    parser.add_argument('--baseline', type=Path, help='Optional previous result directory for --verify')
    args = parser.parse_args(argv)
    if args.compare and args.verify:
        parser.error('--compare and --verify are separate actions')
    if args.predictions and not args.compare:
        parser.error('--predictions requires --compare')
    if args.baseline and not args.verify:
        parser.error('--baseline requires --verify')
    if args.agent_replay_dir and not args.agent_replay_dir.is_dir():
        parser.error('--agent-replay-dir must name an existing prior result directory')
    return args


def resolve_replay_dir(args):
    """Keep the verified prior run as the default when it is available."""
    if args.no_agent_replay:
        return None
    if args.agent_replay_dir is not None:
        return args.agent_replay_dir
    prior_default = ROOT / 'audit_output/four_blockers_test_all_verified'
    return prior_default if prior_default.is_dir() else None


def build_audit_command(args, replay_dir):
    """Forward runner options with the existing audit policy and offline default."""
    command = [
        sys.executable, str(ROOT / 'pipeline.py'),
        '--invoice-ai', '--four-blocker-fixes', '--h2-service-date-as-billing-day',
        '--budget-usd', str(args.budget_usd),
        '--ai-cache-dir', str(args.ai_cache_dir.resolve()),
        '--output-dir', str(args.output_dir.resolve()),
    ]
    if not args.execute_ai:
        command.append('--offline-ai')
    if args.unit_clarifications:
        command.extend(['--unit-clarifications', str(args.unit_clarifications.resolve())])
    if replay_dir:
        command.extend(['--agent-replay-dir', str(replay_dir.resolve())])
    return command


def source_hashes():
    """Record both entrypoints and the policy sources used for this run."""
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }


def run_context(args, replay_dir):
    return {
        'policy_revision': 'four_blockers_v2',
        'offline_ai': not args.execute_ai,
        'shared_ledger': str(args.ai_cache_dir.resolve()),
        'agent_replay_dir': str(replay_dir.resolve()) if replay_dir else None,
        'source_hashes': source_hashes(),
    }


def main(argv=None):
    args = parse_args(argv)
    if args.compare:
        compare_main(['--predictions', str(args.predictions)] if args.predictions else [])
        return
    if args.verify:
        command = [str(args.verify)]
        if args.baseline:
            command += ['--baseline', str(args.baseline)]
        verify_main(command)
        return
    replay_dir = resolve_replay_dir(args)
    subprocess.run(
        [sys.executable, str(ROOT / 'test.py'), '-q'],
        cwd=ROOT, check=True,
    )
    subprocess.run(build_audit_command(args, replay_dir), cwd=ROOT, check=True)
    rows, remaining = export_results(args.output_dir)
    save(args.output_dir / 'test_all_context.json', run_context(args, replay_dir))
    if not args.baseline_only:
        import complete_workflow
        final = complete_workflow.run(args.output_dir)
        rows, remaining = export_results(final)
        save(final / 'test_all_context.json', run_context(args, replay_dir))
        if not args.json_only:
            complete_workflow.publish(final, ROOT / 'submission.csv')
        report_results(final, rows, remaining)
        return
    if not args.json_only:
        portable_csv_export(args.output_dir)
    report_results(args.output_dir, rows, remaining)


if __name__ == '__main__':
    main()
