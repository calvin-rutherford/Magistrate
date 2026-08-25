import click
import subprocess
import os

@click.group()
def cli():
    """Magistrate (Agent Top) CLI tool for managing AI Agents."""
    pass

@cli.command()
def start():
    """Start the Magistrate backend services (Docker compose & Django)."""
    click.echo("Starting Magistrate Backend Infrastructure...")
    subprocess.run(["docker-compose", "up", "-d"], cwd=os.path.join(os.path.dirname(__file__), '..'))
    click.echo("Services started. Make sure to run Django migrations if you haven't yet.")
    # Here we would normally start daphne and celery worker processes
    click.echo("Backend is up!")

@cli.command()
def top():
    """Launch the Magistrate Terminal UI."""
    from .repl import main
    main()

@cli.command()
@click.argument('task')
def spawn(task):
    """Spawn a new Agent with a specific task."""
    click.echo(f"Spawning agent for task: {task}")
    # Here we would normally hit the Django REST API to spawn the agent
    # e.g., requests.post('http://localhost:8000/api/agents/spawn', json={'task': task})

if __name__ == '__main__':
    cli()
