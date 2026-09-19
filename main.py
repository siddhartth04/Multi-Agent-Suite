"""Entry point that prints how to run the application.

Each module is a separate service, so there is no single process that runs the
whole application -- this just points at the ways to start it.
"""

from gateway.registry import REGISTRY, dependency_edges


def main() -> None:
    print("Executive Intelligence (System Under Test)\n")

    print("Modules:")
    for module in REGISTRY:
        kind = "independent" if module.independent else f"depends on {', '.join(module.depends_on)}"
        print(f"  :{module.default_port}  {module.module_id:14s} {' -> '.join(module.agents):45s} ({kind})")

    print("\nCross-module dependencies:")
    for edge in dependency_edges():
        print(f"  {edge['from']} --{edge['transport']}--> {edge['to']}")

    print("\nRun each module in its own terminal:")
    for module in REGISTRY:
        print(
            f"  uvicorn modules.{module.module_id}.server:app "
            f"--host 0.0.0.0 --port {module.default_port}"
        )
    print("  uvicorn gateway.app:app --host 0.0.0.0 --port 8000")

    print("\nOr:  docker compose up --build")
    print("See README.md for the endpoint contract and telemetry.")


if __name__ == "__main__":
    main()
