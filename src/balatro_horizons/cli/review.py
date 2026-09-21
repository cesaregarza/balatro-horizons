"""Dashboard server command."""


def run_review_command(args):
    import uvicorn

    from balatro_horizons.api.app import create_app

    uvicorn.run(
        create_app(
            args.data_dir,
            public_origin=args.public_origin,
            bind_host=args.host,
            allow_remote=args.allow_remote,
            # The review server is the explicit dashboard entry point. The
            # lower-level factory and ordinary Config remain default-off.
            workbench_enabled=True,
        ),
        host=args.host,
        port=args.port,
    )
    return None
