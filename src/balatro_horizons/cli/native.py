"""Native runtime command."""


def run_native_command(args):
    from balatro_horizons.config import load_config
    from balatro_horizons.game.session import WindowsBridge

    bridge = WindowsBridge(load_config().environment)
    if args.operation == "stop":
        bridge.stop()
        return {"stopped": True}
    state = bridge.launch() if args.operation == "launch" else bridge.rpc("bh_inspect")
    return {
        "phase": state["state"],
        "identity": state["bh"]["identity"],
        "ready": state["bh"]["ready"],
        "busy": state["bh"]["busy"],
    }
