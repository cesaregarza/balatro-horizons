"""Dashboard server command."""


def run_review_command(args):
    import uvicorn

    from balatro_horizons.api.app import create_app

    uvicorn.run(
        create_app(
            args.data_dir,
            public_origin=args.public_origin,
            bind_host=args.host,
            workbench_enabled=args.workbench,
        ),
        host=args.host,
        port=args.port,
    )
    return None
