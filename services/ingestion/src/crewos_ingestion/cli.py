"""
Command-line entry point for the ingestion service.
"""

import argparse
import uvicorn

def main():
    parser = argparse.ArgumentParser(description="Run the CrewOs Ingestion service")
    parser.add_argument(
        "--env-file",
        default="services/ingestion/.env.local",
        help="Path to the local .env file"
    )
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    uvicorn.run(
        "crewos_ingestion.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=args.port,
        env_file=args.env_file,
    )