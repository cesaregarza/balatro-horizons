"""Evidence command family over package-owned orchestration."""

import subprocess


def run_evidence_command(args):
    operation = args.evidence_operation
    if operation == "plan":
        from balatro_horizons.evidence.stages import plan

        return plan(
            gameplay_only=args.gameplay_only,
            connection_only=args.connection_only,
            continuation_only=args.continuation_only,
            interruption_only=args.interruption_only,
            session_expiry_only=args.session_expiry_only,
            resume_actions=getattr(args, "resume_actions", False),
            resume_certification=getattr(args, "resume_certification", False),
        )
    if operation == "collect":
        if args.session_expiry_only:
            return collect_session_expiry(args)
        if args.fixture_report is not None:
            raise ValueError("FIXTURE_REPORT_REQUIRES_SESSION_EXPIRY_ONLY")
        if args.interruption_only:
            return collect_interruption(args)
        if args.continuation_only:
            return collect_continuation(args)
        if args.report is not None:
            raise ValueError("REPORT_REQUIRES_CONTINUATION_ONLY")
        from balatro_horizons.evidence.collect.orchestrator import collect

        return collect(
            from_stage=args.from_stage,
            gameplay_only=args.gameplay_only,
            action_episode_id=args.episode_id,
        )
    if operation == "certify":
        from balatro_horizons.evidence.release import certify_release

        return certify_release()
    if operation == "publish":
        from balatro_horizons.evidence.publish import publish

        return publish()
    if operation == "reuse":
        return reuse_evidence(args)
    from balatro_horizons.evidence.inspect import inspect_artifact

    return inspect_artifact(args.artifact, args.limit)


def collect_session_expiry(args):
    from balatro_horizons.config import ROOT, load_config
    from balatro_horizons.evidence.collect.session_expiry import collect

    if (args.report is None or args.fixture_report is None or args.from_stage or args.episode_id
            or args.gameplay_only or args.continuation_only or args.interruption_only):
        raise ValueError("SESSION_EXPIRY_REQUIRES_TWO_REPORTS_AND_NO_OTHER_MODE")
    result = collect(load_config(ROOT / "configs/smoke.yaml"), args.report, args.fixture_report)
    if result["status"] != "passed":
        raise ValueError(result["reason"])
    return result


def collect_continuation(args):
    from balatro_horizons.config import ROOT, load_config
    from balatro_horizons.evidence.collect.continuation import collect

    if args.report is None or args.from_stage or args.episode_id or args.gameplay_only:
        raise ValueError("CONTINUATION_REQUIRES_REPORT_AND_NO_RESUME_MODE")
    result = collect(load_config(ROOT / "configs/smoke.yaml"), args.report)
    if result["status"] != "passed":
        raise ValueError(result["reason"])
    return result


def collect_interruption(args):
    from balatro_horizons.config import ROOT, load_config
    from balatro_horizons.evidence.collect.interruption import collect

    if args.report is None or args.from_stage or args.episode_id or args.gameplay_only or args.continuation_only:
        raise ValueError("INTERRUPTION_REQUIRES_REPORT_AND_NO_RESUME_MODE")
    result = collect(load_config(ROOT / "configs/smoke.yaml"), args.report)
    if result["status"] != "passed":
        raise ValueError(result["reason"])
    return result


def reuse_evidence(args):
    from balatro_horizons.evidence.reuse import activate, prepare

    try:
        certificate, evidence = prepare(
            args.root,
            args.candidate,
            args.baseline,
            args.offline_report,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("EVIDENCE_REUSE_GIT_FAILED") from error
    if args.apply:
        activate(args.root, args.candidate, certificate, evidence)
    return {
        "compatible": True,
        "activated": args.apply,
        "accepted_implementation_hash": certificate["accepted_implementation_hash"],
        "native_implementation_hash": certificate["native_implementation_hash"],
        "reused_certificate": certificate["reuse"]["parent_certificate_id"],
        "native_launches": 0,
        "checkpoint_certificates_migrated": False,
    }
