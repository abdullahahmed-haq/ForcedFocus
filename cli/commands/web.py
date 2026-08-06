from cli.output import out

def cmd_web(args):
    """Start or stop the web interface."""
    action = args.action
    if action == "start":
        out.print_data(
            {
                "status": "ok",
                "message": "The dashboard is served by the ForcedFocus daemon at http://127.0.0.1:7070.",
            },
            title="Web UI",
        )
    elif action == "stop":
        out.print_data(
            {
                "status": "ok",
                "message": "The dashboard is part of the daemon and is not stopped separately.",
            },
            title="Web UI",
        )
