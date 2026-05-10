from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Funding Spread Monitor")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the web server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    subparsers.add_parser("compact-db", help="Prune stored snapshots and shrink the database")

    args = parser.parse_args()
    command = args.command or "serve"
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)

    if command == "compact-db":
        from app.config import Settings
        from app.storage import SQLiteRepository

        settings = Settings.from_env()
        repository = SQLiteRepository(settings.database_path)
        repository.initialize()
        result = repository.compact_storage(
            snapshot_retention_per_exchange_ticker=settings.snapshot_retention_per_exchange_ticker,
            collector_run_retention_per_task=settings.collector_run_retention_per_task,
        )
        repository.close()
        print(result)
        return

    import uvicorn
    from app.main import app

    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
