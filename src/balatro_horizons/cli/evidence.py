"""Evidence command family over package-owned orchestration."""

import subprocess


def run_evidence_command(args):
    operation = args.evidence_operation
    if operation == "plan":
        from balatro_horizons.evidence.stages import plan

        return plan(
            gameplay_only=args.gameplay_only,
            resume_actions=getattr(args, "resume_actions", False),
            resume_certification=getattr(args, "resume_certification", False),
        )
    if operation == "collect":
        from balatro_horizons.evidence.collect.orchestrator import collect

        return collect(
            from_stage=args.from_stage,
            gameplay_only=args.gameplay_only,
            action_episode_id=args.episode_id,
        )
    if operation == "certify":
        from balatro_horizons.evidence.certification import certify_release

        return certify_release()
    if operation == "publish":
        from balatro_horizons.evidence.publish import publish

        return publish()
    if operation == "reuse":
        return reuse_evidence(args)
    from balatro_horizons.evidence.inspect import inspect_artifact

    return inspect_artifact(args.artifact, args.limit)


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
