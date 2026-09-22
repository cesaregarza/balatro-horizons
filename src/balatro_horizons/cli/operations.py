"""Register operational commands without changing their output or exit semantics."""

from . import (
    credentials,
    deploy_frontend,
    diagnose_native,
    install_candidate,
    offline,
    package_balatro_guide,
    prompt_sync,
    register_player,
    smoke,
    workbench_session,
    workbench_status,
)


def add_operational_commands(sub):
    credentials.configure_parser(sub.add_parser("credentials", help="Configure private backend credentials"))
    offline.configure_parser(sub.add_parser("offline", help="Run offline checks"))
    smoke.configure_parser(sub.add_parser("smoke", help="Preflight or run a capped smoke"))
    deploy = sub.add_parser("deploy", help="Install a candidate or publish a frontend")
    targets = deploy.add_subparsers(dest="deploy_operation", required=True)
    deploy_frontend.configure_parser(targets.add_parser("frontend"))
    install_candidate.configure_parser(targets.add_parser("candidate"))
    guide = sub.add_parser("guide", help="Validate and package the Balatro guide")
    package_balatro_guide.configure_parser(
        guide.add_subparsers(dest="guide_operation", required=True).add_parser("package")
    )
    prompt = sub.add_parser("prompt", help="Check or sync persistent prompt instructions")
    prompt_sync.configure_parser(
        prompt.add_subparsers(dest="prompt_operation", required=True).add_parser("sync")
    )
    register_player.configure_parser(
        sub.choices["human"].add_subparsers(dest="human_operation").add_parser("register")
    )
    review = sub.choices["review"].add_subparsers(dest="review_operation")
    workbench_session.configure_parser(review.add_parser("session"))
    workbench_status.configure_parser(review.add_parser("status"))
    native = sub.choices["native"].add_subparsers(dest="operation", required=True)
    for name in ("launch", "stop", "status"):
        native.add_parser(name)
    diagnose_native.configure_parser(native.add_parser("diagnose"))
